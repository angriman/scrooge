"""One category, merchant or account over time: what the transactions page shows above the
rows when they are cut to exactly one of them.

The rows are those the cash-flow page counts, each converted at the rate of its own day into
one currency (plan decision 34), and are read as the cash flow reads them: for a category of
expenses, a merchant or an account, money spent, a refund lowering it; for a category of
income, money earned. A merchant is named as the cash flow names it (`query.merchant`).

The months run up to the one of the last day committed, which is partial until its last day
is: statements come in after their month is over, so the month of today would read as
nothing spent. The usual month is the median of the twelve before it, when every person
shown has rows from the first of them on.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge import analysis, income, networth, query, spending
from scrooge.analysis import CENT, Converted
from scrooge.cashflow import Counted
from scrooge.extractors.common import month_end
from scrooge.networth import DEFAULT_CURRENCY

KINDS = ("category", "merchant", "account")  # what the transactions can be cut to
MONTHS = 24  # shown, the last one included
MERCHANTS = 5  # listed for a category, the largest first
_EARNED = "entrate"  # the side of the taxonomy whose categories are money earned


@dataclass(frozen=True, slots=True)
class Month:
    first: date  # its first day
    amount: Decimal  # spent, or earned for a category of income


@dataclass(frozen=True, slots=True)
class Merchant:
    name: str  # as last written
    amount: Decimal
    count: int


@dataclass(frozen=True, slots=True)
class Context:
    kind: str  # one of KINDS
    name: str  # as asked; for a merchant, as last written
    currency: str
    earned: bool  # the amounts are money earned, not spent
    months: tuple[Month, ...]  # MONTHS of them, oldest first; none when nothing is committed
    usual: Decimal | None  # the median of the twelve months before the last; None: not known
    through: date | None  # the last day committed for everyone shown; None: nothing is
    merchants: tuple[Merchant, ...] | None  # for a category only: the largest over the months

    @property
    def this_month(self) -> Decimal | None:
        return self.months[-1].amount if self.months else None

    @property
    def partial(self) -> bool:
        """Whether the last month is not over in the statements committed."""
        return self.through is not None and self.through < month_end(self.through)


def run(
    conn: sqlite3.Connection,
    kind: str,
    name: str,
    *,
    owner: str | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> Context:
    """`name`, a category, a merchant or an account (`kind`), of `owner` or of everyone, month
    by month in `currency`. Raises `query.QueryError` for an owner, an account or a category
    the database does not have; a merchant nothing was paid to is only empty."""
    if kind not in KINDS:
        msg = f"{kind!r} is not one of {', '.join(KINDS)}"
        raise ValueError(msg)
    query.check_names(
        conn,
        query.Filter(
            owner=owner,
            account=name if kind == "account" else None,
            category=name if kind == "category" else None,
        ),
    )
    earned = kind == "category" and _is_earned(conn, name)
    # Every counted row, so that the months known are those of the history, not only those
    # of the rows that match.
    found = analysis.rows(conn, owner, currency, lambda _: True)
    if found.through is None:
        return Context(kind, name, currency, earned, (), None, None, None)
    counts = _matches(kind, name, earned=earned)

    def value(each: Converted) -> Decimal:
        return each.amount if earned else -each.amount

    last = found.through.replace(day=1)
    starts = [analysis.add_months(last, n) for n in range(1 - MONTHS, 1)]
    inside = [each for each in found.between(starts[0], found.through) if counts(each.row)]
    sums: dict[date, Decimal] = defaultdict(Decimal)
    for each in inside:
        sums[each.day.replace(day=1)] += value(each)
    usual = analysis.usual(
        found, last, lambda each: ("", value(each)) if counts(each.row) else None
    )
    return Context(
        kind,
        _written(inside, name) if kind == "merchant" else name,
        currency,
        earned,
        tuple(Month(start, sums[start].quantize(CENT)) for start in starts),
        None if usual is None else usual.get("", Decimal(0)),
        found.through,
        _merchants(inside, value) if kind == "category" else None,
    )


def balance(
    conn: sqlite3.Connection,
    account: str,
    *,
    owner: str | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> Decimal | None:
    """What `account` holds on the last day, in `currency`: of `owner`, or of everyone who has
    an account of that name, together. None when nothing is valued for it."""
    held = networth.balances(conn, networth.value(conn, owner), currency)
    found = [value for (_, name), value in held.items() if name == account]
    return sum(found, Decimal(0)) if found else None


def _is_earned(conn: sqlite3.Connection, category: str) -> bool:
    """Whether `category` is one of income: what it adds up to is then money earned."""
    found = conn.execute(
        "SELECT 1 FROM categories WHERE name = ? AND side = ?", (category, _EARNED)
    ).fetchone()
    return found is not None


def _matches(kind: str, name: str, *, earned: bool) -> Callable[[Counted], bool]:
    """Which counted rows `name` adds up, as the cash flow counts them."""
    if earned:
        return lambda row: income.is_income(row) and name in (row.top, row.leaf)
    if kind == "category":
        return lambda row: spending.is_expense(row) and name in (row.top, row.leaf)
    if kind == "account":
        return lambda row: spending.is_expense(row) and row.account == name
    return spending.spent_at(name)


def _written(rows: list[Converted], asked: str) -> str:
    """A merchant's name as last written; as asked when nothing was paid to it."""
    return spending.merchant(rows[-1].row) if rows else asked


def _merchants(
    rows: list[Converted], value: Callable[[Converted], Decimal]
) -> tuple[Merchant, ...]:
    """The MERCHANTS of `rows` (oldest first) that add up to the most, by `value`."""
    sums: dict[str, tuple[int, Decimal]] = {}
    names: dict[str, str] = {}
    for each in rows:  # oldest first: the last name written stays
        name = spending.merchant(each.row)
        count, total = sums.get(name.casefold(), (0, Decimal(0)))
        sums[name.casefold()] = (count + 1, total + value(each))
        names[name.casefold()] = name
    found = sorted(
        (Merchant(names[key], total.quantize(CENT), count) for key, (count, total) in sums.items()),
        key=lambda merchant: (-merchant.amount, merchant.name),
    )
    return tuple(found[:MERCHANTS])
