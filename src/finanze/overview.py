"""The first page: net worth on the last day, what it is made of, and the largest holdings.

Nothing is worked out here that `networth` and `portfolio` do not already say: everything
is valued once (`networth.value`), and the figures are the last day of the one and the
positions of the other, cut and added up for one screen. Nothing here is stored and nothing
here writes.

**Shares are of net worth**, in percent to two decimals, shared out like the cents of
`portfolio` so that those of the kinds of asset, and those of the people, add up to exactly
100. What is owed (the card) is a negative line and has a negative share: the others then
add up to more than 100, and all of them to 100. When net worth is not above zero a share
says nothing, and there is none (None).

**A holding** is one instrument (its symbol and its currency) in one line of net worth,
whoever holds it and in whichever account: the same ETF at two brokers, or of two people,
is one row. Only the largest `TOP` are listed; `Overview.rest` is what the others add up
to, so that the two together are `portfolio`'s total of all securities.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finanze import networth, portfolio

TOP = 10  # how many holdings are listed
SPANS = (30, 365)  # the days back that net worth is compared with
_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Change:
    """Net worth against what it was `days` before the last day."""

    days: int
    before: Decimal | None  # None: the history is shorter than that
    amount: Decimal | None  # the last day less `before`
    percent: Decimal | None  # `amount` in percent of `before`; None unless that is above zero


@dataclass(frozen=True, slots=True)
class Part:
    """What one kind of asset, or one person, adds to net worth on the last day."""

    name: str
    value: Decimal
    share: Decimal | None  # of net worth, in percent (see the module)


@dataclass(frozen=True, slots=True)
class Holding:
    """One instrument, in every account of everyone shown."""

    symbol: str
    name: str  # empty when none is stored
    asset: str  # the line of net worth it is part of
    value: Decimal
    share: Decimal | None  # of net worth, in percent


@dataclass(frozen=True, slots=True)
class Rest:
    """The holdings that are not among the largest."""

    count: int
    value: Decimal
    share: Decimal | None


@dataclass(frozen=True, slots=True)
class Overview:
    currency: str
    day: date | None  # the last day of `networth.run` for the same people; None: no such day
    total: Decimal  # net worth on `day`
    changes: tuple[Change, ...]  # one for each of `SPANS`
    assets: tuple[Part, ...]  # in the order of `networth.ASSETS`; those worth nothing are absent
    people: tuple[Part, ...]  # by name; empty unless everyone is shown and they are several
    top: tuple[Holding, ...]  # by value (largest first), then symbol
    rest: Rest | None  # None: `top` is all of them
    securities: Part  # every holding together: `portfolio.Portfolio.total`


def _shares(values: list[Decimal], total: Decimal) -> list[Decimal | None]:
    """Each value's part of `total` in percent; they add up to that of their sum."""
    if total <= 0:
        return [None] * len(values)
    return [*portfolio.shared_out([value * 100 / total for value in values])]


def _added(shares: list[Decimal | None]) -> Decimal | None:
    known = [share for share in shares if share is not None]
    return sum(known, Decimal(0)) if len(known) == len(shares) else None


def _parts(lines: dict[str, tuple[Decimal, ...]], total: Decimal) -> tuple[Part, ...]:
    last = {name: values[-1] for name, values in sorted(lines.items()) if values[-1]}
    shares = _shares(list(last.values()), total)
    return tuple(
        Part(name, value, share) for (name, value), share in zip(last.items(), shares, strict=True)
    )


def _change(total: tuple[Decimal, ...], days: int) -> Change:
    before = networth.before(total, days)
    if before is None:
        return Change(days, None, None, None)
    amount = total[-1] - before
    percent = (amount * 100 / before).quantize(_CENT) if before > 0 else None
    return Change(days, before, amount, percent)


def run(
    conn: sqlite3.Connection,
    *,
    owner: str | None = None,
    currency: str = networth.DEFAULT_CURRENCY,
    top: int = TOP,
) -> Overview:
    """Net worth of `owner`, or of everyone, on the last day, and the `top` largest holdings."""
    valued = networth.value(conn, owner)
    worth = networth.worth_of(conn, valued, currency)
    if not worth.days:
        return Overview(
            currency, None, Decimal(0), (), (), (), (), None, Part("", Decimal(0), None)
        )
    held = portfolio.positions_of(conn, valued, currency)
    total = worth.total[-1]
    people = _parts(worth.by("owner"), total) if owner is None else ()
    sums: dict[tuple[str, str, str], Decimal] = {}
    names: dict[tuple[str, str, str], str] = {}
    for position in held.positions:
        key = (position.symbol, position.currency, position.asset)
        sums[key] = sums.get(key, Decimal(0)) + position.value
        names[key] = names.get(key) or position.name
    largest = sorted(sums, key=lambda key: (-sums[key], key))
    # What is no security is shared out with them, so that every share is of net worth.
    *shares, _ = _shares([*(sums[key] for key in largest), total - held.total], total)
    holdings = [
        Holding(key[0], names[key], key[2], sums[key], share)
        for key, share in zip(largest, shares, strict=True)
    ]
    by_asset = _parts(worth.by("asset"), total)
    cut = holdings[top:]
    rest = None
    if cut:
        value = sum((holding.value for holding in cut), Decimal(0))
        rest = Rest(len(cut), value, _added([holding.share for holding in cut]))
    return Overview(
        currency=currency,
        day=worth.days[-1],
        total=total,
        changes=tuple(_change(worth.total, days) for days in SPANS),
        assets=tuple(sorted(by_asset, key=lambda part: networth.ASSETS.index(part.name))),
        people=people if len(people) > 1 else (),
        top=tuple(holdings[:top]),
        rest=rest,
        securities=Part("Securities", held.total, _added(shares)),
    )
