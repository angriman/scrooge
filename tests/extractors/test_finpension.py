from datetime import date
from decimal import Decimal

import pytest

from finanze.errors import ExtractionError, UnsupportedStatementError
from finanze.extractors import finpension
from finanze.models import CashBalance, Extraction, Kind, SecurityTrade
from finanze.validation import check_balances
from tests.extractors.golden import FIXTURES, assert_matches_golden

TSV = (FIXTURES / "finpension" / "transactions.tsv").read_text(encoding="utf-8")
SWISS = "ALPHA (CH) Index Fund - Equities Switzerland I-acc"


@pytest.fixture(scope="module")
def result() -> Extraction:
    return finpension.extract(TSV, source_file="transactions.tsv")


def test_matches_golden_file(result: Extraction) -> None:
    assert_matches_golden(result, "finpension")


def test_the_report_covers_the_days_from_first_to_last_row(result: Extraction) -> None:
    assert result.period == (date(2026, 7, 6), date(2026, 9, 16))
    assert {t.account for t in result.transactions} == {"Finpension 3a"}
    assert all(t.date == t.booking_date for t in result.transactions)


def test_the_running_balance_reconciles_with_six_decimals(result: Extraction) -> None:
    assert result.balances == (
        CashBalance("Finpension 3a", "CHF", Decimal(0), Decimal("13.671078")),
    )
    check_balances(result)


def test_buys_and_sells_are_fund_trades_by_isin(result: Extraction) -> None:
    trades = [t for t in result.transactions if isinstance(t, SecurityTrade)]
    assert [(t.ticker, t.quantity, t.price, t.amount, t.asset_tab) for t in trades] == [
        ("CH0000000011", Decimal("0.1"), Decimal("1500.5"), Decimal("-150.05"), "Funds"),
        ("CH0000000029", Decimal(1), Decimal("1300.25"), Decimal("-1300.25"), "Funds"),
        ("CH0000000029", Decimal("-0.02"), Decimal(1310), Decimal("26.2"), "Funds"),
        ("CH0000000011", Decimal("0.05"), Decimal(1510), Decimal("-75.5"), "Funds"),
    ]
    assert {(t.kind, t.category) for t in trades} == {(Kind.SECURITY_TRADE, "Investimenti")}
    assert all(t.cost == -t.amount for t in trades)
    assert trades[0].description == f"Buy: {SWISS} (0.1 @ 1500.5)"
    world = "ALPHA (CH) Index Fund - Equities World ex CH I-acc"
    assert {t.ticker: t.name for t in trades} == {"CH0000000011": SWISS, "CH0000000029": world}


def test_cash_rows_are_classified(result: Extraction) -> None:
    by_ref = {t.source_ref: t for t in result.transactions}
    assert (by_ref["1"].kind, by_ref["1"].category) == (Kind.TRANSFER, "Investimenti")
    assert (by_ref["4"].kind, by_ref["4"].category) == (Kind.COMMISSION, "Commissioni")
    assert (by_ref["5"].kind, by_ref["5"].category) == (Kind.DIVIDEND, "Dividendi")
    assert (by_ref["5"].counterparty, by_ref["5"].amount) == ("CH0000000029", Decimal("12.345678"))
    assert (by_ref["7"].kind, by_ref["7"].category) == (Kind.INTEREST, "Interessi")
    assert by_ref["7"].description == "Interests"


def test_a_transfer_from_another_provider_is_a_transfer_like_a_deposit() -> None:
    moved = finpension.extract(TSV.replace("\tDeposit\t", "\tTransfer\t", 1), source_file="t.tsv")
    first = moved.transactions[0]
    assert (first.kind, first.category, first.amount) == (
        Kind.TRANSFER,
        "Investimenti",
        Decimal(1500),
    )


@pytest.mark.parametrize(
    "row",
    [
        "2026-07-06\tTransfer\tALPHA\tCH0000000011\t1\tCHF\t1\t1500\t0\t0",  # fund units
        "2026-07-06\tTransfer\t\t\t\tCHF\t1\t\t-1500\t-1500",  # money out
    ],
)
def test_a_transfer_of_units_or_out_is_refused(row: str) -> None:
    header = TSV.splitlines()[0]
    with pytest.raises(UnsupportedStatementError, match="only a transfer of cash in"):
        finpension.extract(f"{header}\n{row}\n", source_file="t.tsv")


def test_a_report_of_one_month_and_other_delimiters_are_read() -> None:
    header, first = TSV.splitlines()[:2]
    for delimiter in (";", ","):
        text = f"{header}\n{first}\n".replace("\t", delimiter)
        short = finpension.extract("﻿" + text, source_file="t.csv")
        assert short.period == (date(2026, 7, 6), date(2026, 7, 6))
        assert [t.amount for t in short.transactions] == [Decimal(1500)]


@pytest.mark.parametrize(
    ("old", "new", "error", "message"),
    [
        ("\t49.7\n", "\t49.71\n", ExtractionError, "row 3: balance 49.71 does not follow"),
        ("\t0.1\tCHF", "\t0.2\tCHF", ExtractionError, "0.2 x 1500.5 is not the cash flow"),
        ("\t-0.02\tCHF", "\t0.02\tCHF", ExtractionError, "0.02 units is the wrong sign for a Sell"),
        (f"{SWISS}\tCH0000000011\t0.1", f"{SWISS}\t\t0.1", ExtractionError, "Buy without an ISIN"),
        ("Interests", "Bonus", UnsupportedStatementError, "unsupported category 'Bonus'"),
        ("CHF\t1\t\t0.11", "CHF\t0.95\t\t0.11", UnsupportedStatementError, "currency rate '0.95'"),
        ("2026-09-01", "2026-06-01", ExtractionError, "not in date order"),
        ("\tBalance", "\tSaldo", ExtractionError, "missing column"),
    ],
)
def test_anything_unexpected_is_refused(
    old: str, new: str, error: type[Exception], message: str
) -> None:
    content = TSV.replace(old, new, 1)
    assert content != TSV
    with pytest.raises(error, match=message):
        finpension.extract(content, source_file="t.tsv")


def test_cash_rounded_to_six_decimals_still_matches_units_times_price() -> None:
    line = "\t0.1\tCHF\t1\t1500.5\t-150.05\t1349.95"
    finer = TSV.replace(line, "\t0.1\tCHF\t1\t1500.500004\t-150.05\t1349.95")
    assert finer != TSV
    assert len(finpension.extract(finer, source_file="t.tsv").transactions) == 9


def test_a_rate_of_one_is_accepted_however_it_is_written() -> None:
    padded = TSV.replace("\tCHF\t1\t", "\tCHF\t1.0000000000\t")
    assert padded != TSV
    assert len(finpension.extract(padded, source_file="t.tsv").transactions) == 9


def test_a_numbered_download_is_a_portfolio_of_its_own() -> None:
    second = finpension.extract(TSV, source_file="2022/transaction_report-2.tsv")
    assert {t.account for t in second.transactions} == {"Finpension 3a 2"}
    assert second.balances[0].account == "Finpension 3a 2"
    plain = finpension.extract(TSV, source_file="pillar 3a.tsv")
    assert plain.balances[0].account == "Finpension 3a"


def test_a_report_without_rows_is_refused() -> None:
    with pytest.raises(ExtractionError, match="no rows"):
        finpension.extract(TSV.splitlines()[0] + "\n", source_file="t.tsv")
