"""The merchants money was spent at: what the transactions page offers to cut its rows to.

A merchant is named as the cash flow names it (`query.merchant`), and only what the cash flow
counts as money spent names one, so that each name offered lists rows. Those spent at over the
last twelve months, up to the last day committed, come with what was spent there, each row
converted at the rate of its own day, the largest first; only the rows of those months are
converted. The others follow, the latest first. A name is the one last written; spaces and
case are not told apart.

The figures go with the SHOWN first merchants only, as many as the chip lists at once; the
rest are sent as names, so that typing can still find any of them.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from scrooge import analysis, cashflow, spending
from scrooge.analysis import CENT
from scrooge.cashflow import Counted
from scrooge.networth import DEFAULT_CURRENCY, convert, rates_on
from scrooge.storage.money import from_nano

SHOWN = 50  # sent with their figures: as many as the chip lists before anything is typed


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
    merchants: tuple[Merchant, ...]  # the SHOWN first
    others: tuple[str, ...]  # the names of the rest, in the same order


def run(
    conn: sqlite3.Connection, *, owner: str | None = None, currency: str = DEFAULT_CURRENCY
) -> Merchants:
    """Every merchant `owner` (everyone) spent money at, with what was spent there over the
    last twelve months in `currency`. Raises `query.QueryError` for an owner the database does
    not have, `networth.MissingRateError` for a currency of those months without rates."""
    spent = [row for row in cashflow.counted(conn, owner) if spending.is_expense(row)]
    through = analysis.through(conn, owner)
    if through is None:
        return Merchants(currency, None, None, (), ())
    since = analysis.year_before(through) + timedelta(days=1)
    names: dict[str, str] = {}
    lasts: dict[str, date] = {}
    for row in spent:  # oldest first: the last name written stays
        name = spending.merchant(row)
        names[name.casefold()], lasts[name.casefold()] = name, row.day
    sums = _sums(conn, [row for row in spent if since <= row.day <= through], currency)
    found = [
        Merchant(names[key], total.quantize(CENT), count, lasts[key])
        for key, (count, total) in ((key, sums.get(key, (0, Decimal(0)))) for key in names)
    ]
    found.sort(
        key=lambda merchant: (
            merchant.count == 0,
            -merchant.spent,
            -merchant.last.toordinal(),
            merchant.name.casefold(),
        )
    )
    return Merchants(
        currency,
        since,
        through,
        tuple(found[:SHOWN]),
        tuple(merchant.name for merchant in found[SHOWN:]),
    )


def _sums(
    conn: sqlite3.Connection, rows: list[Counted], currency: str
) -> dict[str, tuple[int, Decimal]]:
    """A merchant's name, folded -> how many of `rows` are theirs and what was spent in them,
    in `currency`, each row at the rate of its own day."""
    by_currency: dict[str, list[Counted]] = defaultdict(list)
    for row in rows:
        by_currency[row.currency].append(row)
    sums: dict[str, tuple[int, Decimal]] = {}
    for native, members in by_currency.items():
        rates = rates_on(conn, [row.day for row in members])
        amounts = convert([from_nano(row.amount_nano) for row in members], native, currency, rates)
        for row, amount in zip(members, amounts, strict=True):
            key = spending.merchant(row).casefold()
            count, total = sums.get(key, (0, Decimal(0)))
            sums[key] = (count + 1, total - amount)
    return sums
