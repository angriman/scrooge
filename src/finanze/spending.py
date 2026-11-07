"""Where the money goes: the spending view of the analysis page (plan decision 34).

The rows are the expenses the cash-flow page counts, each converted at the rate of its own
day, shown as amounts spent: a refund lowers its category. In one person's view what the
other person paid them lowers what they spent, on a line of its own; what they paid the
other person stays in its category. For both people together neither is there.

Each top-level category is set against the median month of the twelve before the range, and
against the same range a year before, where those months are known. A merchant is the
counterparty as written, without store numbers, places or addresses; fees, taxes and
interest are those of the account they were charged on.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median

from finanze import analysis, cashflow
from finanze.analysis import CENT, Converted
from finanze.cashflow import Counted, Grouping
from finanze.extractors.common import month_end
from finanze.models import Kind
from finanze.networth import DEFAULT_CURRENCY

SHOWN = 6  # the largest categories, each with its own colour in the chart
OTHERS = "Others"  # the rest of the categories, together
PAID_BACK = "Paid back"  # what the other person paid, in one person's view
MERCHANTS = 20  # the merchants listed; the others are added up
_FEES = frozenset({Kind.COMMISSION, Kind.TAX, Kind.INTEREST})
_SHARE = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class Leaf:
    name: str
    count: int
    spent: Decimal


@dataclass(frozen=True, slots=True)
class Category:
    name: str  # top-level
    count: int
    spent: Decimal  # money out, less what came back
    share: Decimal | None  # of all spent; None when nothing was
    monthly: Decimal  # spent per month of the range
    usual: Decimal | None  # the median month of the twelve before the range
    year_before: Decimal | None  # spent in the same range a year earlier
    leaves: tuple[Leaf, ...]  # the largest first


@dataclass(frozen=True, slots=True)
class Merchant:
    name: str  # as last written
    category: str  # the top-level category most was spent on there
    count: int
    spent: Decimal
    first: date
    last: date

    @property
    def average(self) -> Decimal:
        return (self.spent / self.count).quantize(CENT)


@dataclass(frozen=True, slots=True)
class Period:
    name: str
    first: date
    last: date
    parts: dict[str, Decimal]  # every one of `Spending.parts`, in that order

    @property
    def total(self) -> Decimal:
        return sum(self.parts.values(), Decimal(0))


@dataclass(frozen=True, slots=True)
class Spending:
    currency: str
    months: int  # the calendar months the range touches
    spent: Decimal  # in all categories
    paid_back: Decimal  # by the other person: taken off what was spent
    parts: tuple[str, ...]  # what the chart stacks: the largest categories, OTHERS, PAID_BACK
    periods: tuple[Period, ...]
    categories: tuple[Category, ...]  # the largest first
    merchants: tuple[Merchant, ...]  # the MERCHANTS largest
    other_merchants: int  # how many more
    other_spent: Decimal  # and what was spent at them
    through: date | None

    @property
    def net(self) -> Decimal:
        return self.spent - self.paid_back


def is_spent(row: Counted) -> bool:
    """An expense, a refund of one, or (for one person) what the other paid them."""
    if row.investing:
        return False
    return row.side == cashflow.EXPENSES or row.counterparty_owner is not None


def paid_back(row: Counted) -> bool:
    """What the other person paid, other than into a category of expenses."""
    return row.side != cashflow.EXPENSES


def merchant(row: Counted) -> str:
    """Who was paid: the counterparty up to its first comma, without digits or signs; the
    account for a fee, a tax, interest, or a row that names nobody."""
    if row.kind in _FEES:
        return row.account
    name = re.sub(r"[\W\d_]+", " ", row.counterparty.partition(",")[0])
    return " ".join(name.split()) or row.account


def run(  # noqa: PLR0913  # the range, whose, how grouped, in what
    conn: sqlite3.Connection,
    *,
    first: date | None = None,
    last: date | None = None,
    owner: str | None = None,
    grouping: Grouping = Grouping.MONTH,
    currency: str = DEFAULT_CURRENCY,
) -> Spending:
    """What was spent from `first` to `last`, both included, by `owner` or by everyone, in
    `currency`, ranges filled in as for income."""
    found = analysis.rows(conn, owner, currency, is_spent)
    span = found.span(first, last)
    if span is None:
        nothing = Decimal(0)
        return Spending(currency, 0, nothing, nothing, (), (), (), (), 0, nothing, found.through)
    inside = found.between(span.start, span.end)
    spent = [each for each in inside if not paid_back(each.row)]
    returned = sum((each.amount for each in inside if paid_back(each.row)), Decimal(0))
    months = _months(span.start, span.end)
    categories = _categories(found, span, spent, months)
    largest = [category.name for category in categories[:SHOWN] if category.spent > 0]
    parts = (
        *largest,
        *([OTHERS] if len(categories) > len(largest) else []),
        *([PAID_BACK] if returned else []),
    )
    periods = []
    for name in analysis.periods(span.start, span.end, grouping):
        days = cashflow.days(name)
        totals = dict.fromkeys(parts, Decimal(0))
        for each in found.between(max(days[0], span.start), min(days[1], span.end)):
            if paid_back(each.row):
                part = PAID_BACK
            else:
                part = each.row.top if each.row.top in largest else OTHERS
            totals[part] -= each.amount
        periods.append(Period(name, *days, {k: v.quantize(CENT) for k, v in totals.items()}))
    total = -sum((each.amount for each in spent), Decimal(0))
    merchants = _merchants(spent)
    rest = merchants[MERCHANTS:]
    return Spending(
        currency,
        months,
        total.quantize(CENT),
        returned.quantize(CENT),
        parts,
        tuple(periods),
        tuple(categories),
        tuple(merchants[:MERCHANTS]),
        len(rest),
        sum((each.spent for each in rest), Decimal(0)),
        span.through,
    )


def _categories(
    found: analysis.Rows, span: analysis.Span, spent: list[Converted], months: int
) -> list[Category]:
    total = -sum((each.amount for each in spent), Decimal(0))
    by_top: dict[str, list[Converted]] = defaultdict(list)
    for each in spent:
        by_top[each.row.top].append(each)
    usual = _usual(found, span)
    year = _year_before(found, span)
    categories = []
    for name, rows in by_top.items():
        amount = -sum((each.amount for each in rows), Decimal(0))
        leaves: dict[str, tuple[int, Decimal]] = {}
        for each in rows:
            count, sum_ = leaves.get(each.row.leaf, (0, Decimal(0)))
            leaves[each.row.leaf] = (count + 1, sum_ - each.amount)
        categories.append(
            Category(
                name,
                len(rows),
                amount.quantize(CENT),
                (amount / total).quantize(_SHARE) if total > 0 else None,
                (amount / months).quantize(CENT),
                None if usual is None else usual.get(name, Decimal(0)),
                None if year is None else year.get(name, Decimal(0)),
                tuple(
                    sorted(
                        (
                            Leaf(leaf, count, sum_.quantize(CENT))
                            for leaf, (count, sum_) in leaves.items()
                        ),
                        key=lambda leaf: (-leaf.spent, leaf.name),
                    )
                ),
            )
        )
    return sorted(categories, key=lambda category: (-category.spent, category.name))


def _usual(found: analysis.Rows, span: analysis.Span) -> dict[str, Decimal] | None:
    """top -> the median of what was spent in each of the twelve months before the range."""
    month = span.start.replace(day=1)
    starts = [analysis.add_months(month, n) for n in range(-12, 0)]
    if starts[0] < span.known:
        return None
    sums: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0)] * 12)
    for n, start in enumerate(starts):
        for each in found.between(start, month_end(start)):
            if not paid_back(each.row):
                sums[each.row.top][n] -= each.amount
    return {top: median(values).quantize(CENT) for top, values in sums.items()}


def _year_before(found: analysis.Rows, span: analysis.Span) -> dict[str, Decimal] | None:
    """top -> what was spent in the range a year earlier."""
    start, end = analysis.year_before(span.start), analysis.year_before(span.end)
    if start < span.known:
        return None
    sums: dict[str, Decimal] = defaultdict(Decimal)
    for each in found.between(start, end):
        if not paid_back(each.row):
            sums[each.row.top] -= each.amount
    return {top: total.quantize(CENT) for top, total in sums.items()}


def _merchants(spent: list[Converted]) -> list[Merchant]:
    rows: dict[str, list[Converted]] = defaultdict(list)
    names: dict[str, str] = {}
    for each in spent:  # oldest first: the last name written stays
        name = merchant(each.row)
        rows[name.casefold()].append(each)
        names[name.casefold()] = name
    merchants = []
    for key, mine in rows.items():
        by_top: dict[str, Decimal] = defaultdict(Decimal)
        for each in mine:
            by_top[each.row.top] -= each.amount
        amount = -sum((each.amount for each in mine), Decimal(0))
        merchants.append(
            Merchant(
                names[key],
                max(by_top, key=lambda top: (by_top[top], top)),
                len(mine),
                amount.quantize(CENT),
                mine[0].day,
                mine[-1].day,
            )
        )
    return sorted(merchants, key=lambda merchant: (-merchant.spent, merchant.name))


def _months(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month + 1
