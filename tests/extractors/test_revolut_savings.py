from datetime import date
from decimal import Decimal

import pytest

from finanze.errors import ExtractionError, UnsupportedStatementError
from finanze.extractors import revolut_savings
from finanze.models import Extraction, Kind
from finanze.validation import check_balances
from tests.extractors.golden import FIXTURES, assert_matches_golden

TSV = (FIXTURES / "revolut_savings" / "savings.tsv").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def result() -> Extraction:
    return revolut_savings.extract(TSV, source_file="savings.tsv")


def test_matches_golden_file(result: Extraction) -> None:
    assert_matches_golden(result, "revolut_savings")


def test_interest_and_the_fee_on_it_are_booked_on_revolut_eur(result: Extraction) -> None:
    totals: dict[tuple[Kind, str], Decimal] = {}
    for t in result.transactions:
        totals[t.kind, t.category] = totals.get((t.kind, t.category), Decimal(0)) + t.amount
    assert totals == {
        (Kind.INTEREST, "Interessi"): Decimal("0.1786"),  # 0.0812 + 0.0574 + 0.04 PAID
        (Kind.COMMISSION, "Commissioni"): Decimal("-0.0086"),  # Service Fee -0.0050 - 0.0036
    }
    assert {(t.account, t.currency) for t in result.transactions} == {("Revolut EUR", "EUR")}


def test_fund_movements_are_ignored(result: Extraction) -> None:
    assert [t.source_ref for t in result.transactions] == ["1", "2", "4", "5", "7"]


def test_month_and_dates(result: Extraction) -> None:
    assert result.month == date(2026, 9, 1)
    assert result.transactions[0].date == date(2026, 9, 30)


def test_account_is_reported_as_unreconciled(result: Extraction) -> None:
    assert result.unreconciled == ("Revolut EUR",)
    assert result.balances == ()
    check_balances(result)


@pytest.mark.parametrize(
    ("content", "error", "message"),
    [
        (TSV.replace("Value, EUR\tValue, CHF", "EUR\tCHF"), ExtractionError, "Value"),
        (TSV.replace("Sep 2, 2026", "Sept 2, 2026"), ExtractionError, "date"),
        (TSV.replace("Sep 2, 2026", "Sep 31, 2026"), ExtractionError, "date"),
        (TSV.replace("Sep 2, 2026", "Foo 2, 2026"), ExtractionError, "date"),
        (TSV.replace("\t0.0812\t", "\tn/a\t"), ExtractionError, "amount"),
        (TSV.replace("Interest PAID", "Bonus PAID"), UnsupportedStatementError, "Bonus PAID"),
    ],
)
def test_malformed_statements_raise(content: str, error: type[Exception], message: str) -> None:
    assert content != TSV
    with pytest.raises(error, match=message):
        revolut_savings.extract(content, source_file="savings.tsv")


def test_a_statement_of_several_months_and_reinvested_returns() -> None:
    cells = ["Aug 2, 2026, 2:10:47 AM", "Return Reinvested Class R EUR IE0000000001"]
    reinvested = "\t".join([*cells, "0.04", "0.03", "0.95", "1", "0.04"]) + "\n"
    longer = revolut_savings.extract(TSV + reinvested, source_file="year.tsv")
    assert longer.period == (date(2026, 8, 1), date(2026, 9, 30))
    assert len(longer.transactions) == len(
        revolut_savings.extract(TSV, source_file="s.tsv").transactions
    )
