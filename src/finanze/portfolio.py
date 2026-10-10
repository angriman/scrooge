"""What each person holds on the last day net worth is computed for: the holdings page.

The figures are those of `networth`, not computed again: the same series, of which only the
last day is read, at that day's rate. A position is one instrument in one account: shares,
ETFs, fund units and bills. Cash, the card and a second-pillar fund are not positions.
Nothing here is stored and nothing here writes.

**The two pages never disagree.** Net worth adds up what a person holds of one kind of asset
and rounds the sum; rounding each position on its own could miss that sum by a cent or
more. So the positions of one person and one kind share out the rounded sum between them:
each is rounded down to the cent, and the cents then missing go to those that lost most by
it. Each `Position.value` is thus within a cent of its exact value, and for ETFs, stocks
and bills they add up to exactly the last value of the `networth.Line` of that person and
kind. `Pillar 3a` is the one line with cash in it: there the fund units add up to the line
less the cash of the pension account, to the cent. `value_native` is in the instrument's
own currency and has no sum to meet: it is simply rounded.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, Decimal

from finanze import networth

_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Position:
    """One instrument in one account at the end of `Portfolio.day`."""

    owner: str
    account: str
    symbol: str
    name: str  # of the instrument; empty when none is stored
    type: str  # of the instrument: etf, stock, fund, bill or bond
    asset: str  # the line of net worth it is part of: `networth.ETFS`, `STOCKS`, ...
    currency: str  # of the instrument
    quantity: Decimal  # shares or units; the face value of a bill
    price: Decimal | None  # the close it is valued at; None for a bill, which has none
    price_day: date | None  # the day that close is from: before `Portfolio.day` when stale
    price_is_trade: bool  # no close is stored for `price_day`: the price of a trade stands in
    maturity: date | None  # bills and bonds
    value_native: Decimal  # in `currency`, to the cent
    value: Decimal  # in `Portfolio.currency`, to the cent (see the module on its rounding)


@dataclass(frozen=True, slots=True)
class Portfolio:
    currency: str
    day: date | None  # the last day of `networth.run` for the same people; None: no such day
    # By owner, kind of asset in the order of `networth.ASSETS`, value (largest first),
    # symbol and account.
    positions: tuple[Position, ...]
    notes: tuple[str, ...]  # those of net worth that are about what is held


def _shared_out(exact: Sequence[Decimal]) -> list[Decimal]:
    """`exact` to the cent, so that the parts add up to their rounded sum."""
    whole = sum(exact, Decimal(0)).quantize(_CENT)
    parts = [value.quantize(_CENT, rounding=ROUND_FLOOR) for value in exact]
    missing = int((whole - sum(parts, Decimal(0))) / _CENT)
    lost_most = sorted(range(len(exact)), key=lambda n: parts[n] - exact[n])
    for n in lost_most[:missing]:
        parts[n] += _CENT
    return parts


def run(
    conn: sqlite3.Connection,
    *,
    owner: str | None = None,
    currency: str = networth.DEFAULT_CURRENCY,
) -> Portfolio:
    """What `owner`, or everyone, holds at the end of the last day of `networth.run`.

    Only what is worth something that day is listed: what was sold is not, nor what net
    worth cannot value (a note then says why).
    """
    valued = networth.value(conn, owner)
    if valued is None:
        return Portfolio(currency, None, (), ())
    last = valued.days[-1:]
    rates = networth.rates_on(conn, last)
    held = [s for s in valued.series if s.instrument_id is not None and s.values[-1]]
    # Added up in the order net worth adds them up, so that the sums are the same.
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for n, series in enumerate(held):
        groups[series.owner, series.asset].append(n)
    values = [Decimal(0)] * len(held)
    for members in groups.values():
        exact = [
            networth.convert(held[n].values[-1:], held[n].currency, currency, rates)[0]
            for n in members
        ]
        for n, value in zip(members, _shared_out(exact), strict=True):
            values[n] = value
    accounts = dict(conn.execute("SELECT id, name FROM accounts").fetchall())
    instruments = {
        row[0]: row[1:]
        for row in conn.execute("SELECT id, symbol, name, type, maturity FROM instruments")
    }
    positions: list[Position] = []
    for series, value in zip(held, values, strict=True):
        symbol, name, kind, maturity = instruments[series.instrument_id]
        quote = series.quote
        positions.append(
            Position(
                owner=series.owner,
                account=accounts[series.account_id],
                symbol=symbol,
                name=name,
                type=kind,
                asset=series.asset,
                currency=series.currency,
                quantity=series.quantity or Decimal(0),
                price=None if quote is None else quote.close,
                price_day=None if quote is None else quote.day,
                price_is_trade=quote is not None and quote.traded,
                maturity=maturity and date.fromisoformat(maturity),
                value_native=series.values[-1].quantize(_CENT),
                value=value,
            )
        )
    positions.sort(
        key=lambda p: (p.owner, networth.ASSETS.index(p.asset), -p.value, p.symbol, p.account)
    )
    return Portfolio(currency, valued.days[-1], tuple(positions), valued.security_notes)
