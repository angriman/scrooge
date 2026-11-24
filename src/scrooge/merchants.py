"""The merchants money was spent at: what the transactions page offers to cut its rows to.

A merchant is named as the cash flow names it (`query.merchant`), and only what the cash flow
counts as money spent names one, so that each name offered lists rows. Each comes with what
was spent there over the last twelve months, up to the last day committed, each row converted
at the rate of its own day: those spent at in that year first, the largest first, then the
others, the latest first. A name is the one last written; spaces and case are not told apart.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from scrooge import analysis, spending
from scrooge.analysis import CENT
from scrooge.networth import DEFAULT_CURRENCY


@dataclass(frozen=True, slots=True)
class Merchant:
    name: str  # as last written
    spent: Decimal  # over the twelve months, a refund lowering it
    count: int  # rows over the twelve months
    last: date  # of its last row, whenever it was


@dataclass(frozen=True, slots=True)
class Merchants:
    currency: str
    since: date | None  # the first day of the twelve months; None: nothing is committed
    through: date | None  # the last day committed for everyone shown
    merchants: tuple[Merchant, ...]


def run(
    conn: sqlite3.Connection, *, owner: str | None = None, currency: str = DEFAULT_CURRENCY
) -> Merchants:
    """Every merchant `owner` (everyone) spent money at, with what was spent there over the
    last twelve months in `currency`. Raises `query.QueryError` for an owner the database does
    not have."""
    found = analysis.rows(conn, owner, currency, spending.is_expense)
    if found.through is None:
        return Merchants(currency, None, None, ())
    since = analysis.year_before(found.through) + timedelta(days=1)
    names: dict[str, str] = {}
    lasts: dict[str, date] = {}
    sums: dict[str, tuple[int, Decimal]] = {}
    for each in found.rows:  # oldest first: the last name written stays
        name = spending.merchant(each.row)
        key = name.casefold()
        names[key], lasts[key] = name, each.day
        if since <= each.day <= found.through:
            count, total = sums.get(key, (0, Decimal(0)))
            sums[key] = (count + 1, total - each.amount)
    merchants = [
        Merchant(names[key], total.quantize(CENT), count, lasts[key])
        for key, (count, total) in ((key, sums.get(key, (0, Decimal(0)))) for key in names)
    ]
    merchants.sort(
        key=lambda merchant: (
            merchant.count == 0,
            -merchant.spent,
            -merchant.last.toordinal(),
            merchant.name.casefold(),
        )
    )
    return Merchants(currency, since, found.through, tuple(merchants))
