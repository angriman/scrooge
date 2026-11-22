"""Daily closes, splits and exchange rates, and the maturity of a T-bill, from the network
(D9, D10, D20).

This is the only module that opens a connection, and only `scrooge.marketdata` calls it: for
`scrooge prices`, and for the import page's button that looks up the bills an import stopped
on (decided 2026-11-03). Nothing here touches the database: what is fetched is handed
back, and stored by the caller, once.

A source is asked for a range of days and never for one day: a day without a close (a
weekend, a holiday) is simply absent, and whoever values a day takes the last close on or
before it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

_DIGITS = 7  # what a close is worth: Yahoo sends single-precision floats (51.127498626708984)
_TIMEOUT = 30  # seconds
_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/"
_SEARCH = "https://query2.finance.yahoo.com/v1/finance/search?"
_SECURITIES = "https://www.treasurydirect.gov/TA_WS/securities/search?"


class PriceError(Exception):
    """A symbol's prices could not be fetched or read."""


@dataclass(frozen=True, slots=True)
class Split:
    """From `day` on, `numerator` shares stand for every `denominator` held before."""

    day: date
    numerator: int
    denominator: int


@dataclass(frozen=True, slots=True)
class History:
    """What a symbol closed at, day by day, as it traded: not adjusted for later splits."""

    currency: str
    closes: tuple[tuple[date, Decimal], ...]  # oldest first; days without a close are absent
    splits: tuple[Split, ...] = ()


class PriceSource(Protocol):
    """Where closes come from. `name` is stored with each of them (`fetched:<name>`)."""

    name: str

    def history(self, symbol: str, first: date, last: date) -> History:
        """The closes of `symbol` from `first` to `last`, both included."""

    def find(self, isin: str) -> str | None:
        """The symbol this source knows an ISIN under, or None."""


class MaturitySource(Protocol):
    """Where the maturity of a T-bill comes from, by its CUSIP."""

    def maturity(self, cusip: str) -> date | None:
        """The day the bill `cusip` matures, or None when the source knows no such bill."""


def rate_symbol(currency: str, base: str) -> str:
    """The symbol under which Yahoo quotes one unit of `currency` in `base`."""
    return f"{currency}{base}=X"


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})  # noqa: S310  # the address is one of the https constants above
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310  # as above
            body: bytes = response.read()
    except urllib.error.HTTPError as exc:
        return exc.read()  # Yahoo explains a refusal in the same JSON as an answer
    except OSError as exc:
        msg = f"no answer from the price source: {exc}"
        raise PriceError(msg) from None
    return body


def _today() -> date:
    return datetime.now(UTC).date()


class Yahoo:
    """Yahoo Finance: unofficial, no key. `fetch` is replaced in tests: they never connect."""

    name = "yahoo"

    def __init__(
        self, fetch: Callable[[str], bytes] = _get, today: Callable[[], date] = _today
    ) -> None:
        self._fetch = fetch
        self._today = today

    def history(self, symbol: str, first: date, last: date) -> History:
        if last < first:
            msg = f"{symbol}: {last} is before {first}"
            raise PriceError(msg)
        # Asked up to today whatever `last` is: a close is adjusted for every split since,
        # and only an answer that reaches today lists them all.
        query = urlencode(
            {
                "period1": _epoch(first),
                "period2": _epoch(max(last, self._today()) + timedelta(days=1)),
                "interval": "1d",
                "events": "split",
            }
        )
        found = parse_chart(self._fetch(f"{_CHART}{quote(symbol, safe='')}?{query}"), symbol)
        closes = tuple((day, close) for day, close in found.closes if first <= day <= last)
        return History(found.currency, closes, found.splits)

    def find(self, isin: str) -> str | None:
        query = urlencode({"q": isin, "quotesCount": 5, "newsCount": 0})
        return parse_search(self._fetch(f"{_SEARCH}{query}"), isin)


class TreasuryDirect:
    """The US Treasury's own list of securities: public, no key. `fetch` is replaced in tests."""

    def __init__(self, fetch: Callable[[str], bytes] = _get) -> None:
        self._fetch = fetch

    def maturity(self, cusip: str) -> date | None:
        query = urlencode({"cusip": cusip, "format": "json"})
        return parse_securities(self._fetch(f"{_SECURITIES}{query}"), cusip)


def _epoch(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=UTC).timestamp())


def _json(payload: bytes, what: str) -> Any:  # noqa: ANN401  # whatever the source sent
    try:
        # Numbers stay decimals, digit for digit: no float is ever made of a price.
        return json.loads(payload, parse_float=Decimal)
    except ValueError:
        msg = f"{what}: the price source did not answer in JSON"
        raise PriceError(msg) from None


def parse_chart(payload: bytes, symbol: str) -> History:
    """Read Yahoo's answer to a chart request.

    Yahoo adjusts every close for the splits that came after it. Those splits are in the same
    answer, so each close is turned back into the price of its day: a stored close then never
    depends on when it was fetched.
    """
    try:
        chart = _json(payload, symbol)["chart"]
        if chart["error"] is not None:
            msg = f"{symbol}: {chart['error']['description']}"
            raise PriceError(msg)
        result = chart["result"][0]
        meta = result["meta"]
        zone = _zone(meta)
        days = [_day(stamp, zone) for stamp in result.get("timestamp", [])]
        closes = result["indicators"]["quote"][0].get("close", [])
        found = (result.get("events") or {}).get("splits", {}).values()
        splits = sorted(
            (Split(_day(split["date"], zone), *_ratio(split, symbol)) for split in found),
            key=lambda split: split.day,
        )
        history = [
            (day, _unadjusted(Decimal(close), day, splits))
            for day, close in zip(days, closes, strict=True)
            if close is not None
        ]
        return History(str(meta["currency"]), tuple(history), tuple(splits))
    except (KeyError, IndexError, TypeError, ValueError, InvalidOperation):
        msg = f"{symbol}: the price source's answer is not in the expected form"
        raise PriceError(msg) from None


def parse_search(payload: bytes, isin: str) -> str | None:
    """Read Yahoo's answer to a search: the first symbol it lists, if any."""
    try:
        quotes = _json(payload, isin)["quotes"]
        return str(quotes[0]["symbol"]) if quotes else None
    except (KeyError, IndexError, TypeError):
        msg = f"{isin}: the price source's answer is not in the expected form"
        raise PriceError(msg) from None


def parse_securities(payload: bytes, cusip: str) -> date | None:
    """Read TreasuryDirect's answer to a search by CUSIP: one entry per auction of the bill
    (a reopened bill has several), all of them maturing on the same day."""
    try:
        found = {
            date.fromisoformat(str(entry["maturityDate"])[:10])
            for entry in _json(payload, cusip)
            if entry["cusip"] == cusip
        }
    except (KeyError, TypeError, ValueError):
        msg = f"{cusip}: TreasuryDirect's answer is not in the expected form"
        raise PriceError(msg) from None
    if len(found) > 1:
        msg = f"{cusip}: TreasuryDirect gives more than one maturity: {sorted(found)}"
        raise PriceError(msg)
    return next(iter(found), None)


def _zone(meta: dict[str, object]) -> tzinfo:
    """The exchange's time zone. `gmtoffset` alone is the offset on the day of the answer:
    it would date the sessions of the other half of the year an hour off, and a London
    session that opens at midnight a day early."""
    name = meta.get("exchangeTimezoneName")
    if name:
        return ZoneInfo(str(name))  # an unknown name is a KeyError: not the expected form
    return timezone(timedelta(seconds=int(str(meta["gmtoffset"]))))


def _day(stamp: object, zone: tzinfo) -> date:
    """The exchange's own date of a session that Yahoo names by its opening, in seconds."""
    return datetime.fromtimestamp(int(str(stamp)), zone).date()


def _ratio(split: dict[str, object], symbol: str) -> tuple[int, int]:
    numerator, denominator = Decimal(str(split["numerator"])), Decimal(str(split["denominator"]))
    whole = numerator == int(numerator) and denominator == int(denominator)
    if not whole or numerator <= 0 or denominator <= 0:
        msg = f"{symbol}: cannot store a split of {numerator} for {denominator}"
        raise PriceError(msg)
    return int(numerator), int(denominator)


def _unadjusted(close: Decimal, day: date, splits: list[Split]) -> Decimal:
    for split in splits:
        if split.day > day:
            close = close * split.numerator / split.denominator
    return significant(close)


def significant(value: Decimal) -> Decimal:
    """`value` to the digits a single-precision float can be trusted for, in plain digits."""
    if value == 0:
        return Decimal(0)
    rounded = value.quantize(Decimal(1).scaleb(value.adjusted() - _DIGITS + 1))
    return rounded.normalize() + Decimal(0)  # + 0: no exponent form
