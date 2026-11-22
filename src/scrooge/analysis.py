"""What the views of the analysis page share (plan decision 34): the rows the cash-flow page
counts, each converted at the rate of its own day into one currency, and the range and the
periods they are shown over.

A range runs, unless asked otherwise, from the first row to the last day committed: for both
people together, the earlier of their last days. A comparison with the months before a day is
only made where every person shown has rows from the month of that day on.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from scrooge import cashflow, query
from scrooge.cashflow import Counted, Grouping
from scrooge.networth import convert, rates_on
from scrooge.storage.money import from_nano

CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Converted:
    row: Counted
    amount: Decimal  # in the currency asked for, signed as stored

    @property
    def day(self) -> date:
        return self.row.day


@dataclass(frozen=True, slots=True)
class Span:
    """A range with something to show in it."""

    start: date
    end: date
    through: date  # the last day committed for everyone shown
    known: date  # the first day of the month from which every person shown has rows


@dataclass(frozen=True, slots=True)
class Rows:
    """The rows of a view, oldest first, and what tells how far they can be read."""

    rows: tuple[Converted, ...]
    days: tuple[date, ...]  # of the rows, in their order
    through: date | None  # the last day committed for everyone shown; None: nothing is
    known: date | None  # the first day of the month from which every person shown has rows

    def between(self, first: date, last: date) -> Sequence[Converted]:
        """The rows from `first` to `last`, both included."""
        return self.rows[bisect_left(self.days, first) : bisect_right(self.days, last)]

    def span(self, first: date | None, last: date | None) -> Span | None:
        """The range asked for, its ends filled in; None when there is nothing to show, or
        when it ends before it starts."""
        if self.through is None or self.known is None:
            return None
        start, end = first or self.rows[0].day, last or self.through
        return Span(start, end, self.through, self.known) if start <= end else None


def rows(
    conn: sqlite3.Connection,
    owner: str | None,
    currency: str,
    wanted: Callable[[Counted], bool],
) -> Rows:
    """The counted rows of `owner` (of everyone) that are `wanted`, converted into
    `currency`."""
    found: dict[str, list[Counted]] = defaultdict(list)
    for row in cashflow.counted(conn, owner):
        if wanted(row):
            found[row.currency].append(row)
    converted: list[Converted] = []
    for native, members in found.items():
        rates = rates_on(conn, [row.day for row in members])
        amounts = convert([from_nano(row.amount_nano) for row in members], native, currency, rates)
        converted.extend(map(Converted, members, amounts))
    converted.sort(key=lambda each: each.day)
    firsts: dict[str, date] = {}
    for each in converted:
        firsts.setdefault(each.row.owner, each.day.replace(day=1))
    return Rows(
        tuple(converted),
        tuple(each.day for each in converted),
        through(conn, owner),
        max(firsts.values(), default=None),
    )


def periods(start: date, end: date, grouping: Grouping) -> list[str]:
    """The names of the periods from the one of `start` to the one of `end`."""
    names = []
    day = start
    while day <= end:
        names.append(cashflow.period(day, grouping))
        day = cashflow.days(names[-1])[1] + timedelta(days=1)
    return names


def window(span: Span, days: tuple[date, date]) -> tuple[date, date] | None:
    """The twelve months that end with a period, or with the last day committed when the
    period goes beyond it; None when no day of the period is committed, or when those months
    are not all known."""
    end = min(days[1], span.through)
    start = year_before(end) + timedelta(days=1)
    if end < days[0] or start < span.known:
        return None
    return start, end


def add_months(month: date, n: int) -> date:
    """The first day of the month `n` months after (before, below zero) that of `month`."""
    index = month.year * 12 + month.month - 1 + n
    return date(index // 12, index % 12 + 1, 1)


def year_before(day: date) -> date:
    """The same day a year earlier; 28 February for 29 February."""
    return day.replace(year=day.year - 1, day=min(day.day, 28 if day.month == 2 else day.day))


def through(conn: sqlite3.Connection, owner: str | None) -> date | None:
    """The last day committed for `owner`, or for everyone: the earliest of their last days."""
    lasts = [
        person.last
        for person in query.people(conn)
        if person.last is not None and owner in (None, person.name)
    ]
    return min(lasts, default=None)
