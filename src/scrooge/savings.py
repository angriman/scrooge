"""What was saved: income less what was spent, the savings rate, and the analysis page's
headline figures (plan decision 34).

Income is the income view's, spending the spending view's: in one person's view what the
other person paid lowers what was spent, so that the two people's savings add up to the
family's. What a pension fund takes from the salary before it is paid is not seen: salary
is counted as it arrives.

The savings rate of a period is that of the twelve months ending with it, on the same terms
as the employer's share of income. Beside the rate on income there is the rate on salary,
the income from work alone: what was saved as a part of what work paid.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge import analysis, income, spending
from scrooge.analysis import CENT, Converted
from scrooge.cashflow import Counted, Grouping
from scrooge.networth import DEFAULT_CURRENCY


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
        return analysis.share(self.saved, self.income)

    @property
    def rate_salary(self) -> Decimal | None:
        """What was saved, as a part of the salary (the income from work); None without one."""
        return analysis.share(self.saved, self.work)

    @property
    def from_work(self) -> Decimal | None:
        return analysis.share(self.work, self.income)


@dataclass(frozen=True, slots=True)
class Period:
    name: str
    first: date
    last: date
    totals: Totals
    rate: Decimal | None  # the savings rate of the twelve months ending with it
    rate_salary: Decimal | None  # the same, on the salary of those twelve months


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
    for name, days, inside in found.by_period(span, grouping):
        window = analysis.window(span, days)
        year = None if window is None else _totals(found.between(*window))
        periods.append(
            Period(
                name,
                *days,
                _totals(inside),
                None if year is None else year.rate,
                None if year is None else year.rate_salary,
            )
        )
    before = span.year_before()
    return Savings(
        currency,
        span.start,
        span.end,
        _totals(found.between(span.start, span.end)),
        None if before is None else _totals(found.between(*before)),
        tuple(periods),
        span.through,
    )
