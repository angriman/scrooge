from datetime import date
from decimal import Decimal

import pytest

from finanze.errors import ExtractionError, UnsupportedStatementError
from finanze.extractors import revolut
from finanze.models import CashBalance, Extraction, Kind
from finanze.validation import check_balances
from tests.extractors.golden import FIXTURES, assert_matches_golden

TSV = (FIXTURES / "revolut" / "chf.tsv").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def result() -> Extraction:
    return revolut.extract(TSV, source_file="chf.tsv")


def _row(result: Extraction, ref: str) -> tuple[date, Decimal, Kind, str]:
    txn = next(t for t in result.transactions if t.source_ref == ref)
    return txn.booking_date, txn.amount, txn.kind, txn.category


def test_matches_golden_file(result: Extraction) -> None:
    assert_matches_golden(result, "revolut")


def test_balances_are_the_ends_of_the_running_balance(result: Extraction) -> None:
    assert result.month == date(2026, 9, 1)
    assert result.balances == (
        CashBalance("Revolut CHF", "CHF", Decimal("7.13"), Decimal("188.31")),
    )
    check_balances(result)


def test_csv_export_gives_the_same_result(result: Extraction) -> None:
    assert revolut.extract("\ufeff" + TSV.replace("\t", ","), source_file="chf.tsv") == result


def test_payment_started_last_month_is_booked_on_completion(result: Extraction) -> None:
    assert _row(result, "1") == (date(2026, 9, 1), Decimal(-20), Kind.EXPENSE, "")
    started = next(t.date for t in result.transactions if t.source_ref == "1")
    assert started == date(2026, 8, 31)  # D19: the real date stays the day it started


def test_month_is_the_completion_month_even_when_every_row_started_earlier() -> None:
    header, first = TSV.splitlines()[:2]  # started Aug 31, completed Sep 1
    sparse = revolut.extract(f"{header}\n{first}\n", source_file="usd.tsv")
    assert sparse.month == date(2026, 9, 1)
    assert [t.booking_date for t in sparse.transactions] == [date(2026, 9, 1)]


def test_pending_reverted_and_savings_mirror_rows_are_skipped(result: Extraction) -> None:
    assert {"10", "11", "17"}.isdisjoint(t.source_ref for t in result.transactions)


def test_deterministic_categories(result: Extraction) -> None:
    day = date(2026, 9, 1)
    assert _row(result, "2") == (day.replace(day=4), Decimal(150), Kind.TRANSFER, "Topup Revolut")
    assert _row(result, "14") == (day.replace(day=21), Decimal(50), Kind.TRANSFER, "Topup Revolut")
    assert _row(result, "16") == (day.replace(day=23), Decimal(30), Kind.TRANSFER, "Topup Revolut")
    assert _row(result, "4") == (day.replace(day=7), Decimal("-47.5"), Kind.FX, "Acquisto EUR")
    assert _row(result, "15") == (day.replace(day=22), Decimal(-20), Kind.FX, "")  # no GBP category
    assert _row(result, "6") == (day.replace(day=9), Decimal(-40), Kind.TRANSFER, "Prelievi")
    assert _row(result, "9")[2:] == (Kind.INTERNAL, "")
    assert _row(result, "13")[2:] == (Kind.INTERNAL, "")


def test_everything_else_is_income_or_expense_left_for_the_rules(result: Extraction) -> None:
    assert _row(result, "3")[2:] == (Kind.EXPENSE, "")
    assert _row(result, "7")[2:] == (Kind.INCOME, "")  # card refund
    assert _row(result, "8")[2:] == (Kind.EXPENSE, "")  # transfer to a person
    assert _row(result, "12")[2:] == (Kind.INCOME, "")


def test_fees_are_separate_rows(result: Extraction) -> None:
    assert _row(result, "4:fee")[1:] == (Decimal("-0.48"), Kind.COMMISSION, "Commissioni")
    # a plan charge has no amount, only a fee: that fee is the expense
    assert _row(result, "5:fee")[1:] == (Decimal("-18.99"), Kind.EXPENSE, "")
    assert "5" not in {t.source_ref for t in result.transactions}


@pytest.mark.parametrize(
    ("content", "error", "message"),
    [
        (TSV.replace("\t129.78\n", "\t129.77\n"), ExtractionError, "does not follow"),
        (TSV.replace("\tBalance\n", "\tSaldo\n"), ExtractionError, "missing column"),
        (TSV.replace("COMPLETED", "PENDING"), ExtractionError, "no completed rows"),
        (TSV.replace("-7.35\t0\tCHF", "-7.35\t0\tEUR"), ExtractionError, "mixes currencies"),
        (
            TSV.replace("2026-08-31 18:02:11\t2026-09-01", "2026-08-30 18:02:11\t2026-08-31"),
            ExtractionError,
            "neither started nor completed",
        ),
        (TSV.replace("Card Refund", "Cashback"), UnsupportedStatementError, "Cashback"),
        (TSV.replace("REVERTED", "DECLINED"), UnsupportedStatementError, "DECLINED"),
        (TSV.replace("Transfer\tDeposit", "Transfer\tPocket"), UnsupportedStatementError, "Pocket"),
    ],
)
def test_malformed_statements_raise(content: str, error: type[Exception], message: str) -> None:
    assert content != TSV
    with pytest.raises(error, match=message):
        revolut.extract(content, source_file="chf.tsv")
