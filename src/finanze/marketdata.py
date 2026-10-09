"""The price step of month close: bring stored closes, splits and exchange rates up to date.

This is the only caller of `finanze.prices`, the one module that uses the network (D9). What
is fetched is stored once and never fetched again (D10): each run asks only for the days after
the last one stored, so a committed month keeps the figures it was closed with. Everything is
fetched first and stored in one transaction afterwards.

What cannot be fetched is reported, never guessed: a share without a close is valued at the
price it last traded at, and the net-worth page says so.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from finanze.prices import History, PriceError, PriceSource, rate_symbol
from finanze.storage import db, holdings, store

BASE = "CHF"  # every stored rate is francs per unit of the currency
PRICED = frozenset({"etf", "stock", "fund"})  # a bill has no quoted price (D20)
# A request starts a few days early: the first day wanted may be a weekend or a holiday,
# and the close of the day before is then the one that counts.
_MARGIN = timedelta(days=10)
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
    instrument: store.Instrument
    price_symbol: str
    history: History


def refresh(  # noqa: PLR0913  # what to fetch, from where, up to when, and two local overrides
    conn: sqlite3.Connection,
    source: PriceSource,
    *,
    through: date,
    now: datetime,
    symbols: Mapping[str, str] | None = None,
    currencies: Collection[str] = (),
) -> Refreshed:
    """Fetch what is missing up to `through` and store it.

    Closes are fetched for every share, ETF and fund ever held, over the days it was held;
    rates for every currency of the database and for `currencies`, from the first day
    anything is known. `symbols` says where an instrument's prices are found when that is
    not under its own symbol (`price_symbol` in `instruments.yaml`).
    """
    problems: list[str] = []
    fetched = _closes(conn, source, through, symbols or {}, problems)
    rates = _rates(conn, source, through, currencies, problems)
    stamp, name = db.timestamp(now), f"fetched:{source.name}"
    added = [0, 0, 0]
    with db.transaction(conn):
        for item in fetched:
            instrument = item.instrument.id
            store.set_price_symbol(conn, instrument, item.price_symbol)
            for day, close in item.history.closes:
                added[0] += store.add_price(
                    conn, instrument, day, close, source=name, fetched_at=stamp
                )
            for split in item.history.splits:
                added[2] += store.add_split(
                    conn, instrument, split.day, split.numerator, split.denominator
                )
        for currency, history in rates.items():
            for day, rate in history.closes:
                added[1] += store.add_rate(conn, day, currency, rate, source=name, fetched_at=stamp)
    return Refreshed(added[0], added[1], added[2], tuple(problems))


def _closes(
    conn: sqlite3.Connection,
    source: PriceSource,
    through: date,
    symbols: Mapping[str, str],
    problems: list[str],
) -> list[_Fetched]:
    name = f"fetched:{source.name}"
    spans = _held_spans(conn, through)
    fetched: list[_Fetched] = []
    for instrument in store.instruments(conn):
        if instrument.type not in PRICED or instrument.id not in spans:
            continue
        first, last = spans[instrument.id]
        stored = store.price_days(conn, instrument.id, name)
        wanted = _missing(first, last, stored)
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
        fetched.append(_Fetched(instrument, price_symbol, history))
    return fetched


def _missing(first: date, last: date, stored: tuple[date, date] | None) -> list[tuple[date, date]]:
    """The days to ask for, from a few before `first` to `last`, less those `stored` spans.

    What is stored is never asked for again. Days before it are, when history older than the
    first stored day was imported since; within the margin there may just be no close.
    """
    if stored is None:
        return [(first - _MARGIN, last)] if first <= last else []
    spans: list[tuple[date, date]] = []
    if first + _MARGIN < stored[0]:
        spans.append((first - _MARGIN, stored[0] - timedelta(days=1)))
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
) -> dict[str, History]:
    name = f"fetched:{source.name}"
    first = store.first_day(conn)
    if first is None:
        return {}
    rates: dict[str, History] = {}
    for currency in sorted({*store.currencies(conn), *currencies} - {BASE}):
        spans = _missing(first, through, store.rate_days(conn, currency, name))
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
        rates[currency] = history
    return rates
