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

**What a position cost** is what was paid for the lots still held, first in, first out (D13,
the lots of `storage.bills`), in the instrument's own currency (`cost_native`), and in the
currency asked for (`cost`) with each lot at the exchange rate of the day it was bought (or
came in), so that a gain in that currency is the change of the price and of the rate since
(decided 2026-11-07). It is what the rows store as the total paid: the amount of the trade
without the broker's commission, which is a row of its own; for shares awarded, or moved in
in kind with a price, what they were worth the day they came. A trade is in the currency of
its instrument, always: an instrument is its symbol and its currency. A split changes the
quantity of the lots held before it, not what they cost. The cost is unknown, never guessed,
when the lots do not add up to the quantity held (the history starts with shares already
held; or a split left a fraction that is no exact decimal), or when a lot still held came in
without a price. Shares moved in kind between
two accounts do not take their cost along: nothing in the rows links the two sides.

`Portfolio.by_asset`, `total` and `shares` add up the positions only: of `Pillar 3a` the fund
units, without the cash of the account that the line of net worth also holds.

**The ladder of bills** lists the bills held on `Portfolio.day`, but counts the days to their
maturity from the day asked for (today, on the page) and values them on it: in a straight line
from cost to face as net worth does, at the last exchange rate stored by then. Nothing is
counted after the last day a person's statements cover (net worth carries what was held
then), so a bill bought since is not on it, and one that matured since is worth its face.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, Decimal
from math import prod

from scrooge import networth
from scrooge.storage.bills import Event, open_lots
from scrooge.storage.money import from_nano

_CENT = Decimal("0.01")
_LADDER = ("bill", "bond")  # the types of instrument that are repaid on a day
_Split = tuple[date, int, int]  # the day, and so many new shares for so many old ones


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
    # What the lots still held cost, first in first out, in `currency`, to the cent; None
    # when it is not known (see the module).
    cost_native: Decimal | None = None
    gain_native: Decimal | None = None  # `value_native` less `cost_native`
    # The same in `Portfolio.currency`, each lot at the rate of the day it was bought; None
    # when it is not known.
    cost: Decimal | None = None
    gain: Decimal | None = None  # `value` less `cost`

    @property
    def gain_percent(self) -> Decimal | None:
        """`gain_native` in percent of `cost_native`, to two decimals; None when either is
        not known, or nothing was paid."""
        if self.gain_native is None or not self.cost_native:
            return None
        return (self.gain_native * 100 / self.cost_native).quantize(_CENT)

    @property
    def return_percent(self) -> Decimal | None:
        """`gain` in percent of `cost`: in `Portfolio.currency`, with the change of the rate."""
        return _percent(self.gain, self.cost)


def _percent(gain: Decimal | None, cost: Decimal | None) -> Decimal | None:
    if gain is None or not cost:
        return None
    return (gain * 100 / cost).quantize(_CENT)


@dataclass(frozen=True, slots=True)
class Rung:
    """A bill or bond held, on the ladder of what is repaid when. Its face is the quantity."""

    position: Position  # on `Portfolio.day`
    days: int | None  # from `Portfolio.ladder_day` to the maturity; below zero: matured
    value_native: Decimal  # on `Portfolio.ladder_day`, in the bill's currency, to the cent
    value: Decimal  # the same in `Portfolio.currency`, at the last rate stored by then

    @property
    def interest(self) -> Decimal | None:
        """What is earned when it is repaid: the face less what it cost, in its currency, to
        the cent (as `storage.bills` splits it off then); None when the cost is not known."""
        cost = self.position.cost_native
        return None if cost is None else (self.position.quantity - cost).quantize(_CENT)


@dataclass(frozen=True, slots=True)
class Group:
    """The positions of one kind of asset, of everyone shown."""

    asset: str
    positions: tuple[Position, ...]  # by value (largest first), symbol, owner and account
    value: Decimal  # what they add up to, in `Portfolio.currency`
    share: Decimal  # of `Portfolio.total` in percent: the shares of its positions, added up


@dataclass(frozen=True, slots=True)
class Holding:
    """One instrument, whoever holds it and in whichever account."""

    symbol: str
    name: str
    asset: str
    currency: str  # of the instrument
    positions: int  # how many of `Portfolio.positions` it is made of
    value: Decimal  # in `Portfolio.currency`
    share: Decimal  # of `Portfolio.total` in percent: the shares of its positions, added up
    # In `Portfolio.currency`; None when that of any of its positions is not known.
    cost: Decimal | None
    gain: Decimal | None

    @property
    def return_percent(self) -> Decimal | None:
        return _percent(self.gain, self.cost)


@dataclass(frozen=True, slots=True)
class Portfolio:
    currency: str
    day: date | None  # the last day of `networth.run` for the same people; None: no such day
    # By owner, kind of asset in the order of `networth.ASSETS`, value (largest first),
    # symbol and account.
    positions: tuple[Position, ...]
    notes: tuple[str, ...]  # those of net worth that are about what is held
    # The cash of the pension accounts, in `currency`: what the `Pillar 3a` lines of net worth
    # hold beyond the fund units listed here, to the cent. None: nobody has such an account.
    pension_cash: Decimal | None = None
    # The bills and bonds held, by maturity (those without one last), then by symbol, owner and
    # account, valued on `ladder_day`: the day asked for, never before `day`. None: no bills.
    ladder: tuple[Rung, ...] = ()
    ladder_day: date | None = None
    # the earliest last day the statements of those shown cover: what they held then is what
    # is listed on `day` (`networth.value`); None: no such day
    through: date | None = None

    @property
    def total(self) -> Decimal:
        """What all the positions are worth, in `currency`."""
        return sum((position.value for position in self.positions), Decimal(0))

    @property
    def cost(self) -> Decimal:
        """What the positions whose cost is known cost, in `currency`."""
        return sum((p.cost for p in self.positions if p.cost is not None), Decimal(0))

    @property
    def gain(self) -> Decimal:
        """What the positions whose cost is known gained, in `currency`."""
        return sum((p.gain for p in self.positions if p.gain is not None), Decimal(0))

    @property
    def unknown_cost(self) -> int:
        """How many positions have no known cost, and are in neither sum above."""
        return sum(1 for position in self.positions if position.cost is None)

    @property
    def return_percent(self) -> Decimal | None:
        return _percent(self.gain, self.cost)

    @property
    def by_asset(self) -> dict[str, Decimal]:
        """Kind of asset -> what its positions are worth, in the order of `networth.ASSETS`."""
        sums: dict[str, Decimal] = {}
        for asset in networth.ASSETS:
            values = [p.value for p in self.positions if p.asset == asset]
            if values:
                sums[asset] = sum(values, Decimal(0))
        return sums

    @property
    def shares(self) -> dict[Position, Decimal]:
        """Each position's part of `total` in percent, to two decimals.

        They are shared out like the cents of the values, so they add up to exactly 100; all
        of them are zero when the total is.
        """
        total = self.total
        if not total:
            return dict.fromkeys(self.positions, Decimal(0))
        exact = [position.value * 100 / total for position in self.positions]
        return dict(zip(self.positions, shared_out(exact), strict=True))

    @property
    def holdings(self) -> tuple[Holding, ...]:
        """The positions by instrument (its symbol, currency and kind), largest first."""
        shares = self.shares
        made_of: dict[tuple[str, str, str], list[Position]] = defaultdict(list)
        for position in self.positions:
            made_of[position.symbol, position.currency, position.asset].append(position)
        holdings = []
        for (symbol, currency, asset), parts in made_of.items():
            value = sum((p.value for p in parts), Decimal(0))
            known = all(p.cost is not None for p in parts)
            cost = sum((p.cost or Decimal(0) for p in parts), Decimal(0)) if known else None
            holdings.append(
                Holding(
                    symbol=symbol,
                    name=next((p.name for p in parts if p.name), ""),
                    asset=asset,
                    currency=currency,
                    positions=len(parts),
                    value=value,
                    share=sum((shares[p] for p in parts), Decimal(0)),
                    cost=cost,
                    gain=None if cost is None else value - cost,
                )
            )
        return tuple(sorted(holdings, key=lambda h: (-h.value, h.symbol)))

    @property
    def groups(self) -> tuple[Group, ...]:
        """The positions by kind of asset, in the order of `networth.ASSETS`."""
        shares = self.shares
        groups = []
        for asset, value in self.by_asset.items():
            held = [position for position in self.positions if position.asset == asset]
            held.sort(key=lambda p: (-p.value, p.symbol, p.owner, p.account))
            share = sum((shares[position] for position in held), Decimal(0))
            groups.append(Group(asset, tuple(held), value, share))
        return tuple(groups)


def shared_out(exact: Sequence[Decimal]) -> list[Decimal]:
    """`exact` to the cent, so that the parts add up to their rounded sum."""
    whole = sum(exact, Decimal(0)).quantize(_CENT)
    parts = [value.quantize(_CENT, rounding=ROUND_FLOOR) for value in exact]
    missing = int((whole - sum(parts, Decimal(0))) / _CENT)
    lost_most = sorted(range(len(exact)), key=lambda n: parts[n] - exact[n])
    for n in lost_most[:missing]:
        parts[n] += _CENT
    return parts


def _lot_events(
    trades: Sequence[networth.LotTrade], splits: Sequence[_Split], last: date
) -> tuple[list[Event], int]:
    """The purchases and disposals up to `last` in one unit, and how many units make a share.

    A split multiplies what was held overnight (`holdings.timeline`): a trade before its day
    counts for the new shares it became. Three new shares for two are no exact decimal for
    every quantity, so nothing is divided: the unit is a share of the last day divided by
    every denominator, and each quantity is multiplied only.
    """
    events = [
        Event(
            trade.event.txn_id,
            trade.event.quantity
            * prod(new if day > trade.day else old for day, new, old in splits),
            trade.event.cost_nano,
        )
        for trade in trades
        if trade.day <= last
    ]
    return events, prod(old for _, _, old in splits)


def _lots(
    trades: Sequence[networth.LotTrade], splits: Sequence[_Split], held: Decimal, last: date
) -> list[tuple[date, Decimal]] | None:
    """The day each lot that makes up `held` at the end of `last` was bought and what it cost;
    None when that is not known."""
    events, per_share = _lot_events(trades, splits, last)
    lots = open_lots(events)
    if sum((left for _, left, _ in lots), Decimal(0)) != held * per_share:
        return None
    unpriced = {trade.event.txn_id for trade in trades if not trade.priced}
    if any(purchase in unpriced for purchase, _, _ in lots):
        return None
    bought_on = {trade.event.txn_id: trade.day for trade in trades}
    return [(bought_on[purchase], from_nano(paid)) for purchase, _, paid in lots]


def _converted(
    lots: Sequence[tuple[date, Decimal]],
    currency: str,
    into: str,
    rates: Mapping[str, Sequence[Decimal]],
    days: Mapping[date, int],
) -> Decimal:
    """What `lots` of `currency` cost in `into`, each at the rate of the day it was bought."""
    on = {name: [series[days[day]] for day, _ in lots] for name, series in rates.items()}
    paid = networth.convert([cost for _, cost in lots], currency, into, on)
    return sum(paid, Decimal(0)).quantize(_CENT)


def run(
    conn: sqlite3.Connection,
    *,
    owner: str | None = None,
    currency: str = networth.DEFAULT_CURRENCY,
    today: date | None = None,
) -> Portfolio:
    """What `owner`, or everyone, holds at the end of the last day of `networth.run`.

    Only what is worth something that day is listed: what was sold is not, nor what net
    worth cannot value (a note then says why). The ladder of bills is valued on `today`
    (see the module).
    """
    return positions_of(conn, networth.value(conn, owner), currency, today)


def positions_of(
    conn: sqlite3.Connection,
    valued: networth.Valued | None,
    currency: str,
    today: date | None = None,
) -> Portfolio:
    """`run` for what `networth.value` already worked out."""
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
        for n, value in zip(members, shared_out(exact), strict=True):
            values[n] = value
    accounts = dict(conn.execute("SELECT id, name FROM accounts").fetchall())
    instruments = {
        row[0]: row[1:]
        for row in conn.execute("SELECT id, symbol, name, type, maturity FROM instruments")
    }
    trades = networth.lot_trades(conn)
    splits: dict[int, list[_Split]] = defaultdict(list)
    for split_of, day, new, old in conn.execute(
        "SELECT instrument_id, date, numerator, denominator FROM splits WHERE date <= ?",
        (last[0].isoformat(),),
    ):
        splits[split_of].append((date.fromisoformat(day), int(new), int(old)))
    held_ids = [series.instrument_id for series in held if series.instrument_id is not None]
    # What was held on the last day a person's statements cover is carried after it.
    traded = [
        [t for t in trades.get((s.account_id, i), []) if t.day <= valued.through[s.owner]]
        for s, i in zip(held, held_ids, strict=True)
    ]
    lots = [
        _lots(made_of, splits[i], s.quantity or Decimal(0), last[0])
        for s, i, made_of in zip(held, held_ids, traded, strict=True)
    ]
    bought = sorted({day for each in lots for day, _ in each or ()})
    on_days = networth.rates_on(conn, bought)
    index = {day: n for n, day in enumerate(bought)}
    positions: list[Position] = []
    bills: list[tuple[Position, list[networth.LotTrade]]] = []
    for series, instrument, value, made_of, its_lots in zip(
        held, held_ids, values, traded, lots, strict=True
    ):
        symbol, name, kind, maturity = instruments[instrument]
        quote = series.quote
        quantity = series.quantity or Decimal(0)
        worth = series.values[-1].quantize(_CENT)
        cost = (
            None if its_lots is None else sum((c for _, c in its_lots), Decimal(0)).quantize(_CENT)
        )
        converted = (
            None
            if its_lots is None
            else _converted(its_lots, series.currency, currency, on_days, index)
        )
        positions.append(
            Position(
                owner=series.owner,
                account=accounts[series.account_id],
                symbol=symbol,
                name=name,
                type=kind,
                asset=series.asset,
                currency=series.currency,
                quantity=quantity,
                price=None if quote is None else quote.close,
                price_day=None if quote is None else quote.day,
                price_is_trade=quote is not None and quote.traded,
                maturity=maturity and date.fromisoformat(maturity),
                value_native=worth,
                value=value,
                cost_native=cost,
                gain_native=None if cost is None else worth - cost,
                cost=converted,
                gain=None if converted is None else value - converted,
            )
        )
        if kind in _LADDER:
            bills.append((positions[-1], made_of))
    positions.sort(
        key=lambda p: (p.owner, networth.ASSETS.index(p.asset), -p.value, p.symbol, p.account)
    )
    # Each person's line as net worth adds it up and rounds it, less the units listed.
    pension: dict[str, Decimal] = defaultdict(Decimal)
    for series in valued.series:
        if series.asset == networth.PENSION:
            pension[series.owner] += networth.convert(
                series.values[-1:], series.currency, currency, rates
            )[0]
    cash = sum((line.quantize(_CENT) for line in pension.values()), Decimal(0)) - sum(
        (p.value for p in positions if p.asset == networth.PENSION), Decimal(0)
    )
    return Portfolio(
        currency,
        valued.days[-1],
        tuple(positions),
        valued.security_notes,
        cash if pension else None,
        *_ladder(conn, bills, currency, max(today or last[0], last[0])),
        through=valued.covered,
    )


def _ladder(
    conn: sqlite3.Connection,
    bills: Sequence[tuple[Position, Sequence[networth.LotTrade]]],
    currency: str,
    on: date,
) -> tuple[tuple[Rung, ...], date | None]:
    """The bills held, each with the lots it is made of, as they stand on `on`; no day when
    there are none."""
    rates = networth.rates_on(conn, [on])
    rungs = []
    for position, trades in bills:
        [worth] = networth.bill_values(trades, position.maturity, [on])
        [value] = networth.convert([worth], position.currency, currency, rates)
        days = None if position.maturity is None else (position.maturity - on).days
        rungs.append(Rung(position, days, worth.quantize(_CENT), value.quantize(_CENT)))
    rungs.sort(
        key=lambda rung: (
            (p := rung.position).maturity is None,
            p.maturity or date.min,
            p.symbol,
            p.owner,
            p.account,
        )
    )
    return tuple(rungs), on if rungs else None
