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


def test_an_export_of_every_currency_is_one_account_per_currency(result: Extraction) -> None:
    euros = (
        "Exchange\tCurrent\t2026-09-07 10:00:00\t2026-09-07 10:00:00\tExchanged to EUR"
        "\t50\t0\tEUR\tCOMPLETED\t62.5\n"
        "Card Payment\tCurrent\t2026-09-08 9:00:00\t2026-09-09 9:00:00\tKonsum"
        "\t-12.5\t0\tEUR\tCOMPLETED\t50\n"
    )
    header, _, chf = TSV.partition("\n")
    merged = revolut.extract(f"{header}\n{euros}{chf}", source_file="all.tsv")
    assert merged.balances == (
        CashBalance("Revolut EUR", "EUR", Decimal("12.5"), Decimal(50)),
        CashBalance("Revolut CHF", "CHF", Decimal("7.13"), Decimal("188.31")),
    )
    check_balances(merged)
    assert [(t.account, t.currency, t.amount) for t in merged.transactions[:2]] == [
        ("Revolut EUR", "EUR", Decimal(50)),
        ("Revolut EUR", "EUR", Decimal("-12.5")),
    ]
    assert [t.amount for t in merged.transactions[2:]] == [t.amount for t in result.transactions]
    assert {t.account for t in merged.transactions[2:]} == {"Revolut CHF"}


def test_csv_export_gives_the_same_result(result: Extraction) -> None:
    assert revolut.extract("\ufeff" + TSV.replace("\t", ","), source_file="chf.tsv") == result


def test_payment_started_last_month_is_booked_on_completion(result: Extraction) -> None:
    assert _row(result, "1") == (date(2026, 9, 1), Decimal(-20), Kind.EXPENSE, "")
    started = next(t.date for t in result.transactions if t.source_ref == "1")
    assert started == date(2026, 8, 31)  # D19: the real date stays the day it started


def test_a_statement_covers_the_days_from_the_first_to_the_last_row_completed() -> None:
    longer = revolut.extract(
        TSV.replace("2026-08-31 18:02:11\t2026-09-01", "2026-08-30 18:02:11\t2026-08-31"),
        source_file="year.tsv",
    )
    assert longer.period == (date(2026, 8, 31), date(2026, 9, 23))
    first = longer.transactions[0]
    assert (first.date, first.booking_date) == (date(2026, 8, 30), date(2026, 8, 31))
    check_balances(longer)


def test_a_partial_month_covers_exactly_its_days_and_reconciles(result: Extraction) -> None:
    assert result.period == (date(2026, 9, 1), date(2026, 9, 23))
    lines = TSV.splitlines()
    part = revolut.extract("\n".join([lines[0], *lines[3:7]]), source_file="p.tsv")
    assert part.period == (date(2026, 9, 6), date(2026, 9, 10))
    assert part.month == date(2026, 9, 1)
    assert all(part.period[0] <= t.booking_date <= part.period[1] for t in part.transactions)
    check_balances(part)


def test_the_booking_date_does_not_depend_on_the_period_covered(result: Extraction) -> None:
    lines = TSV.splitlines()
    part = revolut.extract("\n".join([lines[0], *lines[1:3]]), source_file="p.tsv")
    first = {t.source_ref: t.booking_date for t in part.transactions}
    assert first["1"] == _row(result, "1")[0] == date(2026, 9, 1)


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
    assert _row(result, "6") == (day.replace(day=10), Decimal(-40), Kind.TRANSFER, "Prelievi")
    assert _row(result, "9")[2:] == (Kind.INTERNAL, "")
    assert _row(result, "13")[2:] == (Kind.INTERNAL, "")


def test_the_first_deposit_into_savings_is_internal_under_its_older_name() -> None:
    older = TSV.replace("To Flexible Cash Funds", "Savings Vault topup")
    assert _row(revolut.extract(older, source_file="chf.tsv"), "9")[2:] == (Kind.INTERNAL, "")


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
        # a row in another currency starts that currency's balance and leaves a hole in this one
        (TSV.replace("-7.35\t0\tCHF", "-7.35\t0\tEUR"), ExtractionError, "row 4: balance 81.80"),
        (TSV.replace("Card Refund", "Cashback"), UnsupportedStatementError, "Cashback"),
        (TSV.replace("REVERTED", "DECLINED"), UnsupportedStatementError, "DECLINED"),
        (TSV.replace("Transfer\tDeposit", "Transfer\tPocket"), UnsupportedStatementError, "Pocket"),
    ],
)
def test_malformed_statements_raise(content: str, error: type[Exception], message: str) -> None:
    assert content != TSV
    with pytest.raises(error, match=message):
        revolut.extract(content, source_file="chf.tsv")


def test_exchanging_back_into_francs_is_a_move_between_own_accounts() -> None:
    content = TSV.replace("Exchanged to GBP\t-20", "Exchanged to CHF\t-20")
    assert content != TSV
    rows = revolut.extract(content, source_file="chf.tsv").transactions
    assert (Kind.FX, "Giroconto") in {(t.kind, t.category) for t in rows}


def test_savings_are_recognised_in_italian_too() -> None:
    content = TSV.replace("To Flexible Cash Funds", "To EUR Fondi monetari flessibili")
    assert content != TSV
    rows = revolut.extract(content, source_file="chf.tsv").transactions
    assert [t.kind for t in rows if "Fondi" in t.description] == [Kind.INTERNAL]
