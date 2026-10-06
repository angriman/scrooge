"""Security trades as rows of the asset tabs' trade logs (ETFs, Stocks, Bonds).

The rows are pasted by hand below each log, which also holds manual entries: nothing here knows
what the sheet already contains, so a month pasted twice shows up twice.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finanze.models import SecurityTrade

Value = str | Decimal | date
Row = Sequence[Value]

_THOUSAND = Decimal(1000)


class UnknownTabError(Exception):
    """A trade names an asset tab this writer has no layout for."""


def _etf_row(trade: SecurityTrade) -> Row:
    day = trade.booking_date
    return [trade.ticker, day, trade.quantity, trade.price, trade.cost, trade.currency]


def _stock_row(trade: SecurityTrade) -> Row:  # the Stocks log has no price column
    return [trade.ticker, trade.booking_date, trade.quantity, trade.currency]


def bond_name(maturity: date) -> str:
    """Short name used in the Bonds tab, e.g. `US-T Govt Bill Dec24'26`."""
    return f"US-T Govt Bill {maturity:%b}{maturity.day}'{maturity:%y}"


def _bond_row(trade: SecurityTrade) -> Row:
    if trade.maturity is None:
        msg = f"{trade.ticker}: a Bonds trade needs a maturity date"
        raise UnknownTabError(msg)
    return [
        bond_name(trade.maturity),
        trade.price,  # % of face
        trade.quantity / _THOUSAND,  # face value / 1000
        trade.cost,
        trade.booking_date,
        trade.maturity,
    ]


@dataclass(frozen=True, slots=True)
class _Layout:
    first_row: int
    row: Callable[[SecurityTrade], Row]


_LAYOUTS = {
    "ETFs": _Layout(27, _etf_row),
    "Stocks": _Layout(13, _stock_row),
    "Bonds": _Layout(11, _bond_row),
}


@dataclass(frozen=True, slots=True)
class Log:
    tab: str
    first_row: int  # sheet row the tab's trade log starts on
    rows: tuple[Row, ...]


def logs(trades: Iterable[SecurityTrade]) -> tuple[Log, ...]:
    """The new rows of each tab's log, oldest trade first; tabs without trades are left out."""
    by_tab: dict[str, list[SecurityTrade]] = {}
    for trade in trades:
        if trade.asset_tab not in _LAYOUTS:
            msg = f"{trade.ticker}: unknown asset tab {trade.asset_tab!r}"
            raise UnknownTabError(msg)
        by_tab.setdefault(trade.asset_tab, []).append(trade)
    return tuple(
        Log(
            tab,
            layout.first_row,
            tuple(layout.row(trade) for trade in sorted(by_tab[tab], key=lambda t: t.booking_date)),
        )
        for tab, layout in _LAYOUTS.items()
        if tab in by_tab
    )
