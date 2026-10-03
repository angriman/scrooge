import json
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from finanze.errors import ExtractionError, UnsupportedStatementError
from finanze.extractors import ibkr
from finanze.models import CashBalance, Extraction, Kind, SecurityTrade
from finanze.validation import check_balances

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ibkr"
CSV = (FIXTURES / "activity.csv").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def result() -> Extraction:
    return ibkr.extract(CSV, source_file="activity.csv")


def _totals(result: Extraction, kind: Kind) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for txn in result.transactions:
        if txn.kind is kind:
            totals[txn.currency] += txn.amount
    return dict(totals)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal | date):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def test_matches_golden_file(result: Extraction) -> None:
    actual = [_jsonable(asdict(txn)) for txn in result.transactions]
    expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
    assert actual == expected


def test_month_is_the_first_day_of_the_statement_period(result: Extraction) -> None:
    assert result.month == date(2026, 9, 1)


def test_balances_come_from_the_cash_report(result: Extraction) -> None:
    assert result.balances == (
        CashBalance("IBKR CHF", "CHF", Decimal(1200), Decimal("1696.75")),
        CashBalance("IBKR EUR", "EUR", Decimal(500), Decimal("249.2")),
        CashBalance("IBKR USD", "USD", Decimal(2500), Decimal("4867.61")),
    )


def test_every_account_reconciles_to_the_closing_cash(result: Extraction) -> None:
    check_balances(result)


# Known totals below are the statement's own Cash Report lines, per currency.


def test_commission_totals_match_cash_report(result: Extraction) -> None:
    assert _totals(result, Kind.COMMISSION) == {
        "CHF": Decimal("-3.25"),  # Commissions -2 + Other Fees -1.25
        "EUR": Decimal("-6.8"),
        "USD": Decimal("-6.96"),
    }


def test_dividends_include_payments_in_lieu(result: Extraction) -> None:
    # Cash Report: Dividends 10 + Payment In Lieu of Dividends 4.2
    assert _totals(result, Kind.DIVIDEND) == {"USD": Decimal("14.2")}


def test_withholding_tax_and_interest_match_cash_report(result: Extraction) -> None:
    assert _totals(result, Kind.TAX) == {"USD": Decimal("-2.13")}
    assert _totals(result, Kind.INTEREST) == {"USD": Decimal("12.9")}


def test_trade_cash_flows_match_cash_report(result: Extraction) -> None:
    # Cash Report: Trades (Sales) + Trades (Purchase), all segments
    assert _totals(result, Kind.TRADING) == {"USD": Decimal(905), "EUR": Decimal(156)}
    assert _totals(result, Kind.SECURITY_TRADE) == {"USD": Decimal("-1705.5")}
    assert _totals(result, Kind.FX) == {"CHF": Decimal(-2500), "USD": Decimal(3125)}


def test_transfers_match_cash_report(result: Extraction) -> None:
    assert _totals(result, Kind.TRANSFER) == {
        "CHF": Decimal(3000),
        "EUR": Decimal(-400),
        "USD": Decimal("25.1"),  # Deposits 0.1 + Account Transfers 25
    }


def test_security_trades_carry_asset_tab_fields(result: Extraction) -> None:
    trades = [t for t in result.transactions if isinstance(t, SecurityTrade)]
    assert [(t.ticker, t.date, t.quantity, t.price, t.cost, t.currency) for t in trades] == [
        ("ACME", date(2026, 9, 15), Decimal(20), Decimal("112.4"), Decimal(2248), "USD"),
        ("WIDG", date(2026, 9, 23), Decimal(-10), Decimal("54.25"), Decimal("-542.5"), "USD"),
    ]
    assert {t.account for t in trades} == {"IBKR USD"}
    assert {t.asset_tab for t in trades} == {""}  # resolved later from instruments.yaml (D12)
    assert {t.category for t in trades} == {"Investimenti"}


def test_expired_options_produce_no_zero_amount_rows(result: Extraction) -> None:
    assert all(txn.amount != 0 for txn in result.transactions)
    expired = ("NQ 11SEP26 21000 P", "DAX 18SEP26 23000 P")
    assert not any(e in t.description for e in expired for t in result.transactions)


def test_forex_trade_books_both_legs_and_its_commission_in_chf(result: Extraction) -> None:
    fx = [t for t in result.transactions if t.kind is Kind.FX]
    assert [(t.account, t.amount, t.currency, t.category) for t in fx] == [
        ("IBKR CHF", Decimal(-2500), "CHF", "Acquisto USD"),
        ("IBKR USD", Decimal(3125), "USD", "Acquisto USD"),
    ]
    commission = next(t for t in result.transactions if t.description.endswith("CHF.USD"))
    assert (commission.account, commission.currency) == ("IBKR CHF", "CHF")


def test_deterministic_categories(result: Extraction) -> None:
    expected = {
        Kind.TRADING: "Trading",
        Kind.COMMISSION: "Commissioni",
        Kind.DIVIDEND: "Dividendi",
        Kind.TAX: "Ritenute",
        Kind.INTEREST: "Interessi",
    }
    pairs = {(t.kind, t.category) for t in result.transactions if t.kind in expected}
    assert pairs == set(expected.items())  # every row, not just one per kind


def test_transfer_categories_follow_description_and_sign(result: Extraction) -> None:
    by_amount = {t.amount: t.category for t in result.transactions if t.kind is Kind.TRANSFER}
    assert by_amount[Decimal(3000)] == "Topup IBKR"
    assert by_amount[Decimal(-400)] == "Prelievi"
    # Not deterministic: left blank for user review
    assert by_amount[Decimal(25)] == ""
    assert by_amount[Decimal("0.1")] == ""


def test_dividend_counterparty_is_the_ticker(result: Extraction) -> None:
    tickers = {t.counterparty for t in result.transactions if t.kind in {Kind.DIVIDEND, Kind.TAX}}
    assert tickers == {"ACME", "WIDG"}


def test_every_row_has_a_unique_source_ref_and_the_source_file(result: Extraction) -> None:
    refs = [t.source_ref for t in result.transactions]
    assert len(refs) == len(set(refs))
    assert {t.source_file for t in result.transactions} == {"activity.csv"}


def test_all_dates_fall_inside_the_statement_month(result: Extraction) -> None:
    assert all(date(2026, 9, 1) <= t.date <= date(2026, 9, 30) for t in result.transactions)


def test_rerunning_gives_identical_output(result: Extraction) -> None:
    assert ibkr.extract(CSV, source_file="activity.csv") == result


def test_utf8_bom_is_ignored(result: Extraction) -> None:
    assert ibkr.extract("\ufeff" + CSV, source_file="activity.csv") == result


def test_blank_lines_are_ignored(result: Extraction) -> None:
    spaced = CSV.replace("Trades,Header", "\n\nTrades,Header", 1)
    assert ibkr.extract(spaced, source_file="activity.csv") == result


# ---------------------------------------------------------------- failures must be loud


def _without(prefix: str) -> str:
    return "\n".join(line for line in CSV.splitlines() if not line.startswith(prefix)) + "\n"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (CSV.replace("September 30, 2026", "September 29, 2026"), "calendar month"),
        (CSV.replace("September 1, 2026", "September 2, 2026"), "calendar month"),
        (CSV.replace("September 1, 2026 - ", "September 1, 2026 to "), "period"),
        (CSV.replace("September 1, 2026", "Septembre 1, 2026"), "period"),
        (_without("Statement,Data,Period"), "period"),
        (_without("Cash Report,"), "cash report"),
        (_without("Cash Report,Data,Ending Cash,EUR"), "Ending Cash"),
        (_without("Cash Report,Data,Starting Cash,USD"), "Starting Cash"),
        (CSV.replace("2026-09-15, 10:31:07", "2026-10-15, 10:31:07"), "outside"),
        (CSV.replace(",-2248,", ",abc,"), "amount"),
        (CSV.replace(",Order,Stocks,", ",ClosedLot,Stocks,"), "discriminator"),
        (CSV.replace("T. Price", "Trade Price"), "no column 'T. Price'"),
        (CSV.replace("Comm/Fee", "Fee"), "no commission column"),
        (CSV.replace("CHF.USD", "CHF.EUR"), "quote currency"),
        (CSV.replace(",2026-09-25,ACME(", ",25.09.2026,ACME("), "date"),
    ],
)
def test_malformed_statements_raise(content: str, message: str) -> None:
    with pytest.raises(ExtractionError, match=f"(?i){message}"):
        ibkr.extract(content, source_file="activity.csv")


def test_data_row_without_header_raises() -> None:
    with pytest.raises(ExtractionError, match="header"):
        ibkr.extract("Trades,Data,Order,Stocks\n", source_file="x.csv")


@pytest.mark.parametrize("category", ["Treasury Bills", "Bonds", "Warrants"])
def test_unsupported_asset_categories_fail_instead_of_guessing(category: str) -> None:
    content = CSV.replace(",Order,Stocks,", f",Order,{category},")
    with pytest.raises(UnsupportedStatementError, match=category):
        ibkr.extract(content, source_file="activity.csv")
