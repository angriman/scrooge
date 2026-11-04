"""A price source that never connects: tests hand it the closes it knows (D9)."""

from datetime import date
from decimal import Decimal

from finanze.prices import History, PriceError, Split


class FakeSource:
    """Closes per symbol, as (day, close) pairs; everything else is unknown to it."""

    name = "fake"

    def __init__(
        self,
        closes: dict[str, list[tuple[date, str]]] | None = None,
        *,
        currencies: dict[str, str] | None = None,
        isins: dict[str, str] | None = None,
        splits: dict[str, list[Split]] | None = None,
    ) -> None:
        self.closes = closes or {}
        self.currencies = currencies or {}
        self.isins = isins or {}
        self.splits = splits or {}
        self.asked: list[tuple[str, date, date]] = []

    def history(self, symbol: str, first: date, last: date) -> History:
        self.asked.append((symbol, first, last))
        if symbol not in self.closes:
            msg = f"{symbol}: No data found"
            raise PriceError(msg)
        closes = tuple(
            (day, Decimal(close)) for day, close in self.closes[symbol] if first <= day <= last
        )
        currency = self.currencies.get(symbol, "CHF" if symbol.endswith("CHF=X") else "USD")
        return History(currency, closes, tuple(self.splits.get(symbol, ())))

    def find(self, isin: str) -> str | None:
        return self.isins.get(isin)


class FakeMaturities:
    """When the bills it knows mature; it fails like an unreachable source when told to."""

    def __init__(self, known: dict[str, date] | None = None, *, down: bool = False) -> None:
        self.known = known or {}
        self.down = down
        self.asked: list[str] = []

    def maturity(self, cusip: str) -> date | None:
        self.asked.append(cusip)
        if self.down:
            msg = "no answer from the price source: timed out"
            raise PriceError(msg)
        return self.known.get(cusip)
