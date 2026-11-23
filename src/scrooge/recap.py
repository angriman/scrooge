"""One month at a glance, for the first page: how net worth moved over it, and why.

Net worth on the eve of the month and on its last day (the last day there is, while the
month is not over); what came in and what was spent, as `savings` counts them (income less
spending is what was saved); and the rest of the move, `other`: what markets did to what is
held, and whatever moved net worth without being income or spending on a statement, such as
a pension fund's contributions taken from the salary before it is paid, or exchange rates.
What accounts brought into view when they first counted within the month (`arrived`,
`networth.NetWorth.arrivals`) is apart: it was neither earned nor made.
Spending is by category, each against its usual month (`spending`: the median month of the
twelve before).

Which month: the one asked for, else the last month every monthly account's statements
cover (`months.covered`), so that a month still missing a statement is not shown as if it
were complete. Nothing here is stored and nothing here writes.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from scrooge import months, networth, savings, spending
from scrooge.cashflow import Grouping
from scrooge.extractors.common import month_end

_TENTH = Decimal("0.1")


@dataclass(frozen=True, slots=True)
class Category:
    """What was spent in one top-level category in the month, and in its usual month."""

    name: str
    spent: Decimal
    usual: Decimal | None  # None: no month before it to compare with

    @property
    def against_usual(self) -> Decimal | None:
        """How much more (less, when negative) was spent than usual, in percent of the usual
        month; None when nothing is usually spent there."""
        if not self.usual or self.usual <= 0:
            return None
        return ((self.spent - self.usual) * 100 / self.usual).quantize(_TENTH)


@dataclass(frozen=True, slots=True)
class Recap:
    currency: str
    month: date  # its first day
    last: date  # its last day, or the last day there is while it is not over
    before: Decimal | None  # net worth on the eve of the month; None: before the history
    after: Decimal  # net worth on `last`
    income: Decimal
    spent: Decimal  # money out, less what came back
    arrived: Decimal  # what accounts held on the day they first counted, within the month
    rate: Decimal | None  # what was saved, as a part of income; None without income
    rate_year: Decimal | None  # the same, over the twelve months ending with the month
    categories: tuple[Category, ...]  # the largest first
    missing: tuple[months.Coverage, ...]  # monthly accounts whose statements end before `last`
    earlier: date | None  # the month before, when the history reaches it
    later: date | None  # the month after, when there is one

    @property
    def over(self) -> bool:
        """Whether the month has ended: until then, its figures are so far."""
        return self.last == month_end(self.month)

    @property
    def saved(self) -> Decimal:
        return self.income - self.spent

    @property
    def moved(self) -> Decimal | None:
        """How much net worth moved over the month; None when the history starts within it."""
        return None if self.before is None else self.after - self.before

    @property
    def other(self) -> Decimal | None:
        """What moved net worth besides what was saved and what arrived: markets, and what is
        not income or spending on a statement."""
        moved = self.moved
        return None if moved is None else moved - self.saved - self.arrived


def _previous(month: date) -> date:
    return (month - timedelta(days=1)).replace(day=1)


def _next(month: date) -> date:
    return month_end(month) + timedelta(days=1)


def _complete(covered: date | None, latest: date) -> date:
    """The last month every monthly account covers to its end; `latest` when nothing does."""
    if covered is None:
        return latest
    month = covered.replace(day=1)
    return month if covered == month_end(covered) else _previous(month)


def _on(worth: networth.NetWorth, day: date) -> Decimal | None:
    """Net worth on `day`; None before the history starts."""
    index = (day - worth.days[0]).days
    return worth.total[index] if index >= 0 else None


def run(
    conn: sqlite3.Connection,
    *,
    owner: str | None = None,
    currency: str = networth.DEFAULT_CURRENCY,
    month: date | None = None,
) -> Recap | None:
    """The month of `month` (any day of it), else the last complete one, for `owner` or for
    everyone, in `currency`; None when nothing is committed. A month outside the history is
    the nearest one in it."""
    worth = networth.run(conn, owner=owner, currency=currency)
    if not worth.days:
        return None
    lines = months.coverage(conn, owner)
    first, latest = worth.days[0].replace(day=1), worth.days[-1].replace(day=1)
    asked = month.replace(day=1) if month else _complete(months.covered(lines), latest)
    shown = min(max(asked, first), latest)
    last = min(month_end(shown), worth.days[-1])
    saved = savings.run(
        conn, first=shown, last=last, owner=owner, grouping=Grouping.MONTH, currency=currency
    )
    spent = spending.run(
        conn, first=shown, last=last, owner=owner, grouping=Grouping.MONTH, currency=currency
    )
    totals = saved.totals or savings.Totals(Decimal(0), Decimal(0), Decimal(0))
    return Recap(
        currency=currency,
        month=shown,
        last=last,
        before=_on(worth, shown - timedelta(days=1)),
        after=worth.total[(last - worth.days[0]).days],
        income=totals.income,
        spent=totals.spent,
        arrived=sum(
            worth.arrived[(shown - worth.days[0]).days : (last - worth.days[0]).days + 1],
            Decimal(0),
        ),
        rate=totals.rate,
        rate_year=saved.periods[-1].rate if saved.periods else None,
        categories=tuple(
            Category(category.name, category.spent, category.usual) for category in spent.categories
        ),
        missing=tuple(
            line
            for line in lines
            if months.monthly(line) and line.committed and line.committed < last
        ),
        earlier=_previous(shown) if shown > first else None,
        later=_next(shown) if shown < latest else None,
    )
