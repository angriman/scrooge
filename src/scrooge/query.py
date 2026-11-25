"""Find stored transactions: the one function behind `scrooge query` and the transactions page.

Only committed statements are read (the `committed_transactions` view). Amounts stay in their
own currency, so every total is per currency: nothing is converted here.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from itertools import pairwise

from scrooge import categories
from scrooge.categories import Labels  # by its name: `Choices.categories` hides the module
from scrooge.extractors.common import month_end
from scrooge.models import CERTIFICATE_SOURCES, Kind
from scrooge.storage import store
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
    a top-level category stands for all its leaves. `kinds` keeps the rows of any of them, as
    `kind` does those of one. `at_least` and `at_most` bound the size of the amount, whatever
    its sign. `account_kinds` keeps the rows of the accounts of those kinds (`ACCOUNTS` names
    them in groups). `text` is looked for in the counterparty and the description, ignoring case.
    `merchant` is who was paid as the cash flow names them (`merchant`), ignoring case, and
    keeps only what the cash flow counts as spent there: no income, transfer, trade or money
    paid back.
    """

    first: date | None = None
    last: date | None = None
    booking: bool = False
    owner: str | None = None
    account: str | None = None
    category: str | None = None
    kind: Kind | None = None
    kinds: tuple[Kind, ...] = ()
    at_least: Decimal | None = None
    at_most: Decimal | None = None
    text: str = ""
    merchant: str | None = None
    account_kinds: tuple[str, ...] = ()


# The kinds of account a filter can be cut to, by the name the address gives them: a broker's
# statements feed two kinds, one for its cash and one for what it holds.
ACCOUNTS: dict[str, tuple[str, ...]] = {
    "broker": ("broker_cash", "broker_securities"),
    "bank": ("bank",),
    "card": ("card",),
}

_FEES = frozenset({Kind.COMMISSION, Kind.TAX, Kind.INTEREST})


def merchant(kind: Kind, account: str, counterparty: str) -> str:
    """Who was paid: the counterparty up to its first comma, without digits or signs; the
    account for a fee, a tax, interest, or a row that names nobody."""
    if kind in _FEES:
        return account
    name = re.sub(r"[\W\d_]+", " ", counterparty.partition(",")[0])
    return " ".join(name.split()) or account


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
    # The first and last day committed statements cover, a pension certificate's one day not
    # counted; None: there is none.
    first: date | None
    last: date | None
    staged: int  # statements staged and not yet committed
    transactions: int  # of committed statements


def people(conn: sqlite3.Connection) -> tuple[Person, ...]:
    """Everyone in the database, by name."""
    found = conn.execute(
        "SELECT o.name, "
        "count(f.id) FILTER (WHERE f.committed_at IS NOT NULL) AS committed, "
        "min(f.period_start) FILTER (WHERE f.committed_at IS NOT NULL "
        "AND f.source NOT IN (?, ?)) AS first, "
        "max(f.period_end) FILTER (WHERE f.committed_at IS NOT NULL "
        "AND f.source NOT IN (?, ?)) AS last, "
        "count(f.id) FILTER (WHERE f.committed_at IS NULL) AS staged, "
        "(SELECT count(*) FROM committed_transactions t WHERE t.owner = o.name) AS transactions "
        "FROM owners o LEFT JOIN statement_files f ON f.owner_id = o.id "
        "GROUP BY o.id ORDER BY o.name",
        CERTIFICATE_SOURCES * 2,
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


_BUILT_IN = categories.Labels()  # the labels of the taxonomy alone


@dataclass(frozen=True, slots=True)
class Choices:
    """What the account and category filters can be set to."""

    accounts: tuple[str, ...]
    # A heading and the keys under it: each top-level expense category followed by its
    # leaves, then income, then transfers (`HEADINGS`, headings of their own).
    categories: tuple[tuple[str, tuple[str, ...]], ...]
    # The same without the top-level keys: what a transaction can be given.
    leaves: tuple[tuple[str, tuple[str, ...]], ...]
    # The top-level categories of the expenses: what a new expense category can go under.
    groups: tuple[str, ...] = ()
    # The label of each key, and of each heading.
    label: Labels = _BUILT_IN


HEADINGS = {"income": "Income", "transfer": "Transfer"}  # the sides without groups


def choices(conn: sqlite3.Connection, owner: str | None = None) -> Choices:
    """The accounts of `owner` (of everyone without one) and the whole taxonomy, by key."""
    accounts = conn.execute(
        "SELECT DISTINCT a.name FROM accounts a JOIN owners o ON o.id = a.owner_id "
        "WHERE o.name = coalesce(?, o.name) ORDER BY a.name",
        (owner,),
    ).fetchall()
    found = conn.execute(
        "SELECT c.key AS name, c.side, p.key AS parent FROM categories c "  # noqa: S608  # only fixed text is interpolated
        f"LEFT JOIN categories p ON p.id = c.parent_id ORDER BY {store.TAXONOMY_ORDER}"
    ).fetchall()
    groups: dict[str, list[str]] = {}
    leaves: dict[str, list[str]] = {}
    for r in found:
        top = r["side"] == "expenses" and r["parent"] is None
        heading = r["parent"] or (r["name"] if top else HEADINGS[r["side"]])
        names = groups.setdefault(heading, [])
        if r["name"] not in names:  # a leaf of the key of its parent is one choice
            names.append(r["name"])
        if not top:
            leaves.setdefault(heading, []).append(r["name"])
    # Found under Banking, as the cash flow counts it, though no row is given it.
    groups.get(categories.GROUP_BANKING, []).append(categories.INTEREST_CHARGED)
    return Choices(
        tuple(r["name"] for r in accounts),
        tuple((heading, tuple(names)) for heading, names in groups.items()),
        tuple((heading, tuple(names)) for heading, names in leaves.items()),
        tuple(r["name"] for r in found if r["side"] == "expenses" and r["parent"] is None),
        store.category_labels(conn),
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


# A row's category and top-level one as the cash flow counts them: interest charged
# (`categories.charged`, here in SQL) as the leaf INTEREST_CHARGED of Banking.
_CHARGED = f"(category = '{categories.INTEREST}' AND amount_nano < 0)"
_CATEGORY = f"CASE WHEN {_CHARGED} THEN '{categories.INTEREST_CHARGED}' ELSE category END"
_PARENT = f"CASE WHEN {_CHARGED} THEN '{categories.GROUP_BANKING}' ELSE category_parent END"


def category_key(conn: sqlite3.Connection, name: str) -> str:
    """The key of the category `name` means, by its key or its label (`store.category_key`):
    `categories.INTEREST_CHARGED` too, which no row is given but the cash flow counts by."""
    if categories.key(name) == categories.INTEREST_CHARGED:
        return categories.INTEREST_CHARGED
    return store.category_key(conn, name)


def run(conn: sqlite3.Connection, wanted: Filter) -> Result:
    """The committed transactions that match `wanted`, with what they add up to. Its category
    may be named by its key or its label (`category_key`), and finds the rows the cash flow
    counts under it: Interest those at or above nothing, Banking and Interest charged those
    below (plan decision 52)."""
    if wanted.category is not None:
        wanted = replace(wanted, category=category_key(conn, wanted.category))
    check_names(conn, wanted)
    column = "booking_date" if wanted.booking else "date"
    conditions: list[tuple[str, object]] = [
        (f"{column} >= ?", wanted.first and wanted.first.isoformat()),
        (f"{column} <= ?", wanted.last and wanted.last.isoformat()),
        ("owner = ?", wanted.owner),
        ("account = ?", wanted.account),
        (f"? IN ({_CATEGORY}, {_PARENT})", wanted.category),
        ("kind = ?", wanted.kind and wanted.kind.value),
        (
            "kind IN (SELECT value FROM json_each(?))",
            json.dumps([kind.value for kind in wanted.kinds]) if wanted.kinds else None,
        ),
        (
            "(SELECT a.kind FROM transactions t JOIN accounts a ON a.id = t.account_id "
            "WHERE t.id = committed_transactions.id) IN (SELECT value FROM json_each(?))",
            json.dumps(list(wanted.account_kinds)) if wanted.account_kinds else None,
        ),
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
    if wanted.merchant is not None:
        rows = _spent_at(conn, wanted.merchant, wanted.owner, rows)

    def day(row: Row) -> date:
        return row.booking_date if wanted.booking else row.date

    return Result(
        rows,
        totals=_sums(rows, lambda _: ""),
        by_year=_sums(rows, lambda row: f"{day(row):%Y}"),
        by_month=_sums(rows, lambda row: f"{day(row):%Y-%m}"),
    )


def check_names(conn: sqlite3.Connection, wanted: Filter) -> None:
    """Refuse a name nothing can match: a typo would otherwise read as "no transactions". A
    category may be named by its key or its label."""
    category = wanted.category and category_key(conn, wanted.category)
    checks = (
        ("owner", wanted.owner, "SELECT 1 FROM owners WHERE name = ?"),
        (
            "account",
            wanted.account,
            "SELECT 1 FROM accounts a JOIN owners o ON o.id = a.owner_id "
            "WHERE a.name = ?1 AND o.name = coalesce(?2, o.name)",
        ),
        ("category", wanted.category, "SELECT 1 FROM categories WHERE key = ?"),
    )
    for what, name, sql in checks:
        # Interest charged is no stored category, and always there to find (`run`).
        if name is None or (what == "category" and category == categories.INTEREST_CHARGED):
            continue
        args = {"account": (name, wanted.owner), "category": (category,)}.get(what, (name,))
        if conn.execute(sql, args).fetchone() is None:
            whose = f" of {wanted.owner}" if what == "account" and wanted.owner else ""
            msg = f"no {what} named {name!r}{whose}"
            raise QueryError(msg)


def _spent_at(
    conn: sqlite3.Connection, name: str, owner: str | None, rows: tuple[Row, ...]
) -> tuple[Row, ...]:
    """The `rows` the cash flow counts as money spent at the merchant `name`, of `owner` or of
    everyone: their total is then the figure the cash flow shows for it."""
    # The cash flow reads its rows through this module: it is imported when asked for.
    from scrooge import cashflow, spending  # noqa: PLC0415

    at = spending.spent_at(name)
    spent = {row.id for row in cashflow.counted(conn, owner) if at(row)}
    return tuple(row for row in rows if row.id in spent)


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


@dataclass(frozen=True, slots=True)
class CategorySum:
    """What the rows of one top-level category and one currency add up to."""

    name: str  # the key of the top-level category; `TRANSFERS` for the rows that have none
    currency: str
    count: int
    total: Decimal


TRANSFERS = "Transfers"  # what the rows of kind `internal`, which have no category, are summed as


def by_category(rows: Iterable[Row], ranks: store.Ranks) -> tuple[CategorySum, ...]:
    """What `rows` add up to by top-level category and currency, the largest first; equal
    ones in the taxonomy's order (`ranks`). Interest charged is of Banking (`run`)."""
    sums: dict[tuple[str, str], tuple[int, Decimal]] = {}
    for row in rows:
        charged = categories.charged(row.category, to_nano(row.amount))
        top = categories.GROUP_BANKING if charged else row.category_parent or row.category
        key = (top or TRANSFERS, row.currency)
        count, total = sums.get(key, (0, Decimal(0)))
        sums[key] = (count + 1, total + row.amount)
    return tuple(
        sorted(
            (CategorySum(name, currency, *sums[name, currency]) for name, currency in sums),
            key=lambda found: (-abs(found.total), ranks.top(found.name), found.currency),
        )
    )


_PAID = frozenset({Kind.INCOME, Kind.EXPENSE, Kind.TRANSFER})
_MONTHLY = range(26, 36)  # days between two payments of a month apart, give or take
_YEARLY = range(350, 381)


def _paced(gaps: list[int], pace: range) -> bool:
    """Whether three gaps in four are of `pace`: a payment skipped or made twice is allowed."""
    return sum(gap in pace for gap in gaps) * 4 >= len(gaps) * 3


@dataclass(frozen=True, slots=True)
class Recurrence:
    """Payments that come back at a steady pace: those of one person and account to one name.

    `name` is who was paid as the cash flow names them (`merchant`), so the merchant filter of
    the transactions finds them; two groups can have the same one. `rows` are the payments
    themselves, oldest first; `ended` when none came for an interval and a half before
    `through`, the last day of the rows of the same person and account looked at: an account
    whose statements stop earlier than the others' does not make its payments look ended.
    """

    name: str
    owner: str
    account: str
    every: str  # "monthly" or "yearly"
    rows: tuple[Row, ...]
    through: date

    @property
    def id(self) -> str:
        """The first payment's id: it stays the same while the group grows, and no two groups
        share it, even when they share a name."""
        return self.rows[0].id

    @property
    def latest(self) -> Row:
        return self.rows[-1]

    @property
    def previous(self) -> Decimal | None:
        """What the payment before the latest was, when the price changed between the two: a
        rise of years ago is the price now, not a change."""
        before = self.rows[-2].amount
        return None if before == self.latest.amount else before

    @property
    def first(self) -> date:
        return self.rows[0].date

    @property
    def last(self) -> date:
        return self.latest.date

    @property
    def next(self) -> date:
        """When the next payment is due: a month or a year after the last, on the same day
        where the month has it."""
        months = 1 if self.every == "monthly" else 12
        year, month = divmod(self.last.year * 12 + self.last.month - 1 + months, 12)
        end = month_end(date(year, month + 1, 1))
        return end.replace(day=min(self.last.day, end.day))

    @property
    def monthly(self) -> Decimal:
        """The latest amount spread over a month: a yearly one is a twelfth of it."""
        return self.latest.amount if self.every == "monthly" else self.latest.amount / 12

    @property
    def ended(self) -> bool:
        return (self.through - self.last).days * 2 > 3 * _INTERVAL[self.every]


_INTERVAL = {"monthly": 30, "yearly": 365}


def recurrences(rows: Iterable[Row]) -> tuple[Recurrence, ...]:
    """The groups of rows that come back at a steady pace.

    Rows go together when they are of the same person and account, the same counterparty (or
    description, without one) and the same direction; of those, the ones within a fifth of
    their middle amount are its payments. Three or more with a month between them, as a rule
    (three gaps in four), are monthly; two or more a year apart are yearly: a shop where some
    purchases happen to cost about the same is not paid at a pace. Only money in
    and out (`_PAID`) is a payment: trades, fees, dividends and moves between one's own
    accounts are never recurring.
    """
    groups: dict[tuple[str, str, str, bool], list[Row]] = {}
    through: dict[tuple[str, str], date] = {}
    for row in rows:
        whose = (row.owner, row.account)
        through[whose] = max(through.get(whose, row.date), row.date)
        name = (row.counterparty or row.description).strip().casefold()
        if name and row.kind in _PAID:
            groups.setdefault((row.owner, row.account, name, row.amount > 0), []).append(row)
    found: list[Recurrence] = []
    for (owner, account, _, _), listed in groups.items():
        sizes = sorted(abs(row.amount) for row in listed)
        middle = sizes[len(sizes) // 2]
        steady = sorted(
            (row for row in listed if abs(abs(row.amount) - middle) * 5 <= middle),
            key=lambda row: row.date,
        )
        gaps = [(b.date - a.date).days for a, b in pairwise(steady)]
        if not gaps or not middle:
            continue
        every = (
            "monthly"
            if len(steady) >= 3 and _paced(gaps, _MONTHLY)
            else "yearly"
            if _paced(gaps, _YEARLY)
            else None
        )
        if every:
            name = merchant(steady[-1].kind, account, steady[-1].counterparty)
            last = through[owner, account]
            found.append(Recurrence(name, owner, account, every, tuple(steady), last))
    return tuple(found)


def recurring(rows: Iterable[Row]) -> dict[str, str]:
    """The rows that come back at a steady pace: row id -> "monthly" or "yearly" (see
    `recurrences`)."""
    return {row.id: group.every for group in recurrences(rows) for row in group.rows}
