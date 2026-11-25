"""What each account holds now, set out as a spreadsheet of accounts has it: its cash in each
currency it is kept in, and its securities apart, in one figure; and what it held at the end
of each of the last twelve months, for a small line of its own.

Everything comes from what `networth.value` worked out, as the account's balance does
(`networth.balances`): a page values everything once and reads this from it. The parts are
converted at the last day's rates, as the balance is, so they add up to it but for a cent of
rounding; each month-end at the rates of its own day.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge import networth
from scrooge.storage import store

MONTHS = 12  # month-ends kept for the line of an account's balance
_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Cash:
    """The cash an account keeps in one currency, on the last day. `asset` says what kind of
    cash it is, as net worth counts it: cash (`networth.CASH`), what a card owes or is owed
    (`CARD`), the cash inside a pillar 3a (`PENSION`) or what a pension fund's certificates
    state (`PILLAR2`)."""

    asset: str
    currency: str
    amount: Decimal  # in `currency`, to the cent
    converted: Decimal  # in the currency asked for, to the cent


@dataclass(frozen=True, slots=True)
class MonthEnd:
    """What an account held on the last day of a month, or on the last day there is while the
    month is not over, in the currency asked for."""

    day: date
    balance: Decimal  # to the cent


@dataclass(frozen=True, slots=True)
class Holds:
    """What one account holds now, by part, and its balance at the last month-ends."""

    balance: Decimal  # all of it in the currency asked for, to the cent: `networth.balances`
    cash: tuple[Cash, ...]  # the largest first, in the currency asked for
    securities: Decimal | None  # what its securities are worth together; None: it holds none
    months: tuple[MonthEnd, ...]  # oldest first, from the first it held anything; at most MONTHS


def ends(days: Sequence[date]) -> list[int]:
    """Where in `days` each month ends: on its last day, or the last day there is."""
    return [
        index
        for index, day in enumerate(days)
        if index == len(days) - 1 or days[index + 1].month != day.month
    ]


def of(
    conn: sqlite3.Connection, valued: networth.Valued | None, currency: str
) -> dict[tuple[str, str], Holds]:
    """What every account of `valued` holds, in `currency`; by its owner and name, as
    `networth.balances` has it, whose balances this gives too: a page that shows both reads the
    rates once. Raises `networth.MissingRateError` as that does."""
    if valued is None:
        return {}
    names = store.account_names(conn)
    kept = ends(valued.days)[-MONTHS:]
    days = [valued.days[index] for index in kept]
    # Only the month-ends are converted: the rates are carried to those days alone. The last
    # of them is the last day, whose rates the balance is converted at.
    rates = networth.rates_on(conn, days)
    cash: dict[tuple[str, str], dict[tuple[str, str], list[Decimal]]] = defaultdict(dict)
    securities: dict[tuple[str, str], Decimal] = {}
    history: dict[tuple[str, str], list[Decimal]] = {}
    for series in valued.series:
        key = series.owner, names[series.account_id]
        converted = networth.convert(
            [series.values[index] for index in kept], series.currency, currency, rates
        )
        added = history.setdefault(key, [Decimal(0)] * len(kept))
        history[key] = [was + value for was, value in zip(added, converted, strict=True)]
        if series.instrument_id is None:
            kind = series.asset, series.currency
            native, into = cash[key].get(kind, [Decimal(0), Decimal(0)])
            cash[key][kind] = [native + series.values[-1], into + converted[-1]]
        elif series.values[-1]:
            securities[key] = securities.get(key, Decimal(0)) + converted[-1]
    found: dict[tuple[str, str], Holds] = {}
    for key, balances in history.items():
        start = next((at for at, value in enumerate(balances) if value), len(balances))
        found[key] = Holds(
            balances[-1].quantize(_CENT),
            tuple(
                sorted(
                    (
                        Cash(asset, name, native.quantize(_CENT), into.quantize(_CENT))
                        for (asset, name), (native, into) in cash[key].items()
                    ),
                    key=lambda each: (-each.converted, each.currency, each.asset),
                )
            ),
            None if key not in securities else securities[key].quantize(_CENT),
            tuple(
                MonthEnd(day, value.quantize(_CENT))
                for day, value in zip(days[start:], balances[start:], strict=True)
            ),
        )
    return found
