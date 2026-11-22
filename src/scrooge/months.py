"""What is staged for a person and waits for its commit, how far each account is committed,
and what the committed months hold.

Read-only: the import page and the months pages show this; `staging` is what changes
anything. There is no month to close (decided 2026-10-16): a month is only a way to look at
what is committed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from scrooge import router, staging
from scrooge.extractors.common import month_end, months_between
from scrooge.storage import store
from scrooge.storage.money import from_nano
from scrooge.validation import ReconciliationError

# Inside the classes below `date` may name a field: annotations need another name for the type.
_Date = date


@dataclass(frozen=True, slots=True)
class MonthLine:
    """One month of one person, as the list of months shows it: what is committed of it."""

    owner: str
    month: date
    statements: int  # committed files that cover the month
    transactions: int  # committed, booked in the month


@dataclass(frozen=True, slots=True)
class Span:
    """Days one account has statements for, committed or staged."""

    first: date
    last: date
    committed: bool


@dataclass(frozen=True, slots=True)
class Track:
    """The days an account has statements for: runs of days, staged ones first."""

    owner: str
    account: str
    source: str
    spans: tuple[Span, ...]

    @property
    def label(self) -> str:
        return router.LABELS.get(self.source, self.source)


@dataclass(frozen=True, slots=True)
class File:
    """A statement file: one that covers a month, or one that is staged."""

    id: int
    source: str
    filename: str
    first: date
    last: date
    checked: bool  # False: the statement prints nothing to reconcile against
    transactions: int  # of this file (booked in the month, for a month)
    committed: bool = True

    @property
    def label(self) -> str:
        return router.LABELS.get(self.source, self.source)


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
    committed_at: str | None = None  # None: staged
    # Why it cannot be taken back now: committed statements of its accounts on both sides.
    held_by: tuple[str, ...] = ()

    @property
    def months(self) -> tuple[date, ...]:
        """The first day of every month it covers."""
        return tuple(months_between(self.first, self.last))

    @property
    def label(self) -> str:
        return router.LABELS.get(self.source, self.source)


@dataclass(frozen=True, slots=True)
class Pending:
    """A row to give a category to, or to change the category of."""

    id: str
    date: date
    account: str
    amount: Decimal
    currency: str
    counterparty: str
    description: str
    category: str = ""  # of a row that has one
    side: str = ""  # of that category: "uscite", "entrate" or "transfer"
    changeable: bool = False  # money in or out whose category a rule or a choice gave
    statement: int | None = None  # the file it came from

    @property
    def waiting(self) -> bool:
        return not self.category


@dataclass(frozen=True, slots=True)
class Group:
    """Rows of one counterparty going one way: they get their category together."""

    name: str  # the counterparty, or the description without one; "" when the rows have no text
    incoming: bool
    rows: tuple[Pending, ...]
    category: str = ""  # what the rows have now, when they have one


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    side: str  # "uscite", "entrate" or "transfer"; "" for the rows waiting for a category
    category: str
    currency: str
    count: int
    total: Decimal


@dataclass(frozen=True, slots=True)
class Draft:
    """What is staged for one person: what a commit would make visible."""

    owner: str
    files: tuple[File, ...]
    rows: tuple[Pending, ...]  # every staged row but those a statement books on itself alone
    totals: tuple[CategoryTotal, ...]  # by category and currency, the waiting rows apart
    # What only another statement can settle, anywhere in the person's history.
    gaps: tuple[str, ...]

    @property
    def uncategorized(self) -> tuple[Pending, ...]:
        return tuple(row for row in self.rows if row.waiting)

    @property
    def groups(self) -> tuple[Group, ...]:
        """The waiting rows as `--review` asks about them: per counterparty and direction."""
        return _grouped(self.uncategorized)

    @property
    def group_of(self) -> dict[str, int]:
        """Each waiting row's group, by its place among `groups`."""
        return {row.id: number for number, group in enumerate(self.groups) for row in group.rows}

    @property
    def blocked_by(self) -> tuple[str, ...]:
        """Why it cannot be committed yet; empty when it can."""
        reasons = list(self.gaps)
        if self.uncategorized:
            count = len(self.uncategorized)
            rows = "1 transaction still needs" if count == 1 else f"{count} transactions still need"
            reasons.append(f"{rows} a category")
        return tuple(reasons)


@dataclass(frozen=True, slots=True)
class Coverage:
    """How far one account of one person is committed, and staged, by one source."""

    owner: str
    account: str
    source: str
    committed: date | None  # the last day committed; None: nothing is
    staged: date | None  # the last day staged beyond it; None: nothing is

    @property
    def label(self) -> str:
        return router.LABELS.get(self.source, self.source)


@dataclass(frozen=True, slots=True)
class Checklist:
    """What is committed of one person's month."""

    owner: str
    month: date
    files: tuple[File, ...]
    totals: tuple[CategoryTotal, ...]  # by booking date
    # Money in or out of the month whose category can be changed.
    categorized: tuple[Pending, ...] = ()

    @property
    def changeable(self) -> tuple[Group, ...]:
        """The rows that have a category, per counterparty, direction and category."""
        return _grouped(self.categorized)


# A row of `transactions t` joined with its account `a`, category `c` and file `f`, as a Pending.
_PENDING = (
    "SELECT t.id, t.date, a.name, t.amount_nano, t.currency, t.counterparty, t.description, "  # noqa: S608  # only fixed text of `store` is interpolated
    f"coalesce(c.name, ''), coalesce(c.side, ''), {store.CHANGEABLE}, t.statement_file_id "
    "FROM transactions t JOIN accounts a ON a.id = t.account_id "
    "LEFT JOIN categories c ON c.id = t.category_id "
    "LEFT JOIN statement_files f ON f.id = t.statement_file_id "
)


def _pending(row: sqlite3.Row) -> Pending:
    return Pending(
        row[0],
        _day(row[1]),
        row[2],
        from_nano(row[3]),
        row[4],
        row[5],
        row[6],
        row[7],
        row[8],
        bool(row[9]),
        row[10],
    )


def draft(conn: sqlite3.Connection, owner: str) -> Draft:
    """What is staged for `owner`, nothing when nothing is."""
    owner_id = store.owner_id(conn, owner)
    if owner_id is None:
        return Draft(owner, (), (), (), ())
    files = conn.execute(
        "SELECT f.id, f.source, f.filename, f.period_start, f.period_end, f.reconciled, "
        "(SELECT count(*) FROM transactions t WHERE t.statement_file_id = f.id) "
        "FROM statement_files f WHERE f.owner_id = ? AND f.committed_at IS NULL "
        "ORDER BY f.period_start, f.source, f.filename",
        (owner_id,),
    ).fetchall()
    rows = conn.execute(
        _PENDING  # only fixed text is interpolated
        + "WHERE a.owner_id = ? AND f.committed_at IS NULL AND t.statement_file_id IS NOT NULL "
        "AND t.kind <> 'internal' ORDER BY t.date, t.id",
        (owner_id,),
    ).fetchall()
    pending = tuple(_pending(row) for row in rows)
    return Draft(
        owner=owner,
        files=tuple(
            File(f[0], f[1], f[2], _day(f[3]), _day(f[4]), f[5] == "yes", f[6], committed=False)
            for f in files
        ),
        rows=pending,
        totals=_totals(conn, pending),
        gaps=_gaps(conn, owner_id),
    )


def _totals(conn: sqlite3.Connection, rows: tuple[Pending, ...]) -> tuple[CategoryTotal, ...]:
    """Per category and currency in the order of the taxonomy, the waiting rows last."""
    order = {
        (r[0], r[1]): n
        for n, r in enumerate(
            conn.execute("SELECT side, name FROM categories ORDER BY sort_order, id")
        )
    }
    sums: dict[tuple[str, str, str], tuple[int, Decimal]] = {}
    for row in rows:
        key = (row.side, row.category, row.currency)
        count, total = sums.get(key, (0, Decimal(0)))
        sums[key] = (count + 1, total + row.amount)
    ranked = sorted(sums, key=lambda k: (not k[1], order.get((k[0], k[1]), len(order)), k[2]))
    return tuple(
        CategoryTotal(side, name, cur, *sums[side, name, cur]) for side, name, cur in ranked
    )


def coverage(conn: sqlite3.Connection, owner: str | None = None) -> tuple[Coverage, ...]:
    """Every account a source feeds, of `owner` or of everyone: the last day committed and the
    last day staged."""
    found = conn.execute(
        "SELECT o.name, a.name, f.source, "
        "max(f.period_end) FILTER (WHERE f.committed_at IS NOT NULL), "
        "max(f.period_end) FILTER (WHERE f.committed_at IS NULL) "
        "FROM statement_accounts s JOIN statement_files f ON f.id = s.statement_file_id "
        "JOIN accounts a ON a.id = s.account_id JOIN owners o ON o.id = a.owner_id "
        "WHERE o.name = coalesce(?, o.name) "
        "GROUP BY o.name, a.name, f.source ORDER BY o.name, a.name, f.source",
        (owner,),
    ).fetchall()
    lines: list[Coverage] = []
    for name, account, source, committed, staged in found:
        last = committed and _day(committed)
        ahead = staged and _day(staged)
        lines.append(
            Coverage(
                name, account, source, last, ahead if ahead and (not last or ahead > last) else None
            )
        )
    return tuple(lines)


def _joined(spans: list[tuple[date, date]], committed: bool) -> list[Span]:
    """`spans` as runs: statements that overlap or follow on day after day are one."""
    runs: list[Span] = []
    for first, last in sorted(spans):
        if runs and first <= runs[-1].last + timedelta(days=1):
            runs[-1] = Span(runs[-1].first, max(runs[-1].last, last), committed)
        else:
            runs.append(Span(first, last, committed))
    return runs


def timeline(conn: sqlite3.Connection, owner: str | None = None) -> tuple[Track, ...]:
    """Of every account a source feeds, of `owner` or of everyone, the days its statements
    cover: staged ones before committed ones, so that drawn in that order a day committed
    hides the same day staged."""
    found = conn.execute(
        "SELECT o.name, a.name, f.source, f.period_start, f.period_end, "
        "f.committed_at IS NOT NULL "
        "FROM statement_accounts s JOIN statement_files f ON f.id = s.statement_file_id "
        "JOIN accounts a ON a.id = s.account_id JOIN owners o ON o.id = a.owner_id "
        "WHERE o.name = coalesce(?, o.name) ORDER BY o.name, a.name, f.source",
        (owner,),
    ).fetchall()
    grouped: dict[tuple[str, str, str], dict[bool, list[tuple[date, date]]]] = {}
    for name, account, source, first, last, committed in found:
        states = grouped.setdefault((name, account, source), {False: [], True: []})
        states[bool(committed)].append((_day(first), _day(last)))
    return tuple(
        Track(name, account, source, (*_joined(states[False], False), *_joined(states[True], True)))
        for (name, account, source), states in grouped.items()
    )


def listing(conn: sqlite3.Connection, owner: str | None = None) -> tuple[MonthLine, ...]:
    """Every month that committed statements of `owner` (of everyone without one) cover,
    newest first."""
    files = conn.execute(
        "SELECT o.name, f.period_start, f.period_end FROM statement_files f "
        "JOIN owners o ON o.id = f.owner_id "
        "WHERE f.committed_at IS NOT NULL AND o.name = coalesce(?, o.name)",
        (owner,),
    ).fetchall()
    covered: dict[tuple[str, date], int] = {}
    for name, first, last in files:
        for month in months_between(_day(first), _day(last)):
            covered[name, month] = covered.get((name, month), 0) + 1
    booked = conn.execute(
        "SELECT owner, date(booking_date, 'start of month'), count(*) "
        "FROM committed_transactions WHERE owner = coalesce(?, owner) GROUP BY 1, 2",
        (owner,),
    ).fetchall()
    counts = {(r[0], _day(r[1])): int(r[2]) for r in booked}
    keys = sorted({*covered, *counts}, key=lambda k: (-k[1].toordinal(), k[0]))
    return tuple(
        MonthLine(name, month, covered.get((name, month), 0), counts.get((name, month), 0))
        for name, month in keys
    )


def checklist(conn: sqlite3.Connection, owner: str, month: date) -> Checklist:
    """What is committed of the month of `owner` that starts on `month`.

    Raises `NothingStagedError` for a person the database does not know.
    """
    owner_id = store.owner_id(conn, owner)
    if owner_id is None:
        msg = f"there is nobody called {owner}"
        raise staging.NothingStagedError(msg)
    span = (month.isoformat(), month_end(month).isoformat())
    committed = "(f.committed_at IS NOT NULL OR t.statement_file_id IS NULL)"
    files = conn.execute(
        "SELECT f.id, f.source, f.filename, f.period_start, f.period_end, f.reconciled, "
        "(SELECT count(*) FROM transactions t WHERE t.statement_file_id = f.id "
        "AND t.booking_date BETWEEN ?1 AND ?2) "
        "FROM statement_files f WHERE f.owner_id = ?3 AND f.period_start <= ?2 "
        "AND f.period_end >= ?1 AND f.committed_at IS NOT NULL ORDER BY f.source, f.filename",
        (*span, owner_id),
    ).fetchall()
    categorized = conn.execute(
        _PENDING  # only fixed text is interpolated
        + f"WHERE a.owner_id = ? AND t.booking_date BETWEEN ? AND ? AND {committed} "
        f"AND {store.CHANGEABLE} ORDER BY t.date, t.id",
        (owner_id, *span),
    ).fetchall()
    totals = conn.execute(
        "SELECT c.side, c.name, t.currency, count(*), sum(t.amount_nano) "  # noqa: S608  # only fixed text is interpolated
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "JOIN categories c ON c.id = t.category_id "
        "LEFT JOIN statement_files f ON f.id = t.statement_file_id "
        f"WHERE a.owner_id = ? AND t.booking_date BETWEEN ? AND ? AND {committed} "
        "GROUP BY c.id, t.currency ORDER BY c.sort_order, c.id, t.currency",
        (owner_id, *span),
    ).fetchall()
    return Checklist(
        owner=owner,
        month=month,
        files=tuple(
            File(f[0], f[1], f[2], _day(f[3]), _day(f[4]), checked=f[5] == "yes", transactions=f[6])
            for f in files
        ),
        totals=tuple(CategoryTotal(r[0], r[1], r[2], r[3], from_nano(r[4])) for r in totals),
        categorized=tuple(_pending(row) for row in categorized),
    )


def _grouped(rows: tuple[Pending, ...]) -> tuple[Group, ...]:
    found: dict[tuple[str, bool, str], list[Pending]] = {}
    for row in rows:
        name = (row.counterparty or row.description).strip()
        found.setdefault((name, row.amount > 0, row.category), []).append(row)
    return tuple(
        Group(name, incoming, tuple(rows), category)
        for (name, incoming, category), rows in found.items()
    )


def statement(conn: sqlite3.Connection, number: int) -> Statement | None:
    """The stored statement file with this id; None when there is none."""
    found = conn.execute(
        "SELECT f.id, o.name, f.source, f.filename, f.sha256, f.period_start, f.period_end, "
        "f.reconciled, f.imported_at, "
        "(SELECT count(*) FROM transactions t WHERE t.statement_file_id = f.id), f.committed_at "
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
        committed_at=found[10],
        held_by=tuple(store.later_committed(conn, number)) if found[10] else (),
    )


def _day(text: str) -> date:
    return date.fromisoformat(text)


def _gaps(conn: sqlite3.Connection, owner_id: int) -> tuple[str, ...]:
    """What the chain of balances and the bills are missing, as sentences. Nothing is settled or
    hidden here: the commit runs the same checks and refuses."""
    try:
        return tuple(str(gap) for gap in staging.would_refuse(conn, owner_id))
    except ReconciliationError as exc:  # two statements disagree: shown, and the commit raises it
        return (str(exc),)
