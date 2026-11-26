"""What the views of the analysis page share (plan decision 34): the rows the cash-flow page
counts, each converted at the rate of its own day into one currency, and the range and the
periods they are shown over.

A range runs, unless asked otherwise, from the first row to the last day committed: for both
people together, the earlier of their last days. A comparison with the months before a day is
only made where every person shown has rows from the month of that day on.
"""

from __future__ import annotations

import calendar
import sqlite3
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from scrooge import cashflow, query
from scrooge.cashflow import Counted, Grouping
from scrooge.extractors.common import month_end
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

    def year_before(self) -> tuple[date, date] | None:
        """The same range a year earlier; None when those days are not all known."""
        start, end = year_before(self.start), year_before(self.end)
        return (start, end) if start >= self.known else None


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

    def within(self, span: Span, days: tuple[date, date]) -> Sequence[Converted]:
        """The rows of a period, first to last day, left to the range's ends where the period
        reaches beyond them."""
        return self.between(max(days[0], span.start), min(days[1], span.end))

    def by_period(
        self, span: Span, grouping: Grouping
    ) -> Iterator[tuple[str, tuple[date, date], Sequence[Converted]]]:
        """Each period of the range, oldest first: its name, its first and last day (whole,
        even where the range cuts it) and the rows of it that are in the range."""
        for name in periods(span.start, span.end, grouping):
            days = cashflow.days(name)
            yield name, days, self.within(span, days)

    def span(self, first: date | None, last: date | None) -> Span | None:
        """The range asked for, its ends filled in; None when there is nothing to show, or
        when it ends before it starts."""
        if self.through is None or self.known is None:
            return None
        start, end = first or self.rows[0].day, last or self.through
        return Span(start, end, self.through, self.known) if start <= end else None


def _from_converted(converted: Sequence[Converted], through_day: date | None) -> Rows:
    firsts: dict[str, date] = {}
    for each in converted:
        firsts.setdefault(each.row.owner, each.day.replace(day=1))
    return Rows(
        tuple(converted),
        tuple(each.day for each in converted),
        through_day,
        max(firsts.values(), default=None),
    )


def _convert_rows(
    conn: sqlite3.Connection, selected: Iterable[Counted], currency: str
) -> list[Converted]:
    found: dict[str, list[Counted]] = defaultdict(list)
    for row in selected:
        found[row.currency].append(row)
    converted: list[Converted] = []
    for native, members in found.items():
        rates = rates_on(conn, [row.day for row in members])
        amounts = convert([from_nano(row.amount_nano) for row in members], native, currency, rates)
        converted.extend(map(Converted, members, amounts))
    converted.sort(key=lambda each: each.day)
    return converted


@dataclass(frozen=True, slots=True)
class Prepared:
    """Counted rows already converted once into a display currency, for `report.run`."""

    converted: tuple[Converted, ...]
    through: date | None

    def select(self, wanted: Callable[[Counted], bool]) -> Rows:
        return _from_converted([each for each in self.converted if wanted(each.row)], self.through)


def prepare(
    conn: sqlite3.Connection,
    counted: Sequence[Counted],
    currency: str,
    through_day: date | None,
) -> Prepared:
    """Convert `counted` into `currency` once so several views can share it."""
    return Prepared(tuple(_convert_rows(conn, counted, currency)), through_day)


def rows(
    conn: sqlite3.Connection,
    owner: str | None,
    currency: str,
    wanted: Callable[[Counted], bool],
) -> Rows:
    """The counted rows of `owner` (of everyone) that are `wanted`, converted into
    `currency`."""
    selected = (row for row in cashflow.counted(conn, owner) if wanted(row))
    return _from_converted(_convert_rows(conn, selected, currency), through(conn, owner))


def usual(
    found: Rows, month: date, part: Callable[[Converted], tuple[str, Decimal] | None]
) -> dict[str, Decimal] | None:
    """What a usual month is before the one of `month`: for each name `part` gives a row,
    the median of what it gives in each of the twelve months before, to the cent (a month
    without any counts as nothing). `part` names a row and says what it adds, or leaves it
    out with None. None when those twelve months are not all known."""
    first = month.replace(day=1)
    starts = [add_months(first, n) for n in range(-12, 0)]
    if found.known is None or starts[0] < found.known:
        return None
    sums: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0)] * 12)
    for n, start in enumerate(starts):
        for each in found.between(start, month_end(start)):
            named = part(each)
            if named is not None:
                sums[named[0]][n] += named[1]
    return {name: median(values).quantize(CENT) for name, values in sums.items()}


def share(part: Decimal, total: Decimal) -> Decimal | None:
    """`part` as a part of `total`, not rounded: it is rounded once, where it is shown. None
    when `total` is not above zero."""
    return part / total if total > 0 else None


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


def months(start: date, end: date) -> int:
    """The calendar months from `start` to `end`, both included."""
    return (end.year - start.year) * 12 + end.month - start.month + 1


def year_before(day: date) -> date:
    """The same day a year earlier; the last day of a February for the last day of a
    February, whether or not either is a leap year's."""
    last = calendar.monthrange(day.year, day.month)[1]
    before = day.year - 1
    if day.month == 2 and day.day == last:
        return date(before, 2, calendar.monthrange(before, 2)[1])
    return day.replace(year=before)


def since(conn: sqlite3.Connection, owner: str | None) -> date | None:
    """The first day of the earliest month committed for `owner`, or for everyone."""
    firsts = [
        person.first.replace(day=1)
        for person in query.people(conn)
        if person.first is not None and owner in (None, person.name)
    ]
    return min(firsts, default=None)


def through(conn: sqlite3.Connection, owner: str | None) -> date | None:
    """The last day committed for `owner`, or for everyone: the earliest of their last days."""
    lasts = [
        person.last
        for person in query.people(conn)
        if person.last is not None and owner in (None, person.name)
    ]
    return min(lasts, default=None)
