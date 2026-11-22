"""Find stored transactions: the one function behind `scrooge query` and the transactions page.

Only committed statements are read (the `committed_transactions` view). Amounts stay in their
own currency, so every total is per currency: nothing is converted here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge.extractors.common import month_end
from scrooge.models import Kind
from scrooge.storage.money import from_nano, to_nano

# Inside `Row`, `date` names the field: later annotations need another name for the type.
_Date = date


class QueryError(Exception):
    """A filter names an owner, an account or a category the database does not have."""


@dataclass(frozen=True, slots=True)
class Filter:
    """What to look for; every field left alone matches everything.

    `first` and `last` are both included and are compared with the real date, or with the
    booking date when `booking` is set. `category` is a name at either level of the taxonomy:
    a top-level category stands for all its leaves. `at_least` and `at_most` bound the size of
    the amount, whatever its sign. `text` is looked for in the counterparty and the
    description, ignoring case.
    """

    first: date | None = None
    last: date | None = None
    booking: bool = False
    owner: str | None = None
    account: str | None = None
    category: str | None = None
    kind: Kind | None = None
    at_least: Decimal | None = None
    at_most: Decimal | None = None
    text: str = ""


@dataclass(frozen=True, slots=True)
class Row:
    id: str
    owner: str
    account: str
    date: date
    booking_date: _Date
    amount: Decimal
    currency: str
    description: str
    counterparty: str
    kind: Kind
    category: str | None  # None only for rows of kind `internal`
    category_parent: str | None
    counterparty_owner: str | None
    file: int | None = None  # the statement file it came from; None for a row typed by hand


@dataclass(frozen=True, slots=True)
class Sum:
    """What the rows of one period and one currency add up to."""

    period: str  # "2025", "2025-05", or "" for the whole result
    currency: str
    count: int
    total: Decimal


@dataclass(frozen=True, slots=True)
class Result:
    rows: tuple[Row, ...]  # by the date the filter compares, oldest first
    totals: tuple[Sum, ...]  # one per currency
    by_year: tuple[Sum, ...]
    by_month: tuple[Sum, ...]


@dataclass(frozen=True, slots=True)
class Person:
    """One person and how much of their history the pages can show."""

    name: str
    committed: int  # statements
    first: date | None  # the first and last day committed statements cover; None: there is none
    last: date | None
    staged: int  # statements staged and not yet committed
    transactions: int  # of committed statements


def people(conn: sqlite3.Connection) -> tuple[Person, ...]:
    """Everyone in the database, by name."""
    found = conn.execute(
        "SELECT o.name, "
        "count(f.id) FILTER (WHERE f.committed_at IS NOT NULL) AS committed, "
        "min(f.period_start) FILTER (WHERE f.committed_at IS NOT NULL) AS first, "
        "max(f.period_end) FILTER (WHERE f.committed_at IS NOT NULL) AS last, "
        "count(f.id) FILTER (WHERE f.committed_at IS NULL) AS staged, "
        "(SELECT count(*) FROM committed_transactions t WHERE t.owner = o.name) AS transactions "
        "FROM owners o LEFT JOIN statement_files f ON f.owner_id = o.id "
        "GROUP BY o.id ORDER BY o.name"
    ).fetchall()
    return tuple(
        Person(
            name=r["name"],
            committed=r["committed"],
            first=r["first"] and date.fromisoformat(r["first"]),
            last=r["last"] and date.fromisoformat(r["last"]),
            staged=r["staged"],
            transactions=r["transactions"],
        )
        for r in found
    )


@dataclass(frozen=True, slots=True)
class Choices:
    """What the account and category filters can be set to."""

    accounts: tuple[str, ...]
    # A heading and the names under it: each top-level expense category followed by its
    # leaves, then income, then transfers.
    categories: tuple[tuple[str, tuple[str, ...]], ...]
    # The same without the top-level names: what a transaction can be given.
    leaves: tuple[tuple[str, tuple[str, ...]], ...]
    # The top-level categories of the expenses: what a new expense category can go under.
    groups: tuple[str, ...] = ()


def choices(conn: sqlite3.Connection, owner: str | None = None) -> Choices:
    """The accounts of `owner` (of everyone without one) and the whole taxonomy."""
    accounts = conn.execute(
        "SELECT DISTINCT a.name FROM accounts a JOIN owners o ON o.id = a.owner_id "
        "WHERE o.name = coalesce(?, o.name) ORDER BY a.name",
        (owner,),
    ).fetchall()
    found = conn.execute(
        "SELECT c.name, c.side, p.name AS parent FROM categories c "
        "LEFT JOIN categories p ON p.id = c.parent_id ORDER BY c.sort_order, c.id"
    ).fetchall()
    groups: dict[str, list[str]] = {}
    leaves: dict[str, list[str]] = {}
    for r in found:
        top = r["side"] == "uscite" and r["parent"] is None
        heading = r["parent"] or (r["name"] if top else r["side"].capitalize())
        names = groups.setdefault(heading, [])
        if r["name"] not in names:  # a leaf named like its parent is one choice
            names.append(r["name"])
        if not top:
            leaves.setdefault(heading, []).append(r["name"])
    return Choices(
        tuple(r["name"] for r in accounts),
        tuple((heading, tuple(names)) for heading, names in groups.items()),
        tuple((heading, tuple(names)) for heading, names in leaves.items()),
        tuple(r["name"] for r in found if r["side"] == "uscite" and r["parent"] is None),
    )


def span(text: str) -> tuple[date, date]:
    """The first and last day of `2025`, of `2025-05`, or `2025-05-17` twice.

    Raises ValueError for anything else.
    """
    if len(text) == 4:
        return date(int(text), 1, 1), date(int(text), 12, 31)
    if len(text) == 7:
        first = date.fromisoformat(f"{text}-01")
        return first, month_end(first)
    day = date.fromisoformat(text)
    return day, day


def run(conn: sqlite3.Connection, wanted: Filter) -> Result:
    """The committed transactions that match `wanted`, with what they add up to."""
    check_names(conn, wanted)
    column = "booking_date" if wanted.booking else "date"
    conditions: list[tuple[str, object]] = [
        (f"{column} >= ?", wanted.first and wanted.first.isoformat()),
        (f"{column} <= ?", wanted.last and wanted.last.isoformat()),
        ("owner = ?", wanted.owner),
        ("account = ?", wanted.account),
        ("? IN (category, category_parent)", wanted.category),
        ("kind = ?", wanted.kind and wanted.kind.value),
        ("abs(amount_nano) >= ?", None if wanted.at_least is None else to_nano(wanted.at_least)),
        ("abs(amount_nano) <= ?", None if wanted.at_most is None else to_nano(wanted.at_most)),
    ]
    used = [(sql, value) for sql, value in conditions if value is not None]
    where = " AND ".join(["1", *(sql for sql, _ in used)])
    found = conn.execute(
        "SELECT id, owner, account, date, booking_date, amount_nano, currency, description, "  # noqa: S608  # only the fixed conditions above are interpolated
        "counterparty, kind, category, category_parent, counterparty_owner, "
        "(SELECT t.statement_file_id FROM transactions t "
        "WHERE t.id = committed_transactions.id) AS file "
        f"FROM committed_transactions WHERE {where} ORDER BY {column}, id",
        [value for _, value in used],
    ).fetchall()
    # SQLite folds the case of ASCII letters only: the text is matched here instead.
    needle = wanted.text.casefold()
    rows = tuple(
        _row(r)
        for r in found
        if needle in r["counterparty"].casefold() or needle in r["description"].casefold()
    )

    def day(row: Row) -> date:
        return row.booking_date if wanted.booking else row.date

    return Result(
        rows,
        totals=_sums(rows, lambda _: ""),
        by_year=_sums(rows, lambda row: f"{day(row):%Y}"),
        by_month=_sums(rows, lambda row: f"{day(row):%Y-%m}"),
    )


def check_names(conn: sqlite3.Connection, wanted: Filter) -> None:
    """Refuse a name nothing can match: a typo would otherwise read as "no transactions"."""
    checks = (
        ("owner", wanted.owner, "SELECT 1 FROM owners WHERE name = ?"),
        (
            "account",
            wanted.account,
            "SELECT 1 FROM accounts a JOIN owners o ON o.id = a.owner_id "
            "WHERE a.name = ?1 AND o.name = coalesce(?2, o.name)",
        ),
        ("category", wanted.category, "SELECT 1 FROM categories WHERE name = ?"),
    )
    for what, name, sql in checks:
        if name is None:
            continue
        args = (name, wanted.owner) if what == "account" else (name,)
        if conn.execute(sql, args).fetchone() is None:
            whose = f" of {wanted.owner}" if what == "account" and wanted.owner else ""
            msg = f"no {what} named {name!r}{whose}"
            raise QueryError(msg)


def _row(r: sqlite3.Row) -> Row:
    return Row(
        id=r["id"],
        owner=r["owner"],
        account=r["account"],
        date=date.fromisoformat(r["date"]),
        booking_date=date.fromisoformat(r["booking_date"]),
        amount=from_nano(r["amount_nano"]),
        currency=r["currency"],
        description=r["description"],
        counterparty=r["counterparty"],
        kind=Kind(r["kind"]),
        category=r["category"],
        category_parent=r["category_parent"],
        counterparty_owner=r["counterparty_owner"],
        file=r["file"],
    )


def _sums(rows: Iterable[Row], period: Callable[[Row], str]) -> tuple[Sum, ...]:
    """One `Sum` per period and currency, in that order."""
    groups: dict[tuple[str, str], list[Decimal]] = {}
    for row in rows:
        groups.setdefault((period(row), row.currency), []).append(row.amount)
    return tuple(
        Sum(key[0], key[1], len(amounts), sum(amounts, Decimal(0)))
        for key, amounts in sorted(groups.items())
    )
