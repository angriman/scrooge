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
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from finanze import cashflow, query
from finanze.cashflow import Grouping
from finanze.networth import DEFAULT_CURRENCY, convert, rates_on
from finanze.storage.money import from_nano

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
_CENT = Decimal("0.01")
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


@dataclass(frozen=True, slots=True)
class _Row:
    day: date
    owner: str
    group: str
    leaf: str
    amount: Decimal  # converted


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
    rows = _income(conn, owner, currency)
    through = _through(conn, owner)
    if not rows or through is None:
        return Income(currency, (), (), through)
    start, end = first or rows[0].day, last or through
    # From the first month of each person's income: before, twelve months are not known.
    known = max(
        min(row.day for row in rows if row.owner == person).replace(day=1)
        for person in {row.owner for row in rows}
    )
    when = [row.day for row in rows]
    periods = []
    for name in _periods(start, end, grouping):
        days = cashflow.days(name)
        totals = dict.fromkeys(GROUPS, Decimal(0))
        for row in _between(rows, when, max(days[0], start), min(days[1], end)):
            totals[row.group] += row.amount
        periods.append(
            Period(
                name,
                *days,
                {group: total.quantize(_CENT) for group, total in totals.items()},
                _employer(rows, when, days, through, known),
            )
        )
    sums: dict[tuple[str, str], tuple[int, Decimal]] = {}
    for row in _between(rows, when, start, end):
        count, total = sums.get((row.group, row.leaf), (0, Decimal(0)))
        sums[row.group, row.leaf] = (count + 1, total + row.amount)
    sources = sorted(
        (
            Source(group, leaf, count, total.quantize(_CENT))
            for (group, leaf), (count, total) in sums.items()
        ),
        key=lambda source: (GROUPS.index(source.group), -source.total, source.leaf),
    )
    return Income(currency, tuple(periods), tuple(sources), through)


def _income(conn: sqlite3.Connection, owner: str | None, currency: str) -> list[_Row]:
    """Every income row of `owner` (of everyone), oldest first, converted into `currency`."""
    found: dict[str, list[tuple[cashflow.Counted, str]]] = defaultdict(list)
    for row in cashflow.counted(conn, owner):
        if row.counterparty_owner is not None:
            continue
        if row.leaf == _TRADING and row.investing:
            group = INVESTIMENTI
        elif row.side == cashflow.INCOME and not row.investing:
            group = _GROUP_OF.get(row.leaf, ALTRO)
        else:
            continue
        found[row.currency].append((row, group))
    converted = []
    for native, members in found.items():
        rates = rates_on(conn, [row.day for row, _ in members])
        amounts = [from_nano(row.amount_nano) for row, _ in members]
        for (row, group), amount in zip(
            members, convert(amounts, native, currency, rates), strict=True
        ):
            converted.append(_Row(row.day, row.owner, group, row.leaf, amount))
    return sorted(converted, key=lambda row: row.day)


def _through(conn: sqlite3.Connection, owner: str | None) -> date | None:
    """The last day committed for `owner`, or for both: the earlier of their last days."""
    lasts = [
        person.last
        for person in query.people(conn)
        if person.last is not None and owner in (None, person.name)
    ]
    return min(lasts, default=None)


def _periods(start: date, end: date, grouping: Grouping) -> list[str]:
    names = []
    day = start
    while day <= end:
        names.append(cashflow.period(day, grouping))
        day = cashflow.days(names[-1])[1] + timedelta(days=1)
    return names


def _between(rows: list[_Row], when: list[date], first: date, last: date) -> list[_Row]:
    """The rows from `first` to `last`, both included; `when` is their days."""
    return rows[bisect_left(when, first) : bisect_right(when, last)]


def _employer(
    rows: list[_Row], when: list[date], days: tuple[date, date], through: date, known: date
) -> Decimal | None:
    end = min(days[1], through)
    start = _year_before(end) + timedelta(days=1)
    if end < days[0] or start < known:
        return None
    work = total = Decimal(0)
    for row in _between(rows, when, start, end):
        total += row.amount
        if row.group == LAVORO:
            work += row.amount
    return (work / total).quantize(_SHARE) if total > 0 else None


def _year_before(day: date) -> date:
    """The same day a year earlier; 28 February for 29 February."""
    return day.replace(year=day.year - 1, day=min(day.day, 28 if day.month == 2 else day.day))
