"""What a person's month holds and what still stands between it and its commit.

Read-only: the month-close page shows this; `monthclose` is what changes anything.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finanze import monthclose
from finanze.extractors.common import month_end, months_between
from finanze.storage import store
from finanze.storage.money import from_nano
from finanze.validation import ReconciliationError

# Inside the classes below `date` may name a field: annotations need another name for the type.
_Date = date


@dataclass(frozen=True, slots=True)
class MonthLine:
    """One month of one person, as the list of months shows it."""

    owner: str
    month: date
    committed: bool
    statements: int  # files that cover the month
    transactions: int  # booked in the month
    uncategorized: int


@dataclass(frozen=True, slots=True)
class File:
    """A statement that covers the month."""

    id: int
    source: str
    filename: str
    first: date
    last: date
    checked: bool  # False: the statement prints nothing to reconcile against
    transactions: int  # of this file, booked in the month


@dataclass(frozen=True, slots=True)
class Statement:
    """One stored statement file: where rows say they came from."""

    id: int
    owner: str
    source: str
    filename: str
    sha256: str  # of the file, which is how its original is kept
    first: date
    last: date
    checked: bool
    imported_at: str
    transactions: int  # every row that came from it

    @property
    def months(self) -> tuple[date, ...]:
        """The first day of every month it covers."""
        return tuple(months_between(self.first, self.last))


@dataclass(frozen=True, slots=True)
class Pending:
    """A row that still needs a category."""

    id: str
    date: date
    account: str
    amount: Decimal
    currency: str
    counterparty: str
    description: str


@dataclass(frozen=True, slots=True)
class Group:
    """Waiting rows of one counterparty going one way: they get their category together."""

    name: str  # the counterparty, or the description without one; "" when the rows have no text
    incoming: bool
    rows: tuple[Pending, ...]


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    side: str  # "uscite", "entrate" or "transfer"
    category: str
    currency: str
    count: int
    total: Decimal


@dataclass(frozen=True, slots=True)
class Checklist:
    """One person's month, in the order things block its commit."""

    owner: str
    month: date
    committed: bool
    committed_at: str | None
    files: tuple[File, ...]
    # Sources with a statement for the month before and none for this one. Never a pension
    # fund: its certificates come when they come, and none is expected in any month.
    missing: tuple[str, ...]
    # What only another statement can settle, anywhere in the person's history.
    gaps: tuple[str, ...]
    uncategorized: tuple[Pending, ...]
    totals: tuple[CategoryTotal, ...]  # by booking date, as the month is closed

    @property
    def groups(self) -> tuple[Group, ...]:
        """The waiting rows as `--review` asks about them: per counterparty and direction."""
        found: dict[tuple[str, bool], list[Pending]] = {}
        for row in self.uncategorized:
            name = (row.counterparty or row.description).strip()
            found.setdefault((name, row.amount > 0), []).append(row)
        return tuple(Group(name, incoming, tuple(rows)) for (name, incoming), rows in found.items())

    @property
    def blocked_by(self) -> tuple[str, ...]:
        """Why the month cannot be committed yet; empty when it can."""
        reasons = list(self.gaps)
        if self.uncategorized:
            count = len(self.uncategorized)
            rows = "1 transaction still needs" if count == 1 else f"{count} transactions still need"
            reasons.append(f"{rows} a category")
        return tuple(reasons)


def listing(conn: sqlite3.Connection, owner: str | None = None) -> tuple[MonthLine, ...]:
    """Every staged or committed month of `owner` (of everyone without one), newest first."""
    booked = (
        "(SELECT count(*) FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE a.owner_id = m.owner_id "
        "AND t.booking_date BETWEEN m.month AND date(m.month, '+1 month', '-1 day')"
    )
    found = conn.execute(
        "SELECT o.name, m.month, m.status, "  # noqa: S608  # only the fixed text above is interpolated
        "(SELECT count(*) FROM statement_files f WHERE f.owner_id = m.owner_id "
        "AND f.period_start <= date(m.month, '+1 month', '-1 day') AND f.period_end >= m.month), "
        f"{booked}), {booked} AND {store.WAITING}) "
        "FROM months m JOIN owners o ON o.id = m.owner_id "
        "WHERE o.name = coalesce(?, o.name) ORDER BY m.month DESC, o.name",
        (owner,),
    ).fetchall()
    return tuple(
        MonthLine(r[0], date.fromisoformat(r[1]), r[2] == "committed", r[3], r[4], r[5])
        for r in found
    )


def checklist(conn: sqlite3.Connection, owner: str, month: date) -> Checklist:
    """The month of `owner` that starts on `month`. Raises `NothingStagedError` without one."""
    owner_id = store.owner_id(conn, owner)
    row = conn.execute(
        "SELECT status, committed_at FROM months WHERE owner_id = ? AND month = ?",
        (owner_id, month.isoformat()),
    ).fetchone()
    if owner_id is None or row is None:
        msg = f"nothing is staged for {owner} in {month:%Y-%m}"
        raise monthclose.NothingStagedError(msg)
    span = (month.isoformat(), month_end(month).isoformat())
    files = conn.execute(
        "SELECT f.id, f.source, f.filename, f.period_start, f.period_end, f.reconciled, "
        "(SELECT count(*) FROM transactions t WHERE t.statement_file_id = f.id "
        "AND t.booking_date BETWEEN ?1 AND ?2) "
        "FROM statement_files f WHERE f.owner_id = ?3 AND f.period_start <= ?2 "
        "AND f.period_end >= ?1 ORDER BY f.source, f.filename",
        (*span, owner_id),
    ).fetchall()
    before = conn.execute(
        "SELECT DISTINCT source FROM statement_files WHERE owner_id = ?1 "
        "AND period_start < ?2 AND period_end >= date(?2, '-1 month') "
        # what feeds an account carried flat (a pension fund's certificates) is not monthly
        "AND source NOT IN (SELECT source FROM accounts WHERE owner_id = ?1 "
        "AND kind = 'manual' AND source IS NOT NULL) ORDER BY source",
        (owner_id, span[0]),
    ).fetchall()
    present = {f[1] for f in files}
    pending = conn.execute(
        "SELECT t.id, t.date, a.name, t.amount_nano, t.currency, t.counterparty, t.description "  # noqa: S608  # only fixed text of `store` is interpolated
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE a.owner_id = ? AND t.booking_date BETWEEN ? AND ? "
        f"AND {store.WAITING} ORDER BY t.date, t.id",
        (owner_id, *span),
    ).fetchall()
    totals = conn.execute(
        "SELECT c.side, c.name, t.currency, count(*), sum(t.amount_nano) "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "JOIN categories c ON c.id = t.category_id "
        "WHERE a.owner_id = ? AND t.booking_date BETWEEN ? AND ? "
        "GROUP BY c.id, t.currency ORDER BY c.sort_order, c.id, t.currency",
        (owner_id, *span),
    ).fetchall()
    return Checklist(
        owner=owner,
        month=month,
        committed=row["status"] == "committed",
        committed_at=row["committed_at"],
        files=tuple(
            File(f[0], f[1], f[2], _day(f[3]), _day(f[4]), checked=f[5] == "yes", transactions=f[6])
            for f in files
        ),
        missing=tuple(r[0] for r in before if r[0] not in present),
        gaps=_gaps(conn, owner_id),
        uncategorized=tuple(
            Pending(r[0], _day(r[1]), r[2], from_nano(r[3]), r[4], r[5], r[6]) for r in pending
        ),
        totals=tuple(CategoryTotal(r[0], r[1], r[2], r[3], from_nano(r[4])) for r in totals),
    )


def statement(conn: sqlite3.Connection, number: int) -> Statement | None:
    """The stored statement file with this id; None when there is none."""
    found = conn.execute(
        "SELECT f.id, o.name, f.source, f.filename, f.sha256, f.period_start, f.period_end, "
        "f.reconciled, f.imported_at, "
        "(SELECT count(*) FROM transactions t WHERE t.statement_file_id = f.id) "
        "FROM statement_files f JOIN owners o ON o.id = f.owner_id WHERE f.id = ?",
        (number,),
    ).fetchone()
    if found is None:
        return None
    return Statement(
        id=found[0],
        owner=found[1],
        source=found[2],
        filename=found[3],
        sha256=found[4],
        first=_day(found[5]),
        last=_day(found[6]),
        checked=found[7] == "yes",
        imported_at=found[8],
        transactions=found[9],
    )


def _day(text: str) -> date:
    return date.fromisoformat(text)


def _gaps(conn: sqlite3.Connection, owner_id: int) -> tuple[str, ...]:
    """What the chain of balances and the bills are missing, as sentences. Nothing is settled or
    hidden here: the commit runs the same checks and refuses."""
    try:
        return tuple(str(gap) for gap in monthclose.would_refuse(conn, owner_id))
    except ReconciliationError as exc:  # two statements disagree: shown, and the commit raises it
        return (str(exc),)
