"""What was saved: income less what was spent, the savings rate, and the analysis page's
headline figures (plan decision 34).

Income is the income view's, spending the spending view's: in one person's view what the
other person paid lowers what was spent, so that the two people's savings add up to the
family's. What a pension fund takes from the salary before it is paid is not seen: salary
is counted as it arrives.

The savings rate of a period is that of the twelve months ending with it, on the same terms
as the employer's share of income.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finanze import analysis, cashflow, income, spending
from finanze.analysis import CENT, Converted
from finanze.cashflow import Counted, Grouping
from finanze.networth import DEFAULT_CURRENCY

_SHARE = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class Totals:
    income: Decimal
    spent: Decimal  # money out, less what came back
    work: Decimal  # the income from work

    @property
    def saved(self) -> Decimal:
        return self.income - self.spent

    @property
    def rate(self) -> Decimal | None:
        """What was saved, as a part of income; None without income."""
        return (self.saved / self.income).quantize(_SHARE) if self.income > 0 else None

    @property
    def from_work(self) -> Decimal | None:
        return (self.work / self.income).quantize(_SHARE) if self.income > 0 else None


@dataclass(frozen=True, slots=True)
class Period:
    name: str
    first: date
    last: date
    totals: Totals
    rate: Decimal | None  # the savings rate of the twelve months ending with it


@dataclass(frozen=True, slots=True)
class Savings:
    currency: str
    first: date | None  # the range shown; None: nothing to show
    last: date | None
    totals: Totals | None  # of the range
    year_before: Totals | None  # of the same range a year earlier, when it is known
    periods: tuple[Period, ...]
    through: date | None


def _counts(row: Counted) -> bool:
    return income.is_income(row) or spending.is_spent(row)


def _totals(rows: Sequence[Converted]) -> Totals:
    earned = spent = work = Decimal(0)
    for each in rows:
        if income.is_income(each.row):
            earned += each.amount
            if income.group(each.row) == income.LAVORO:
                work += each.amount
        else:
            spent -= each.amount
    return Totals(earned.quantize(CENT), spent.quantize(CENT), work.quantize(CENT))


def run(  # noqa: PLR0913  # the range, whose, how grouped, in what
    conn: sqlite3.Connection,
    *,
    first: date | None = None,
    last: date | None = None,
    owner: str | None = None,
    grouping: Grouping = Grouping.MONTH,
    currency: str = DEFAULT_CURRENCY,
) -> Savings:
    """What was earned, spent and saved from `first` to `last`, both included, by `owner`
    or by everyone, in `currency`, ranges filled in as for income."""
    found = analysis.rows(conn, owner, currency, _counts)
    span = found.span(first, last)
    if span is None:
        return Savings(currency, None, None, None, None, (), found.through)
    periods = []
    for name in analysis.periods(span.start, span.end, grouping):
        days = cashflow.days(name)
        inside = found.between(max(days[0], span.start), min(days[1], span.end))
        window = analysis.window(span, days)
        rate = None if window is None else _totals(found.between(*window)).rate
        periods.append(Period(name, *days, _totals(inside), rate))
    before = analysis.year_before(span.start), analysis.year_before(span.end)
    return Savings(
        currency,
        span.start,
        span.end,
        _totals(found.between(span.start, span.end)),
        _totals(found.between(*before)) if before[0] >= span.known else None,
        tuple(periods),
        span.through,
    )
