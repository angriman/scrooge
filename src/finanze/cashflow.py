"""Income against expenses, period by period: what the cash-flow page shows.

Only committed months are read. A row counts on the day it really happened, on the side of
its category: `entrate` is income, `uscite` an expense, so a refund lowers the expenses of its
category. Buying and selling securities and the results of trading are neither: they are
investing, kept apart so that a bill bought and repaid does not swamp the rest (decided
2026-10-08). Moves between a person's own accounts and currency exchanges are never counted;
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
from finanze.extractors.common import month_end
from finanze.models import Kind
from finanze.storage.money import from_nano

INCOME, EXPENSES, INVESTING = "entrate", "uscite", "investing"
# The top-level categories, on either side of the taxonomy, that are investing.
_INVESTING = frozenset({"Investimenti", "Trading"})
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
    side: str  # INCOME, EXPENSES or INVESTING
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
    investing: Decimal  # what went into securities (negative) or came back out of them

    @property
    def net(self) -> Decimal:
        """Income less expenses: what was left to save or invest."""
        return self.income + self.expenses


@dataclass(frozen=True, slots=True)
class CashFlow:
    lines: tuple[Line, ...]  # by period, currency, side (income first) and category
    balances: tuple[Balance, ...]  # by period and currency; periods without rows are absent
    totals: tuple[Line, ...]  # the lines of the whole range: their period is ""


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


def days(name: str) -> tuple[date, date]:
    """The first and last day of a period named by `period`."""
    year, _, part = name.partition("-")
    if part.startswith("W"):
        return (
            date.fromisocalendar(int(year), int(part[1:]), 1),
            date.fromisocalendar(int(year), int(part[1:]), 7),
        )
    if part.startswith("Q"):
        last = int(part[1:]) * 3
        return date(int(year), last - 2, 1), month_end(date(int(year), last, 1))
    return query.span(name)


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
        "SELECT date, currency, category_side, coalesce(category_parent, category), category, "  # noqa: S608  # only "?" marks are interpolated
        f"amount_nano FROM committed_transactions WHERE kind NOT IN ({kinds}) "
        "AND category_side <> 'transfer' "
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
    for day, currency, side, top, leaf, nano in found:
        # They sort as they are shown: "entrate" < "investing" < "uscite". Investing is named
        # by the leaf, which is the same whether money went in or came out.
        where, category = (INVESTING, leaf) if top in _INVESTING else (side, top)
        for name in (period(date.fromisoformat(day), grouping), ""):
            key = (name, currency, where, category)
            count, total = sums.get(key, (0, 0))
            sums[key] = (count + 1, total + nano)
    every = [Line(*key, count, from_nano(total)) for key, (count, total) in sorted(sums.items())]
    lines = tuple(line for line in every if line.period)
    sides: dict[tuple[str, str], dict[str, Decimal]] = {}
    for line in lines:
        totals = sides.setdefault((line.period, line.currency), {})
        totals[line.side] = totals.get(line.side, Decimal(0)) + line.total
    balances = tuple(
        Balance(*key, *(totals.get(side, Decimal(0)) for side in (INCOME, EXPENSES, INVESTING)))
        for key, totals in sides.items()
    )
    return CashFlow(lines, balances, tuple(line for line in every if not line.period))
