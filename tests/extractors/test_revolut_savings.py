from datetime import date
from decimal import Decimal

import pytest

from scrooge.errors import ExtractionError, UnsupportedStatementError
from scrooge.extractors import revolut_savings
from scrooge.models import Extraction, Kind
from scrooge.validation import check_balances
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
        # 0.0812 + 0.0574 + 0.04 PAID, and the 0.09 bought on 19 Sep that no transfer paid for
        (Kind.INTEREST, "Interessi"): Decimal("0.2686"),
        (Kind.COMMISSION, "Commissioni"): Decimal("-0.0086"),  # Service Fee -0.0050 - 0.0036
    }
    assert {(t.account, t.currency) for t in result.transactions} == {("Revolut EUR", "EUR")}


def test_fund_movements_are_ignored(result: Extraction) -> None:
    # 3 and 7 are a purchase and a sale, 8 buys the return reinvested in 10, 11 withdraws one
    assert [t.source_ref for t in result.transactions] == ["1", "2", "4", "5", "6", "9"]


def _buys(*rows: tuple[str, str, str]) -> list[str]:
    """The descriptions of what is booked of these rows: (day in September, description, value)."""
    header = TSV.partition("\n")[0]
    lines = [f"Sep {day}, 2026\t{text}\t{value}\t0\t1\t1\t{value}" for day, text, value in rows]
    content = "\n".join([header, *lines]) + "\n"
    found = revolut_savings.extract(content, source_file="s.tsv").transactions
    return [f"{t.date.day} {t.amount}" for t in found]


def test_a_reinvested_return_explains_one_purchase_of_as_much() -> None:
    reinvested = ("1", "Return Reinvested Class R EUR IE0000000001", "-0.12")
    buy = "BUY EUR Class R IE0000000001"
    assert _buys(("3", buy, "0.12"), reinvested) == []
    assert _buys(("4", buy, "0.12"), ("3", buy, "0.12"), reinvested) == ["4 0.12"]
    assert _buys(("9", buy, "0.12"), reinvested) == ["9 0.12"]  # more than a week after it
    assert _buys(("3", buy, "0.12"), ("4", reinvested[1], "-0.12")) == ["3 0.12"]  # before it


def test_a_purchase_of_one_unit_or_more_is_a_transfer() -> None:
    assert _buys(("3", "BUY EUR Class R IE0000000001", "1")) == []
    assert _buys(("3", "BUY EUR Class R IE0000000001", "0.99")) == ["3 0.99"]


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


def test_a_partial_month_covers_exactly_its_days() -> None:
    lines = TSV.splitlines()
    kept = [
        line for line in lines[1:] if line.startswith(("Sep 15,", "Sep 2,", "Sep 1,", "Sep 29,"))
    ]
    part = revolut_savings.extract("\n".join([lines[0], *kept]) + "\n", source_file="p.tsv")
    assert part.period == (date(2026, 9, 1), date(2026, 9, 29))
    assert part.month == date(2026, 9, 1)


def test_a_statement_of_several_months_and_reinvested_returns() -> None:
    cells = ["Aug 2, 2026, 2:10:47 AM", "Return Reinvested Class R EUR IE0000000001"]
    reinvested = "\t".join([*cells, "0.04", "0.03", "0.95", "1", "0.04"]) + "\n"
    longer = revolut_savings.extract(TSV + reinvested, source_file="year.tsv")
    assert longer.period == (date(2026, 8, 2), date(2026, 9, 30))
    assert len(longer.transactions) == len(
        revolut_savings.extract(TSV, source_file="s.tsv").transactions
    )
