"""The price step of month close: bring stored closes, splits and exchange rates up to date;
and the maturities of the T-bills an import stopped on.

This is the only caller of `scrooge.prices`, the one module that uses the network (D9). What
is fetched is stored once and never fetched again (D10): each run asks only for the days after
the last one stored, and for those before the first only when older history was imported
since, so a committed month keeps the figures it was closed with. Everything is
fetched first and stored in one transaction afterwards.

The benchmarks the investments are compared with (`benchmarks` in scrooge.yaml) are fetched
like a share held from the first day anything is known. A benchmark is an instrument like any
other: the one held under its symbol, when there is one, whose closes are then fetched once
for both; else one made on its first fetch, in the currency the source quotes it in.

What cannot be fetched is reported, never guessed: a share without a close is valued at the
price it last traded at, and the net-worth page says so.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from scrooge.prices import History, MaturitySource, PriceError, PriceSource, rate_symbol
from scrooge.storage import db, holdings, store

BASE = "CHF"  # every stored rate is francs per unit of the currency
PRICED = frozenset({"etf", "stock", "fund"})  # a bill has no quoted price (D20)
# A request starts a few days early: the first day wanted may be a weekend or a holiday,
# and the close of the day before is then the one that counts.
_MARGIN = timedelta(days=10)
# A week without a close is more than a weekend and a holiday: `prices` has not run.
STALE = timedelta(days=7)
_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")


@dataclass(frozen=True, slots=True)
class Refreshed:
    """What `refresh` added, and what it could not get."""

    closes: int
    rates: int
    splits: int
    problems: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Fetched:
    instrument: store.Instrument | None  # None: a benchmark that is no instrument yet
    price_symbol: str
    history: History
    asked: date  # the first day asked for


def refresh(  # noqa: PLR0913  # what to fetch, from where, up to when, and three local overrides
    conn: sqlite3.Connection,
    source: PriceSource,
    *,
    through: date,
    now: datetime,
    symbols: Mapping[str, str] | None = None,
    currencies: Collection[str] = (),
    benchmarks: Collection[str] = (),
) -> Refreshed:
    """Fetch what is missing up to `through` and store it.

    Closes are fetched for every share, ETF and fund ever held, over the days it was held,
    and for each of `benchmarks` (symbols as the source knows them) from the first day
    anything is known; rates for every currency of the database and for `currencies`, from
    that day too. `symbols` says where an instrument's prices are found when that is not
    under its own symbol (`price_symbol` in `instruments.yaml`).
    """
    problems: list[str] = []
    fetched = _closes(conn, source, through, (symbols or {}, benchmarks), problems)
    rates = _rates(conn, source, through, currencies, problems)
    stamp, name = db.timestamp(now), f"fetched:{source.name}"
    added = [0, 0, 0]
    with db.transaction(conn):
        for item in fetched:
            instrument = _instrument_of(conn, item)
            store.set_price_symbol(conn, instrument, item.price_symbol)
            for day, close in item.history.closes:
                added[0] += store.add_price(
                    conn, instrument, day, close, source=name, fetched_at=stamp
                )
            for split in item.history.splits:
                added[2] += store.add_split(
                    conn, instrument, split.day, split.numerator, split.denominator
                )
            store.set_asked_from(conn, _close_series(instrument), name, item.asked)
        for currency, (history, asked) in rates.items():
            store.set_asked_from(conn, _rate_series(currency), name, asked)
            for day, rate in history.closes:
                added[1] += store.add_rate(conn, day, currency, rate, source=name, fetched_at=stamp)
    return Refreshed(added[0], added[1], added[2], tuple(problems))


def stale(conn: sqlite3.Connection, today: date, currencies: Collection[str] = ()) -> list[str]:
    """What `refresh` would bring up to yesterday and has not: each share, ETF or fund still
    held, and each currency of the database and of `currencies`, whose newest close or rate
    stored is more than `STALE` before yesterday, or that has none. Nothing is fetched."""
    through = today - timedelta(days=1)
    oldest = through - STALE
    found: list[str] = []
    spans = _held_spans(conn, through)
    for instrument in store.instruments(conn):
        if instrument.type not in PRICED or spans.get(instrument.id, (through, None))[1] != through:
            continue
        newest = store.newest_price(conn, instrument.id)
        if newest is None or newest < oldest:
            found.append(f"{instrument.symbol}: {_newest('close', newest)}")
    if store.first_day(conn) is None:  # nothing to value yet, in any currency
        return found
    for currency in sorted({*store.currencies(conn), *currencies} - {BASE}):
        newest = store.newest_rate(conn, currency)
        if newest is None or newest < oldest:
            found.append(f"{currency}: {_newest('rate', newest)}")
    return found


def _newest(what: str, day: date | None) -> str:
    return f"no {what} stored" if day is None else f"the newest {what} is of {day}"


def _closes(
    conn: sqlite3.Connection,
    source: PriceSource,
    through: date,
    wanted_as: tuple[Mapping[str, str], Collection[str]],
    problems: list[str],
) -> list[_Fetched]:
    """The closes of what is held and of the benchmarks (`wanted_as`: the typed symbols, and
    the benchmarks' symbols)."""
    symbols = wanted_as[0]
    name = f"fetched:{source.name}"
    spans = _held_spans(conn, through)
    instruments = store.instruments(conn)
    new = _benchmark_spans(conn, instruments, wanted_as, through, spans)
    fetched: list[_Fetched] = []
    for instrument in instruments:
        if instrument.type not in PRICED or instrument.id not in spans:
            continue
        first, last = spans[instrument.id]
        stored = store.price_days(conn, instrument.id, name)
        asked = store.asked_from(conn, _close_series(instrument.id), name)
        wanted = _missing(first, last, stored, asked)
        if not wanted:
            continue
        try:
            price_symbol = _price_symbol(instrument, symbols, source)
            history = _history(source, price_symbol, wanted)
        except PriceError as exc:
            problems.append(f"{instrument.symbol}: no prices ({exc})")
            continue
        if history.currency != instrument.currency:
            problems.append(
                f"{instrument.symbol}: {price_symbol} is quoted in {history.currency}, "
                f"not in {instrument.currency}: set its price_symbol in instruments.yaml"
            )
            continue
        if not history.closes and stored is None:
            problems.append(
                f"{instrument.symbol}: {price_symbol} has no close since {wanted[0][0]}"
            )
        fetched.append(_Fetched(instrument, price_symbol, history, wanted[0][0]))
    for symbol, first in new:
        try:
            history = source.history(symbol, first, through)
        except PriceError as exc:
            problems.append(f"{symbol}: no prices ({exc})")
            continue
        if not history.closes:
            problems.append(f"{symbol}: {symbol} has no close since {first}")
            continue
        fetched.append(_Fetched(None, symbol, history, first))
    return fetched


def _benchmark_spans(
    conn: sqlite3.Connection,
    instruments: list[store.Instrument],
    wanted_as: tuple[Mapping[str, str], Collection[str]],
    through: date,
    spans: dict[int, tuple[date, date]],
) -> list[tuple[str, date]]:
    """Widen `spans` to the days from the first one anything is known for up to `through` for
    each instrument a benchmark is, and say which benchmarks are no instrument yet, with the
    first day to ask for them from.

    A benchmark is the instrument priced under its symbol or so named, the first known."""
    symbols, benchmarks = wanted_as
    first = store.first_day(conn)
    if first is None or first > through:
        return []
    new: list[tuple[str, date]] = []
    for symbol in dict.fromkeys(benchmarks):
        found = next(
            (
                instrument.id
                for instrument in instruments
                if instrument.type in PRICED
                and symbol
                in (instrument.symbol, symbols.get(instrument.symbol) or instrument.price_symbol)
            ),
            None,
        )
        if found is None:
            new.append((symbol, first - _MARGIN))
        else:
            spans[found] = (min(first, spans.get(found, (first, through))[0]), through)
    return new


def _instrument_of(conn: sqlite3.Connection, item: _Fetched) -> int:
    """The instrument the closes are of: a benchmark fetched for the first time is made one,
    an ETF in the currency it is quoted in."""
    if item.instrument is not None:
        return item.instrument.id
    store.ensure_currency(conn, item.history.currency)
    return store.instrument_id(conn, item.price_symbol, "ETFs", item.history.currency)


def _close_series(instrument: int) -> str:
    return f"close:{instrument}"


def _rate_series(currency: str) -> str:
    return f"rate:{currency}"


def _missing(
    first: date, last: date, stored: tuple[date, date] | None, asked: date | None
) -> list[tuple[date, date]]:
    """The days to ask for, from a few before `first` to `last`, less those `stored` spans.

    What is stored is never asked for again, and neither are the days a request already began
    on (`asked`): the source may simply have no close that far back. Days before are asked
    for only when history older than the first day asked was imported since.
    """
    if stored is None or asked is None:
        return [(first - _MARGIN, last)] if first <= last else []
    spans: list[tuple[date, date]] = []
    if first - _MARGIN < asked:
        spans.append((first - _MARGIN, asked - timedelta(days=1)))
    if stored[1] < last:
        spans.append((stored[1] + timedelta(days=1), last))
    return spans


def _history(source: PriceSource, symbol: str, spans: list[tuple[date, date]]) -> History:
    """What the source says of each span, as one history."""
    found = [source.history(symbol, start, end) for start, end in spans]
    closes = tuple(close for history in found for close in history.closes)
    splits = tuple(
        sorted(
            {split for history in found for split in history.splits}, key=lambda split: split.day
        )
    )
    return History(found[0].currency, closes, splits)


def _held_spans(conn: sqlite3.Connection, through: date) -> dict[int, tuple[date, date]]:
    """instrument -> the first and the last day a close of it is needed, up to `through`.

    An instrument sold needs no close after the day the last of it went, and one bought
    needs none before: a statement that says none was held yet is not the first day.
    """
    spans: dict[int, tuple[date, date]] = {}
    for holding in holdings.held(conn):
        steps = holding.steps
        if holding.opening == 0 and all(step.quantity == 0 for step in steps):
            continue
        first = next(s.day for s in steps if holding.opening != 0 or s.quantity != 0)
        last = through if steps[-1].quantity != 0 else min(steps[-1].day, through)
        if first > through:
            continue
        known = spans.get(holding.instrument_id, (first, last))
        spans[holding.instrument_id] = (min(first, known[0]), max(last, known[1]))
    return spans


def _price_symbol(
    instrument: store.Instrument, symbols: Mapping[str, str], source: PriceSource
) -> str:
    """Where the instrument's prices are: as typed, as found before, by its ISIN, or as named."""
    typed = symbols.get(instrument.symbol)
    if typed:
        return typed
    if instrument.price_symbol:
        return instrument.price_symbol
    if _ISIN.fullmatch(instrument.symbol):
        found = source.find(instrument.symbol)
        if found is None:
            msg = f"the price source does not know the ISIN {instrument.symbol}"
            raise PriceError(msg)
        return found
    return instrument.symbol


def _rates(
    conn: sqlite3.Connection,
    source: PriceSource,
    through: date,
    currencies: Collection[str],
    problems: list[str],
) -> dict[str, tuple[History, date]]:
    name = f"fetched:{source.name}"
    first = store.first_day(conn)
    if first is None:
        return {}
    rates: dict[str, tuple[History, date]] = {}
    for currency in sorted({*store.currencies(conn), *currencies} - {BASE}):
        asked = store.asked_from(conn, _rate_series(currency), name)
        spans = _missing(first, through, store.rate_days(conn, currency, name), asked)
        if not spans:
            continue
        symbol = rate_symbol(currency, BASE)
        try:
            history = _history(source, symbol, spans)
        except PriceError as exc:
            problems.append(f"{currency}: no rates ({exc})")
            continue
        if history.currency != BASE:
            problems.append(f"{currency}: {symbol} is quoted in {history.currency}, not in {BASE}")
            continue
        rates[currency] = (history, spans[0][0])
    return rates


@dataclass(frozen=True, slots=True)
class Maturities:
    """What `maturities` found, and the bills the source does not know."""

    found: dict[str, date]
    unknown: tuple[str, ...]


def maturities(source: MaturitySource, cusips: Collection[str]) -> Maturities:
    """Look up when each of `cusips` matures. Nothing is stored: the caller writes them down.

    A source that cannot be reached, or answers in a form it should not, raises `PriceError`.
    """
    found: dict[str, date] = {}
    unknown: list[str] = []
    for cusip in sorted(cusips):
        day = source.maturity(cusip)
        if day is None:
            unknown.append(cusip)
        else:
            found[cusip] = day
    return Maturities(found, tuple(unknown))
