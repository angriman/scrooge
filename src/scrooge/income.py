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
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge import analysis, cashflow
from scrooge.analysis import CENT, Converted
from scrooge.cashflow import Counted, Grouping
from scrooge.networth import DEFAULT_CURRENCY

LAVORO, INVESTIMENTI, ALTRO = "Lavoro", "Investimenti", "Altro"
GROUPS = (LAVORO, INVESTIMENTI, ALTRO)  # as they are stacked and listed
# The group of each income leaf; every other leaf is ALTRO.
_GROUP_OF = {
    "Stipendio": LAVORO,
    "Dividendi": INVESTIMENTI,
    "Interessi": INVESTIMENTI,
    "Trading": INVESTIMENTI,
}
_TRADING = "Trading"  # a leaf on both sides: what was won and what was lost
_SHARE = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class Period:
    name: str  # as `cashflow.period` names it
    first: date
    last: date
    groups: dict[str, Decimal]  # every one of GROUPS, in that order
    employer: Decimal | None  # LAVORO's share of income in the twelve months ending with it

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
        return row.leaf == _TRADING
    return row.side == cashflow.INCOME


def group(row: Counted) -> str:
    """The group of an income row."""
    return INVESTIMENTI if row.investing else _GROUP_OF.get(row.leaf, ALTRO)


def run(  # noqa: PLR0913  # the range, whose, how grouped, in what
    conn: sqlite3.Connection,
    *,
    first: date | None = None,
    last: date | None = None,
    owner: str | None = None,
    grouping: Grouping = Grouping.MONTH,
    currency: str = DEFAULT_CURRENCY,
) -> Income:
    """Income from `first` to `last`, both included, of `owner` or of everyone, in
    `currency`. Without `first` the range starts with the first income, without `last` it
    ends with the last day committed."""
    found = analysis.rows(conn, owner, currency, is_income)
    span = found.span(first, last)
    if span is None:
        return Income(currency, (), (), found.through)
    start, end = span.start, span.end
    periods = []
    for name in analysis.periods(start, end, grouping):
        days = cashflow.days(name)
        totals = dict.fromkeys(GROUPS, Decimal(0))
        for each in found.between(max(days[0], start), min(days[1], end)):
            totals[group(each.row)] += each.amount
        periods.append(
            Period(
                name,
                *days,
                {name: total.quantize(CENT) for name, total in totals.items()},
                _employer(found, span, days),
            )
        )
    sums: dict[tuple[str, str], tuple[int, Decimal]] = {}
    for each in found.between(start, end):
        key = (group(each.row), each.row.leaf)
        count, total = sums.get(key, (0, Decimal(0)))
        sums[key] = (count + 1, total + each.amount)
    sources = sorted(
        (
            Source(name, leaf, count, total.quantize(CENT))
            for (name, leaf), (count, total) in sums.items()
        ),
        key=lambda source: (GROUPS.index(source.group), -source.total, source.leaf),
    )
    return Income(currency, tuple(periods), tuple(sources), found.through)


def _employer(found: analysis.Rows, span: analysis.Span, days: tuple[date, date]) -> Decimal | None:
    window = analysis.window(span, days)
    if window is None:
        return None
    rows: list[Converted] = list(found.between(*window))
    total = sum((each.amount for each in rows), Decimal(0))
    work = sum((each.amount for each in rows if group(each.row) == LAVORO), Decimal(0))
    return (work / total).quantize(_SHARE) if total > 0 else None
