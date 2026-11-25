"""What the investments are compared with: a fund the whole market is in (VT, the world; VOO,
the S&P 500), as `benchmarks` in scrooge.yaml names them.

A benchmark is an instrument whose daily closes `scrooge prices` fetches (`marketdata`): the
one known by its symbol, as it is priced or as it is named. Its close of a day is the last one
on or before it, as net worth takes it, and a split multiplies what is held of it, as it would
have for anyone holding it. Each close is converted into the currency asked for at the rate of
its day, so a move of the exchange rates is part of its return there, as it is of the
investments'.

**Its return** over a range is that of holding it from the end of the day the investments'
time-weighted return is measured from to the end of the last day: the figure to set beside
that return, which is not swayed by when money was put in either. The closes are prices alone:
what the fund paid out is not in them, so its return is that of its price, short of what
holding it returned by what it paid out (a percent or two a year for VT and VOO).

**Its line** on the chart is what the money put in would be worth had it gone into the
benchmark instead: the value the range starts with bought at the close it starts from, money
put in bought at the close of the day before it came (it comes at the start of its day), money
taken out sold at the close of its day, and shares moved in or out in kind, which are valued at
a close, bought or sold at that close, at the end of their day, as the return counts them. So
it starts where the investments do and takes in every flow they do, and the gap between the
two lines is what the choice of investments made. Money taken out beyond what the benchmark
would be worth sells what there is and no more.

Nothing here is stored and nothing here writes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge import networth

DEFAULT = ("VT", "VOO")  # the world and the S&P 500, as Yahoo knows them
_CENT = Decimal("0.01")


class BenchmarkError(Exception):
    """No close of a benchmark is stored for the days it is wanted."""


@dataclass(frozen=True, slots=True)
class Benchmark:
    """What holding a benchmark returned over the range, in percent."""

    symbol: str
    returned: Decimal


@dataclass(frozen=True, slots=True)
class Followed:
    """A benchmark over a range: its return, and its line, one value per day of the range."""

    benchmark: Benchmark
    values: tuple[Decimal, ...]


def follow(  # noqa: PLR0913  # which benchmark, over which days, with what money, in what
    conn: sqlite3.Connection,
    symbol: str,
    days: Sequence[date],
    money: Sequence[Decimal],
    *,
    currency: str,
    late: Sequence[Decimal] = (),
    start: int = 0,
) -> Followed:
    """The benchmark `symbol` over `days` (daily, the first the day before the range), in
    `currency`. `money[0]` is what the investments are worth at the end of the first day,
    `money[n]` what was put in on day n less what was taken out, of which `late[n]` came at
    the day's close (none when it is empty). Its return counts from the end of the `start`th
    day.

    Raises `BenchmarkError` when no close of it is stored on or before the first day, or
    none after it, and `networth.MissingRateError` when its currency has no rate."""
    found = conn.execute(
        "SELECT id, currency FROM instruments WHERE ? IN (symbol, price_symbol) "
        "AND type IN ('etf', 'stock', 'fund') ORDER BY id LIMIT 1",  # `marketdata.PRICED`
        (symbol,),
    ).fetchone()
    if found is None:
        msg = "no close of it is stored; `scrooge prices` fetches it"
        raise BenchmarkError(msg)
    closes = _closes(conn, int(found[0]), days[-1])
    if not closes or closes[0][0] > days[0]:
        msg = f"no close of it is stored on or before {days[0]}; `scrooge prices` fetches it"
        raise BenchmarkError(msg)
    if closes[-1][0] <= days[0]:
        msg = f"no close of it is stored after {days[0]}; `scrooge prices` fetches it"
        raise BenchmarkError(msg)
    native = networth._carried(closes, days)  # the close net worth takes
    prices = networth.convert(native, str(found[1]), currency, networth.rates_on(conn, days))
    held, values = Decimal(0), []
    for at, (price, amount) in enumerate(zip(prices, money, strict=True)):
        at_close = late[at] if at and late else Decimal(0)
        early = amount - at_close
        if early > 0:
            held += early / prices[max(at - 1, 0)]  # bought as the day starts
        else:
            at_close += early  # sold as it ends
        held = max(held + at_close / price, Decimal(0))
        values.append((held * price).quantize(_CENT))
    returned = ((prices[-1] / prices[start] - 1) * 100).quantize(_CENT)
    return Followed(Benchmark(symbol, returned), tuple(values))


def _closes(conn: sqlite3.Connection, instrument: int, last: date) -> list[tuple[date, Decimal]]:
    """The instrument's close of each day up to `last`, oldest first, one typed by hand before
    one fetched; each multiplied by the splits up to its day, so that one close to the next is
    what holding it returned."""
    splits = conn.execute(
        "SELECT date, numerator, denominator FROM splits WHERE instrument_id = ? ORDER BY date",
        (instrument,),
    ).fetchall()
    best: dict[date, Decimal] = {}
    for day_text, close in conn.execute(
        "SELECT date, close FROM prices WHERE instrument_id = ? AND date <= ? "
        "ORDER BY date, source <> 'manual'",
        (instrument, last.isoformat()),
    ):
        best.setdefault(date.fromisoformat(day_text), Decimal(close))
    found: list[tuple[date, Decimal]] = []
    for day, close in best.items():
        factor = Decimal(1)
        for split_day, numerator, denominator in splits:
            if date.fromisoformat(split_day) <= day:
                factor = factor * int(numerator) / int(denominator)
        found.append((day, close * factor))
    return found
