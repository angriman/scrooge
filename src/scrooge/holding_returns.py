"""What each holding returned: over the past month, this year, the past year and since it was
bought, what its sales realised, and what it paid. The rows of the holdings view open to it.

**A holding's return is measured as an account's is** (`performance`), the shares being all
it holds: what they are worth each day is what net worth counts for them (the quantity held
at the day's close, converted at the day's rate), and the money put into them or taken out of
them is what was paid for them and what they were sold for, on the day the trade was booked.
Cash paid for shares comes at the start of its day and earns that day (from the price paid to
the close); what a sale brings goes at its end. Shares moved in or out in kind are valued at
the close of their day, or at what the statement says they were worth when no close is
known, and come at its end, as do the shares held when the history starts. **A dividend**,
less the tax withheld from it, is taken out of the holding at the end of the day it is
booked: the return is a total return, dividends included, and not only the change of the
price. A dividend is the holding's when it is booked in the account that holds it and its
counterparty names the instrument, as the income view groups them (`payouts`).

The return is the time-weighted one, in percent over the range, and the amount the gain:
the worth at the end, less that at the start, less what was put in. A range other than since
bought is shown only for a holding held through all of it; one bought since has no return
over it, rather than one over a part of it that would read as one over all of it. Since
bought runs from the first day anything was held, which is the day the history starts for
shares already held then. A day that starts with less than 100 francs in the holding counts
for nothing (`performance.FLOOR`), as for an account: a holding that never had that much has
no return at all, and is said to be too small for one.

**Realised** is what the sales brought less what the lots they closed cost, first in, first
out, each lot at the rate of the day it was bought and each sale at the rate of its own day,
as `portfolio` counts the cost of the lots still held; shares moved out in kind realise
nothing. Not known when the lots a sale closed are not known (the history starts with shares
already held, or they came in without a price); nothing when nothing was ever sold, which is
said apart from a sale that realised nothing. **Unrealised** is the gain of `portfolio`:
what is held less what its lots cost.

A holding is one instrument in every account together, and only the positions held on the
last day count: a position sold out in another account is not listed, and neither are its
returns. Nothing here is stored and nothing here writes.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from scrooge import networth
from scrooge.categories import DIVIDENDS, WITHHOLDING_TAX
from scrooge.networth import LotTrade, Series, rates_on
from scrooge.payouts import holding_of, named_instruments
from scrooge.performance import instrument_ranges, on_day
from scrooge.portfolio import Portfolio, Position, Split, lot_events, splits_through
from scrooge.storage import store
from scrooge.storage.bills import Event, fifo_costs
from scrooge.storage.money import from_nano, to_nano
from scrooge.storage.store import committed_or_typed

PERIODS = ("1m", "ytd", "1y", "all")  # `all`: since bought
_CENT = Decimal("0.01")
_NANO = Decimal("1e-9")  # what a lot's cost is kept to, as `storage.bills` keeps it
_Moved = tuple[date, str, Decimal, bool]  # as `performance.instrument_ranges` reads it
_Key = tuple[str, str, str, str, str]  # owner, account, symbol, currency, asset


@dataclass(frozen=True, slots=True)
class Period:
    """What a holding returned over one of PERIODS."""

    first: date  # the first day counted
    percent: Decimal  # time-weighted, over the range
    amount: Decimal  # the gain, in the currency asked for


@dataclass(frozen=True, slots=True)
class Returns:
    """What one position, or one holding in every account, returned, in the currency asked
    for, to the cent."""

    periods: dict[str, Period | None]  # by PERIODS; None: not held through all of it
    realised: Decimal | None  # None: the lots of a sale are not known
    unrealised: Decimal | None  # None: the cost of what is held is not known
    dividends: Decimal  # received, before tax
    tax: Decimal  # withheld from them: negative, as stored
    sold: bool = False  # anything of it was ever sold: else nothing could be realised
    # Held, yet no day ever counts: it never started a day with `performance.FLOOR` francs
    # in it, so no range has a return (rather than one it was not held through)
    too_small: bool = False


@dataclass(frozen=True, slots=True)
class Found:
    positions: dict[Position, Returns] = field(default_factory=dict)
    # by symbol, currency and kind of asset, as `Portfolio.holdings` groups the positions
    holdings: dict[tuple[str, str, str], Returns] = field(default_factory=dict)


@dataclass(slots=True)
class _Of:
    """What one position is made of."""

    series: list[Series] = field(default_factory=list)
    moved: list[_Moved] = field(default_factory=list)
    realised: Decimal | None = Decimal(0)
    sold: bool = False
    dividends: Decimal = Decimal(0)
    tax: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class _Trade:
    account: int
    instrument: int
    lot: LotTrade
    in_kind: bool  # moved in or out in kind: no money changed hands


def run(conn: sqlite3.Connection, valued: networth.Valued | None, held: Portfolio) -> Found:
    """What each position of `held`, and each holding, returned (see the module); `valued` is
    what `held` was worked out from."""
    if valued is None or held.day is None:
        return Found()
    days, currency = valued.days, held.currency
    rates = rates_on(conn, days)
    accounts = store.account_names(conn)
    symbols = store.symbols(conn)
    positions = {(p.owner, p.account, p.symbol, p.currency, p.asset): p for p in held.positions}
    made_of: dict[_Key, _Of] = defaultdict(_Of)
    ids: dict[tuple[int, int], _Key] = {}
    for each in valued.series:
        if each.instrument_id is None:
            continue
        key = (
            each.owner,
            accounts[each.account_id],
            symbols[each.instrument_id],
            each.currency,
            each.asset,
        )
        if key in positions:
            made_of[key].series.append(each)
            ids[each.account_id, each.instrument_id] = key
    trades = _trades(conn, days)
    splits = splits_through(conn, days[-1])
    by_position: dict[_Key, list[_Trade]] = defaultdict(list)
    for trade in trades:
        if (trade.account, trade.instrument) in ids:
            by_position[ids[trade.account, trade.instrument]].append(trade)
    when = sorted({t.lot.day for t in trades if t.in_kind and (t.account, t.instrument) in ids})
    closes = networth.closes_on(conn, when) if when else {}
    for key, its in by_position.items():
        of, native = made_of[key], key[3]
        for trade in its:
            on = closes.get(trade.instrument) if trade.in_kind else None
            worth = (
                Decimal(trade.lot.event.quantity) * on[when.index(trade.lot.day)]
                if on
                else from_nano(trade.lot.event.cost_nano)
            )
            of.moved.append((trade.lot.day, native, worth, trade.in_kind))
        of.sold = any(t.lot.event.quantity < 0 and not t.in_kind for t in its)
        of.realised = _realised(
            its, splits[its[0].instrument], days, native, currency=currency, rates=rates
        )
    _paid(conn, days, made_of, currency, rates)
    found = Found()
    for key, of in made_of.items():
        position = positions[key]
        found.positions[position] = _returns(
            [of], position.gain, days, currency=currency, rates=rates
        )
    for holding in held.holdings:
        parts = [
            made_of[key]
            for key in made_of
            if (key[2], key[3], key[4]) == (holding.symbol, holding.currency, holding.asset)
        ]
        found.holdings[holding.symbol, holding.currency, holding.asset] = _returns(
            parts, holding.gain, days, currency=currency, rates=rates
        )
    return found


def _returns(
    parts: Sequence[_Of],
    unrealised: Decimal | None,
    days: Sequence[date],
    *,
    currency: str,
    rates: Mapping[str, Sequence[Decimal]],
) -> Returns:
    """The returns of the positions `parts` together."""
    measured = instrument_ranges(
        [each for part in parts for each in part.series],
        [each for part in parts for each in part.moved],
        days,
        PERIODS,
        currency=currency,
        rates=rates,
    )
    periods = {
        name: None
        if found is None or found.twr is None
        else Period(found.since or days[0], found.twr, found.gain)
        for name, found in measured.items()
    }
    realised = [part.realised for part in parts]
    return Returns(
        periods,
        None if None in realised else _cents(sum((r or Decimal(0) for r in realised), Decimal(0))),
        unrealised,
        _cents(sum((part.dividends for part in parts), Decimal(0))),
        _cents(sum((part.tax for part in parts), Decimal(0))),
        sold=any(part.sold for part in parts),
        too_small=periods["all"] is None
        and any(each.values[-1] > 0 for part in parts for each in part.series),
    )


def _cents(amount: Decimal) -> Decimal:
    return amount.quantize(_CENT) + 0


def _trades(conn: sqlite3.Connection, days: Sequence[date]) -> list[_Trade]:
    """Every committed trade booked within `days`, in the order lots close: on one day,
    purchases before disposals."""
    rows = conn.execute(
        "SELECT t.account_id, tr.instrument_id, t.booking_date, t.id, tr.quantity, tr.cost_nano, "  # noqa: S608  # only "?" marks and the fixed condition of `store.committed_or_typed` are interpolated
        "tr.price IS NOT NULL, t.kind = 'transfer' "
        "FROM trades tr JOIN transactions t ON t.id = tr.transaction_id "
        f"WHERE {committed_or_typed('t.statement_file_id')} AND t.booking_date BETWEEN ? AND ? "
        "ORDER BY t.booking_date, tr.quantity LIKE '-%', t.id",
        (days[0].isoformat(), days[-1].isoformat()),
    )
    return [
        _Trade(
            int(account),
            int(instrument),
            LotTrade(
                date.fromisoformat(day), Event(str(txn), Decimal(qty), int(cost)), bool(priced)
            ),
            bool(in_kind),
        )
        for account, instrument, day, txn, qty, cost, priced, in_kind in rows
    ]


def _realised(  # noqa: PLR0913  # the trades, the splits, and how to convert them
    trades: Sequence[_Trade],
    splits: Sequence[Split],
    days: Sequence[date],
    native: str,
    *,
    currency: str,
    rates: Mapping[str, Sequence[Decimal]],
) -> Decimal | None:
    """What the sales of `trades` brought less what their lots cost, first in first out, in
    `currency`: each lot at the rate of the day it was bought, each sale at that of its own."""
    lots = [trade.lot for trade in trades]
    events, _ = lot_events(lots, splits, days[-1])
    unpriced = False
    converted: list[Event] = []
    brought: dict[str, Decimal] = {}
    for trade, event in zip(trades, events, strict=True):
        at = on_day(
            from_nano(event.cost_nano), native, currency, rates, (trade.lot.day - days[0]).days
        )
        if event.quantity > 0:
            unpriced = unpriced or not trade.lot.priced
            converted.append(Event(event.txn_id, event.quantity, to_nano(at.quantize(_NANO))))
        else:
            converted.append(event)
            if not trade.in_kind:
                brought[event.txn_id] = -at
    if not brought:
        return Decimal(0)
    if unpriced:
        return None
    costs = fifo_costs(converted)
    realised = Decimal(0)
    for sale, proceeds in brought.items():
        cost = costs[sale]
        if cost is None:
            return None
        realised += proceeds - from_nano(cost)
    return realised


def _paid(
    conn: sqlite3.Connection,
    days: Sequence[date],
    made_of: Mapping[_Key, _Of],
    currency: str,
    rates: Mapping[str, Sequence[Decimal]],
) -> None:
    """Add to each position what it paid: the dividends booked in its account whose
    counterparty names its instrument, and the tax withheld from them; each is also taken out
    of it at the end of its day. A row in a currency without a rate is left out."""
    named = named_instruments(conn)
    by_account: dict[tuple[str, str], list[_Key]] = defaultdict(list)
    for key in made_of:
        by_account[key[0], key[1]].append(key)
    rows = conn.execute(
        "SELECT owner, account, booking_date, currency, amount_nano, counterparty, category "
        "FROM committed_transactions WHERE category IN (?, ?) AND booking_date BETWEEN ? AND ?",
        (DIVIDENDS, WITHHOLDING_TAX, days[0].isoformat(), days[-1].isoformat()),
    )
    for owner, account, day, row_currency, nano, counterparty, category in rows:
        names = holding_of(str(counterparty or ""), named)
        keys = [key for key in by_account.get((owner, account), []) if key[2].upper() == names]
        if not keys or (row_currency != currency and not {row_currency, currency} <= rates.keys()):
            continue
        of = made_of[keys[0]]
        on = date.fromisoformat(day)
        amount = from_nano(nano)
        at = on_day(amount, str(row_currency), currency, rates, (on - days[0]).days)
        if category == WITHHOLDING_TAX:
            of.tax += at
        else:
            of.dividends += at
        of.moved.append((on, str(row_currency), -amount, False))
