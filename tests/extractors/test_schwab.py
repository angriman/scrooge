from datetime import date
from decimal import Decimal

import pytest

from finanze.errors import ExtractionError, UnsupportedStatementError
from finanze.extractors import schwab
from finanze.models import Extraction, Kind, SecurityTrade
from finanze.validation import check_balances
from tests.extractors.golden import FIXTURES, assert_matches_golden

CSV = (FIXTURES / "schwab" / "transactions.csv").read_text(encoding="utf-8")
HEADER = CSV.splitlines()[0]


@pytest.fixture(scope="module")
def result() -> Extraction:
    return schwab.extract(CSV, source_file="transactions.csv")


def test_matches_golden_file(result: Extraction) -> None:
    assert_matches_golden(result, "schwab")


def test_the_export_covers_its_first_to_its_last_row_and_checks_nothing(
    result: Extraction,
) -> None:
    assert result.period == (date(2026, 9, 29), date(2026, 10, 28))
    assert result.month == date(2026, 9, 1)
    assert (result.balances, result.holdings) == ((), None)
    assert result.unreconciled == ("Schwab",)
    check_balances(result)  # an unreconciled account needs no balance


def test_a_deposit_is_salary_in_shares_bought_at_the_vest_price(result: Extraction) -> None:
    salary, purchase = result.transactions[:2]
    assert (salary.kind, salary.category, salary.amount) == (
        Kind.INCOME,
        "Stipendio",
        Decimal("596.36880"),
    )
    assert isinstance(purchase, SecurityTrade)
    assert (purchase.ticker, purchase.quantity, purchase.price) == (
        "ACME",
        Decimal("5.085"),
        Decimal("117.28"),
    )
    assert purchase.amount == -salary.amount  # no cash moves
    assert purchase.cost == salary.amount  # what they cost is what they were worth
    assert purchase.asset_tab == "Stocks"
    assert (
        purchase.description == "Deposit of 5.085 ACME @ 117.28 (award C0000001, vested 2026-10-25)"
    )
    assert sum(t.amount for t in result.transactions if t.kind is Kind.INCOME) == sum(
        -t.amount for t in result.transactions if t.kind is Kind.SECURITY_TRADE
    )


def test_dividends_and_the_tax_withheld_are_cash(result: Extraction) -> None:
    cash = {t.kind: t for t in result.transactions if t.kind in {Kind.DIVIDEND, Kind.TAX}}
    assert (cash[Kind.DIVIDEND].amount, cash[Kind.DIVIDEND].category) == (
        Decimal("1091.67"),
        "Dividendi",
    )
    assert (cash[Kind.TAX].amount, cash[Kind.TAX].category) == (Decimal("-163.75"), "Ritenute")
    assert cash[Kind.DIVIDEND].counterparty == "ACME"


def test_the_shares_that_vested_add_up(result: Extraction) -> None:
    shares = [t for t in result.transactions if isinstance(t, SecurityTrade)]
    assert sum(t.quantity for t in shares) == Decimal("20.388")


def test_every_row_has_a_unique_source_ref(result: Extraction) -> None:
    refs = [t.source_ref for t in result.transactions]
    assert len(refs) == len(set(refs))


DIVIDEND = '"10/14/2026","Dividend","ACME","Credit","","","","$1,091.67","","","",""'
DEPOSIT = '"10/28/2026","Deposit","ACME","RS","5.085","","","","","","",""'
DETAILS = '"","","","","","","","","03/04/2026","C0000001","10/25/2026","$117.28"'


@pytest.mark.parametrize(
    ("rows", "error", "message"),
    [
        ([DIVIDEND.replace("Dividend", "Sale")], UnsupportedStatementError, "action 'Sale'"),
        (
            [DIVIDEND.replace('"Credit","","","","$', '"Credit","","$1.00","","$')],
            UnsupportedStatementError,
            "fees and commissions",
        ),
        ([DEPOSIT], ExtractionError, "Deposit without the award details"),
        ([DEPOSIT, DIVIDEND], ExtractionError, "Deposit without the award details"),
        ([DETAILS], ExtractionError, "award details with no Deposit"),
        ([DEPOSIT, DETAILS.replace("$117.28", "$0")], ExtractionError, "at 0"),
        ([DEPOSIT.replace("5.085", "-1"), DETAILS], ExtractionError, "a Deposit of -1"),
        (
            [DEPOSIT.replace('"","","","",""', '"","","$5.00","",""', 1), DETAILS],
            UnsupportedStatementError,
            "a Deposit with cash",
        ),
        (
            [DEPOSIT, DETAILS.replace('"","","","","03/04', '"","","","$1.00","03/04')],
            UnsupportedStatementError,
            "a Deposit with cash",
        ),
        ([DEPOSIT, DETAILS.replace("C0000001", "")], UnsupportedStatementError, "no award id"),
        ([DIVIDEND.replace("$1,091.67", "1$")], ExtractionError, "cannot read amount '1\\$'"),
        ([DIVIDEND.replace("$1,091.67", "$1,09")], ExtractionError, "cannot read amount"),
        ([DIVIDEND.replace("10/14/2026", "2026-09-14")], ExtractionError, "cannot read date"),
        ([DIVIDEND.replace("10/14/2026", "02/30/2026")], ExtractionError, "cannot read date"),
        ([], ExtractionError, "has no rows"),
    ],
)
def test_anything_unexpected_is_refused(
    rows: list[str], error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        schwab.extract("\n".join([HEADER, *rows]) + "\n", source_file="t.csv")


def test_a_missing_column_is_refused() -> None:
    with pytest.raises(ExtractionError, match="missing column"):
        schwab.extract(CSV.replace('"VestFairMarketValue"', '"FMV"'), source_file="t.csv")


def test_the_export_covers_up_to_the_day_its_name_says_it_was_made() -> None:
    text = (FIXTURES / "schwab" / "transactions.csv").read_text(encoding="utf-8")
    named = "EquityAwardsCenter_Transactions_20261121220549.csv"
    assert schwab.extract(text, source_file=named).period == (date(2026, 9, 29), date(2026, 11, 21))
    # A name that holds no day, or one before the last row, leaves the last row's day.
    for other in ("x_20261341000000.csv", "x_20261001000000.csv", "transactions.csv"):
        assert schwab.extract(text, source_file=other).period[1] == date(2026, 10, 28)
