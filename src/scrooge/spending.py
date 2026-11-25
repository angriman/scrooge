"""Where the money goes: the spending view of the analysis page (plan decision 34).

The rows are the expenses the cash-flow page counts, each converted at the rate of its own
day, shown as amounts spent: a refund lowers its category. In one person's view what the
other person paid them lowers what they spent, on a line of its own; what they paid the
other person stays in its category. For both people together neither is there.

Each top-level category is set against the median month of the twelve before the range, and
against the same range a year before, where those months are known. A merchant is the
counterparty as written, without store numbers, places or addresses; fees, taxes and
interest are those of the account they were charged on. A category usually spent on and not
in the range is listed too, at nothing.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge import analysis, cashflow, query
from scrooge.analysis import CENT, Converted
from scrooge.cashflow import Counted, Grouping
from scrooge.networth import DEFAULT_CURRENCY
from scrooge.storage import store

SHOWN = 6  # the largest categories, each with its own colour in the chart
OTHERS = "Others"  # the rest of the categories, together
PAID_BACK = "Paid back"  # what the other person paid, in one person's view
MERCHANTS = 20  # the merchants listed; the others are added up


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
    categories: tuple[Category, ...]  # the largest first; those usually spent on, at nothing
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
    """Who was paid, as `query.merchant` names them: the transactions page filters by it."""
    return query.merchant(row.kind, row.account, row.counterparty)


def is_expense(row: Counted) -> bool:
    """Money spent, as the categories and merchants add it up: an expense or a refund of one,
    not what the other person paid back."""
    return is_spent(row) and not paid_back(row)


def spent_at(name: str) -> Callable[[Counted], bool]:
    """Whether a row is money spent at the merchant `name`, as the merchants are added up
    here: spaces and case are not told apart. The transactions page lists these rows."""
    paid = " ".join(name.split()).casefold()
    return lambda row: is_expense(row) and merchant(row).casefold() == paid


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
    came_back = [each.amount for each in inside if paid_back(each.row)]
    returned = sum(came_back, Decimal(0))
    months = _months(span.start, span.end)
    categories = _categories(found, span, spent, months, store.category_ranks(conn))
    largest = [category.name for category in categories[:SHOWN] if category.spent > 0]
    parts = (
        *largest,
        *([OTHERS] if sum(1 for c in categories if c.count) > len(largest) else []),
        *([PAID_BACK] if came_back else []),  # even when they net to nothing
    )
    periods = []
    for name, days, in_period in found.by_period(span, grouping):
        totals = dict.fromkeys(parts, Decimal(0))
        for each in in_period:
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
    found: analysis.Rows,
    span: analysis.Span,
    spent: list[Converted],
    months: int,
    ranks: store.Ranks,
) -> list[Category]:
    """The categories spent on, the largest first; equal ones in the taxonomy's order."""
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
                analysis.share(amount, total),
                (amount / months).quantize(CENT),
                None if usual is None else usual.get(name, Decimal(0)),
                None if year is None else year.get(name, Decimal(0)),
                tuple(
                    sorted(
                        (
                            Leaf(leaf, count, sum_.quantize(CENT))
                            for leaf, (count, sum_) in leaves.items()
                        ),
                        key=lambda leaf: (-leaf.spent, ranks.leaf(leaf.name, cashflow.EXPENSES)),
                    )
                ),
            )
        )
    # A category usually spent on and not at all in the range is there too, at nothing: a rent
    # that was not paid is a change as much as one that went up.
    nothing = Decimal(0).quantize(CENT)
    for name, amount in (usual or {}).items():
        if name not in by_top and amount > 0:
            categories.append(
                Category(
                    name,
                    0,
                    nothing,
                    analysis.share(nothing, total),
                    nothing,
                    amount,
                    None if year is None else year.get(name, Decimal(0)),
                    (),
                )
            )
    return sorted(
        categories,
        key=lambda category: (-category.spent, ranks.top(category.name, cashflow.EXPENSES)),
    )


def _usual(found: analysis.Rows, span: analysis.Span) -> dict[str, Decimal] | None:
    """top -> the median of what was spent in each of the twelve months before the range."""
    return analysis.usual(
        found,
        span.start,
        lambda each: None if paid_back(each.row) else (each.row.top, -each.amount),
    )


def _year_before(found: analysis.Rows, span: analysis.Span) -> dict[str, Decimal] | None:
    """top -> what was spent in the range a year earlier."""
    before = span.year_before()
    if before is None:
        return None
    sums: dict[str, Decimal] = defaultdict(Decimal)
    for each in found.between(*before):
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
