"""Where income comes from, period by period: the income view of the analysis page.

The rows are those the cash-flow page counts, each converted at the rate of its own day into
one currency, so that sources in francs, euros and dollars add up (plan decision 34). Income
is grouped by where it comes from: work, investments, the rest. Investments are what they
earned, not what they returned: dividends, interest, and trading counted net, its losses on
the expense side included; securities bought and sold are principal and never income. What
the other person pays is left out, for one person too: it is not money from outside.

The employer's share of a period is that of work in all income of the twelve months that end
with it, or with the last day committed when the period goes beyond. It is not given before
twelve months of income are known: for both people together, twelve of each.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge import analysis, cashflow, categories
from scrooge.analysis import CENT, Converted
from scrooge.cashflow import Counted, Grouping
from scrooge.networth import DEFAULT_CURRENCY
from scrooge.storage import store

# Where income comes from, as the page shows it: not categories.
WORK, INVESTMENTS, OTHER = "Work", "Investments", "Other"
GROUPS = (WORK, INVESTMENTS, OTHER)  # as they are stacked and listed
# The group of each income leaf; every other leaf is OTHER.
_GROUP_OF = {
    categories.SALARY: WORK,
    categories.DIVIDENDS: INVESTMENTS,
    categories.INTEREST: INVESTMENTS,
    categories.TRADING: INVESTMENTS,  # a leaf on both sides: what was won and what was lost
}


@dataclass(frozen=True, slots=True)
class Period:
    name: str  # as `cashflow.period` names it
    first: date
    last: date
    groups: dict[str, Decimal]  # every one of GROUPS, in that order
    employer: Decimal | None  # WORK's share of income in the twelve months ending with it

    @property
    def total(self) -> Decimal:
        return sum(self.groups.values(), Decimal(0))


@dataclass(frozen=True, slots=True)
class Source:
    """What one income leaf adds up to over the range."""

    group: str
    leaf: str
    count: int
    total: Decimal
    monthly: Decimal  # earned per month of the range
    usual: Decimal | None  # the median month of the twelve before the range
    year_before: Decimal | None  # earned in the same range a year earlier


@dataclass(frozen=True, slots=True)
class Income:
    currency: str
    periods: tuple[Period, ...]  # every period of the range, oldest first
    sources: tuple[Source, ...]  # by group, then the largest first
    through: date | None  # the last day committed for everyone shown; None: nothing is


def is_income(row: Counted) -> bool:
    """Income: not principal coming back, nor what the other person pays."""
    if row.counterparty_owner is not None:
        return False
    if row.investing:
        return row.leaf == categories.TRADING
    return row.side == cashflow.INCOME


def group(row: Counted) -> str:
    """The group of an income row."""
    return INVESTMENTS if row.investing else _GROUP_OF.get(row.leaf, OTHER)


def run(  # noqa: PLR0913  # the range, whose, how grouped, in what
    conn: sqlite3.Connection,
    *,
    first: date | None = None,
    last: date | None = None,
    owner: str | None = None,
    grouping: Grouping = Grouping.MONTH,
    currency: str = DEFAULT_CURRENCY,
    prepared: analysis.Prepared | None = None,
) -> Income:
    """Income from `first` to `last`, both included, of `owner` or of everyone, in
    `currency`. Without `first` the range starts with the first income, without `last` it
    ends with the last day committed."""
    found = (
        analysis.rows(conn, owner, currency, is_income)
        if prepared is None
        else prepared.select(is_income)
    )
    span = found.span(first, last)
    if span is None:
        return Income(currency, (), (), found.through)
    periods = []
    for name, days, inside in found.by_period(span, grouping):
        totals = dict.fromkeys(GROUPS, Decimal(0))
        for each in inside:
            totals[group(each.row)] += each.amount
        periods.append(
            Period(
                name,
                *days,
                {name: total.quantize(CENT) for name, total in totals.items()},
                _employer(found, span, days),
            )
        )
    months = analysis.months(span.start, span.end)
    sources = _sources(found, span, months, store.category_ranks(conn))
    return Income(currency, tuple(periods), tuple(sources), found.through)


def _sources(
    found: analysis.Rows,
    span: analysis.Span,
    months: int,
    ranks: store.Ranks,
) -> list[Source]:
    sums: dict[tuple[str, str], tuple[int, Decimal]] = {}
    for each in found.between(span.start, span.end):
        key = (group(each.row), each.row.leaf)
        count, total = sums.get(key, (0, Decimal(0)))
        sums[key] = (count + 1, total + each.amount)
    usual = _usual(found, span)
    year = _year_before(found, span)
    nothing = Decimal(0).quantize(CENT)
    sources = [
        Source(
            name,
            leaf,
            count,
            total.quantize(CENT),
            (total / months).quantize(CENT),
            None if usual is None else usual.get(leaf, nothing),
            None if year is None else year.get(leaf, nothing),
        )
        for (name, leaf), (count, total) in sums.items()
    ]
    # A source that usually comes in and did not in the range is there too, at nothing.
    seen = {leaf for _, leaf in sums}
    for leaf, amount in (usual or {}).items():
        if leaf not in seen and amount > 0:
            sources.append(
                Source(
                    _GROUP_OF.get(leaf, OTHER),
                    leaf,
                    0,
                    nothing,
                    nothing,
                    amount,
                    None if year is None else year.get(leaf, nothing),
                )
            )
    return sorted(
        sources,
        key=lambda source: (
            GROUPS.index(source.group),
            -source.total,
            ranks.leaf(source.leaf, cashflow.INCOME),
        ),
    )


def _usual(found: analysis.Rows, span: analysis.Span) -> dict[str, Decimal] | None:
    """leaf -> the median of what came in during each of the twelve months before the range."""
    return analysis.usual(found, span.start, lambda each: (each.row.leaf, each.amount))


def _year_before(found: analysis.Rows, span: analysis.Span) -> dict[str, Decimal] | None:
    """leaf -> what came in during the range a year earlier."""
    before = span.year_before()
    if before is None:
        return None
    sums: dict[str, Decimal] = defaultdict(Decimal)
    for each in found.between(*before):
        sums[each.row.leaf] += each.amount
    return {leaf: total.quantize(CENT) for leaf, total in sums.items()}


def _employer(found: analysis.Rows, span: analysis.Span, days: tuple[date, date]) -> Decimal | None:
    window = analysis.window(span, days)
    if window is None:
        return None
    rows: list[Converted] = list(found.between(*window))
    total = sum((each.amount for each in rows), Decimal(0))
    work = sum((each.amount for each in rows if group(each.row) == WORK), Decimal(0))
    return analysis.share(work, total)
