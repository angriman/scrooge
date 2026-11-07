"""Everything the analysis page shows for one range: what was saved, where the money went,
where income came from (plan decision 34) and, folded, income against expenses in each
currency (the cash flow, merged in 2026-11-07, decision 35).

Without a range it is the last twelve months: those up to the month of the last day
committed, that one included (agreed 2026-11-07). Shortcuts name the other ranges people
look at most: the calendar years of the statements, and all of them.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from finanze import analysis, cashflow, income, query, savings, spending
from finanze.cashflow import Grouping
from finanze.networth import DEFAULT_CURRENCY

LAST_TWELVE = "Last 12 months"


@dataclass(frozen=True, slots=True)
class Shortcut:
    label: str
    first: str  # what the From field takes; "" for none
    last: str  # and the To field


@dataclass(frozen=True, slots=True)
class Report:
    savings: savings.Savings
    spending: spending.Spending
    income: income.Income
    cashflow: cashflow.CashFlow  # in each currency as stored: nothing is converted
    shortcuts: tuple[Shortcut, ...]


def run(  # noqa: PLR0913  # the range, whose, how grouped, in what
    conn: sqlite3.Connection,
    *,
    first: date | None = None,
    last: date | None = None,
    owner: str | None = None,
    grouping: Grouping = Grouping.MONTH,
    currency: str = DEFAULT_CURRENCY,
) -> Report:
    """The three views of `owner` (of everyone) from `first` to `last`, in `currency`."""
    query.check_names(conn, query.Filter(owner=owner))
    through = analysis.through(conn, owner)
    if first is None and last is None and through is not None:
        first = analysis.add_months(through, -11)
    return Report(
        savings.run(
            conn, first=first, last=last, owner=owner, grouping=grouping, currency=currency
        ),
        spending.run(
            conn, first=first, last=last, owner=owner, grouping=grouping, currency=currency
        ),
        income.run(conn, first=first, last=last, owner=owner, grouping=grouping, currency=currency),
        cashflow.run(conn, first=first, last=last, owner=owner, grouping=grouping),
        _shortcuts(conn, owner, through),
    )


def _shortcuts(
    conn: sqlite3.Connection, owner: str | None, through: date | None
) -> tuple[Shortcut, ...]:
    if through is None:
        return ()
    firsts = [p.first for p in query.people(conn) if p.first and owner in (None, p.name)]
    start = min(firsts).year
    years = [str(year) for year in range(through.year, max(start, through.year - 2) - 1, -1)]
    return (
        Shortcut(LAST_TWELVE, "", ""),
        *(Shortcut(year, year, year) for year in years),
        Shortcut("All", str(start), ""),
    )
