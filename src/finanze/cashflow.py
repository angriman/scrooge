"""Income against expenses, period by period: what the cash-flow page shows.

Only committed months are read. A row counts on the day it really happened, on the side of
its category: `entrate` is income, `uscite` an expense, so a refund lowers the expenses of its
category. Moves between a person's own accounts and currency exchanges are never counted;
what the two people pay each other is counted for one person and left out for both together
(plan 7.1). Amounts stay in their own currency: nothing is converted here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from finanze import query
from finanze.models import Kind
from finanze.storage.money import from_nano

INCOME, EXPENSES = "entrate", "uscite"
# Money that only changes place: stored and listed, never income or an expense.
_NOT_COUNTED = (Kind.TRANSFER, Kind.FX, Kind.INTERNAL)


class Grouping(StrEnum):
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


@dataclass(frozen=True, slots=True)
class Line:
    """What one top-level category adds up to in one period and one currency."""

    period: str
    currency: str
    side: str  # INCOME or EXPENSES
    category: str
    count: int
    total: Decimal  # signed: expenses are negative


@dataclass(frozen=True, slots=True)
class Balance:
    """One period in one currency. `expenses` is negative, as stored."""

    period: str
    currency: str
    income: Decimal
    expenses: Decimal

    @property
    def net(self) -> Decimal:
        return self.income + self.expenses


@dataclass(frozen=True, slots=True)
class CashFlow:
    lines: tuple[Line, ...]  # by period, currency, side (income first) and category
    balances: tuple[Balance, ...]  # by period and currency; periods without rows are absent


def period(day: date, grouping: Grouping) -> str:
    """`2025-W18`, `2025-05`, `2025-Q2` or `2025`. A week belongs to its ISO year."""
    if grouping is Grouping.WEEK:
        year, week, _ = day.isocalendar()
        return f"{year}-W{week:02}"
    if grouping is Grouping.MONTH:
        return f"{day:%Y-%m}"
    if grouping is Grouping.QUARTER:
        return f"{day.year}-Q{(day.month + 2) // 3}"
    return f"{day.year}"


def run(
    conn: sqlite3.Connection,
    *,
    first: date | None = None,
    last: date | None = None,
    owner: str | None = None,
    grouping: Grouping = Grouping.MONTH,
) -> CashFlow:
    """Income and expenses from `first` to `last`, both included, of `owner` or of everyone."""
    query.check_names(conn, query.Filter(owner=owner))
    kinds = ", ".join("?" * len(_NOT_COUNTED))
    found = conn.execute(
        "SELECT date, currency, category_side, coalesce(category_parent, category), amount_nano "  # noqa: S608  # only "?" marks are interpolated
        f"FROM committed_transactions WHERE kind NOT IN ({kinds}) AND category_side <> 'transfer' "
        "AND date >= coalesce(?, date) AND date <= coalesce(?, date) "
        # One person: their rows. Everyone: all but what the two paid each other.
        "AND CASE WHEN ? IS NULL THEN counterparty_owner IS NULL ELSE owner = ? END",
        (
            *(kind.value for kind in _NOT_COUNTED),
            first and first.isoformat(),
            last and last.isoformat(),
            owner,
            owner,
        ),
    ).fetchall()
    sums: dict[tuple[str, str, str, str], tuple[int, int]] = {}
    for day, currency, side, category, nano in found:
        # INCOME sorts before EXPENSES: "entrate" < "uscite".
        key = (period(date.fromisoformat(day), grouping), currency, side, category)
        count, total = sums.get(key, (0, 0))
        sums[key] = (count + 1, total + nano)
    lines = tuple(
        Line(*key, count, from_nano(total)) for key, (count, total) in sorted(sums.items())
    )
    sides: dict[tuple[str, str], dict[str, Decimal]] = {}
    for line in lines:
        totals = sides.setdefault((line.period, line.currency), {})
        totals[line.side] = totals.get(line.side, Decimal(0)) + line.total
    balances = tuple(
        Balance(*key, totals.get(INCOME, Decimal(0)), totals.get(EXPENSES, Decimal(0)))
        for key, totals in sides.items()
    )
    return CashFlow(lines, balances)
