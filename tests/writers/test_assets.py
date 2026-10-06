from datetime import date
from decimal import Decimal

import pytest

from finanze.models import Kind, SecurityTrade
from finanze.writers import assets as writer


def _trade(
    ticker: str,
    tab: str,
    day: date,
    quantity: str,
    price: str,
    *,
    cost: str,
    currency: str = "USD",
    maturity: date | None = None,
) -> SecurityTrade:
    return SecurityTrade(
        date=day,
        account=f"IBKR {currency}",
        amount=-Decimal(cost),
        currency=currency,
        description="t",
        kind=Kind.SECURITY_TRADE,
        source_file="f",
        source_ref="r",
        category="Investimenti",
        ticker=ticker,
        quantity=Decimal(quantity),
        price=Decimal(price),
        cost=Decimal(cost),
        asset_tab=tab,
        maturity=maturity,
    )


VT = _trade("VT", "ETFs", date(2026, 9, 15), "48", "148.375", cost="7122")
VT_SELL = _trade("VT", "ETFs", date(2026, 9, 20), "-10", "150", cost="-1500")
GOOG = _trade("GOOG", "Stocks", date(2026, 9, 16), "5", "200.5", cost="1002.5")
BILL = _trade(
    "912797ZZ1",
    "Bonds",
    date(2026, 9, 9),
    "40000",
    "98.25",
    cost="39300.00",
    maturity=date(2026, 12, 24),
)


def test_trades_become_rows_of_their_tab_oldest_first() -> None:
    etfs, stocks, bonds = writer.logs([VT_SELL, GOOG, BILL, VT])
    assert (etfs.tab, etfs.first_row) == ("ETFs", 27)
    assert etfs.rows == (
        ["VT", date(2026, 9, 15), Decimal(48), Decimal("148.375"), Decimal(7122), "USD"],
        ["VT", date(2026, 9, 20), Decimal(-10), Decimal(150), Decimal(-1500), "USD"],  # D11
    )
    assert (stocks.tab, stocks.first_row) == ("Stocks", 13)
    assert stocks.rows == (["GOOG", date(2026, 9, 16), Decimal(5), "USD"],)  # no price column
    assert (bonds.tab, bonds.first_row) == ("Bonds", 11)
    assert bonds.rows == (
        [
            "US-T Govt Bill Dec24'26",
            Decimal("98.25"),
            Decimal(40),  # face value / 1000
            Decimal("39300.00"),
            date(2026, 9, 9),
            date(2026, 12, 24),
        ],
    )


def test_identical_fills_are_each_kept() -> None:
    (etfs,) = writer.logs([VT, VT])
    assert len(etfs.rows) == 2


def test_tabs_without_trades_are_left_out() -> None:
    assert writer.logs([]) == ()
    assert [log.tab for log in writer.logs([GOOG])] == ["Stocks"]


def test_bond_name_follows_the_sheet_convention() -> None:
    assert writer.bond_name(date(2026, 12, 31)) == "US-T Govt Bill Dec31'26"
    assert writer.bond_name(date(2027, 1, 7)) == "US-T Govt Bill Jan7'27"


def test_unknown_tab_or_bond_without_maturity_blocks() -> None:
    with pytest.raises(writer.UnknownTabError, match="unknown asset tab 'Crypto'"):
        writer.logs([_trade("BTC", "Crypto", date(2026, 9, 1), "1", "1", cost="1")])
    with pytest.raises(writer.UnknownTabError, match="needs a maturity"):
        writer.logs([_trade("X", "Bonds", date(2026, 9, 1), "1000", "99", cost="990")])
