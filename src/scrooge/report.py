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

from scrooge import analysis, cashflow, income, paidby, query, savings, spending
from scrooge.cashflow import Grouping
from scrooge.networth import DEFAULT_CURRENCY

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
    paid_by: paidby.PaidBy | None  # who paid, for both people together only


def run(  # noqa: PLR0913  # the range, whose, how grouped, in what
    conn: sqlite3.Connection,
    *,
    first: date | None = None,
    last: date | None = None,
    owner: str | None = None,
    grouping: Grouping = Grouping.MONTH,
    currency: str = DEFAULT_CURRENCY,
) -> Report:
    """The three views of `owner` (of everyone) from `first` to `last`, in `currency`; for
    everyone, who paid too."""
    query.check_names(conn, query.Filter(owner=owner))
    through = analysis.through(conn, owner)
    earliest = analysis.since(conn, owner)
    if first is None and last is None and through is not None:
        first = analysis.add_months(through, -11)
    if first is not None and earliest is not None:
        first = max(first, earliest)
    counted = cashflow.counted(conn, owner)
    prepared = analysis.prepare(conn, counted, currency, through)
    return Report(
        savings.run(
            conn,
            first=first,
            last=last,
            owner=owner,
            grouping=grouping,
            currency=currency,
            prepared=prepared,
        ),
        spending.run(
            conn,
            first=first,
            last=last,
            owner=owner,
            grouping=grouping,
            currency=currency,
            prepared=prepared,
        ),
        income.run(
            conn,
            first=first,
            last=last,
            owner=owner,
            grouping=grouping,
            currency=currency,
            prepared=prepared,
        ),
        cashflow.run(conn, first=first, last=last, owner=owner, grouping=grouping, rows=counted),
        _shortcuts(earliest, through),
        None
        if owner
        else paidby.run(conn, first=first, last=last, currency=currency, prepared=prepared),
    )


def _shortcuts(earliest: date | None, through: date | None) -> tuple[Shortcut, ...]:
    if through is None or earliest is None:
        return ()
    start = earliest.year
    years = [str(year) for year in range(through.year, max(start, through.year - 2) - 1, -1)]
    return (
        Shortcut(LAST_TWELVE, "", ""),
        *(Shortcut(year, year, year) for year in years),
        Shortcut("All", str(start), ""),
    )
