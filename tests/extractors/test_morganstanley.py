from datetime import date
from decimal import Decimal

import pytest

from scrooge.errors import ExtractionError, UnsupportedStatementError
from scrooge.extractors import morganstanley
from scrooge.models import CashBalance, Extraction, Holding, Kind, PrintedValue, SecurityTrade
from scrooge.validation import check_balances
from tests.extractors.golden import FIXTURES, assert_matches_golden

TEXT = (FIXTURES / "morganstanley" / "statement.txt").read_text(encoding="utf-8")
LAST_ROW = "9/25/26 Release 3.100 155.2500\n"


def read(text: str) -> Extraction:
    return morganstanley.extract(text, source_file="statement.txt")


def edited(old: str, new: str) -> str:
    assert TEXT.count(old) == 1
    return TEXT.replace(old, new)


@pytest.fixture(scope="module")
def result() -> Extraction:
    return read(TEXT)


def test_matches_golden_file(result: Extraction) -> None:
    assert_matches_golden(result, "morganstanley")


def test_the_quarter_is_the_period_covered(result: Extraction) -> None:
    assert result.period == (date(2026, 7, 1), date(2026, 9, 30))
    assert result.month == date(2026, 7, 1)
    assert result.account_id == "MSX00000001"


def test_cash_reconciles_with_the_printed_balances(result: Extraction) -> None:
    assert result.balances == (
        CashBalance("Morgan Stanley", "USD", Decimal("0.10"), Decimal("0.00")),
    )
    check_balances(result)


def test_the_shares_held_and_the_statement_s_own_figures(result: Extraction) -> None:
    assert result.holdings == (
        Holding(
            "Morgan Stanley",
            "GOOG",
            "USD",
            Decimal("4.135"),
            Decimal("158.4000"),
            Decimal("654.98"),
            asset_tab="Stocks",
        ),
    )
    assert result.values == (
        PrintedValue("Stock", "USD", Decimal("654.98")),
        PrintedValue("Cash", "USD", Decimal("0.00")),
        PrintedValue("Total", "USD", Decimal("654.98")),
    )


def test_a_release_is_salary_paid_in_shares_at_the_printed_price(result: Extraction) -> None:
    salary, purchase = result.transactions[:2]
    assert (salary.kind, salary.category, salary.amount) == (
        Kind.INCOME,
        "Stipendio",
        Decimal("631.50"),
    )
    assert isinstance(purchase, SecurityTrade)
    assert (purchase.kind, purchase.category, purchase.amount) == (
        Kind.SECURITY_TRADE,
        "Investimenti",
        Decimal("-631.50"),
    )
    assert (purchase.ticker, purchase.quantity, purchase.price, purchase.cost) == (
        "GOOG",
        Decimal("4.210"),
        Decimal(150),
        Decimal("631.50"),
    )
    releases = [t for t in result.transactions if t.description.startswith("Release")]
    assert sum(t.amount for t in releases) == 0  # no cash moves
    assert sum(t.amount for t in releases if t.kind is Kind.INCOME) == Decimal("2102.625")


def test_a_withdrawal_moves_shares_out_in_kind_without_a_price(result: Extraction) -> None:
    moved = [t for t in result.transactions if t.description.startswith("Share withdrawal")]
    assert [(t.date, t.kind, t.amount, t.category) for t in moved] == [
        (date(2026, 7, 29), Kind.TRANSFER, Decimal(0), "Giroconto"),
        (date(2026, 8, 27), Kind.TRANSFER, Decimal(0), "Giroconto"),
    ]
    first = moved[0]
    assert isinstance(first, SecurityTrade)
    assert (first.quantity, first.price, first.description) == (
        Decimal(-6),
        None,
        "Share withdrawal: 6.000 GOOG",
    )


def test_the_cash_rows(result: Extraction) -> None:
    cash = [t for t in result.transactions if not t.description.startswith(("Release", "Share"))]
    assert [(t.date, t.kind, t.category, t.description, t.amount) for t in cash] == [
        (date(2026, 9, 14), Kind.DIVIDEND, "Dividendi", "Dividend", Decimal("0.20")),
        (date(2026, 9, 14), Kind.TAX, "Ritenute", "Withholding tax", Decimal("-0.03")),
        (date(2026, 9, 17), Kind.TRANSFER, "Giroconto", "Proceeds disbursement", Decimal("-0.27")),
        (
            date(2026, 9, 18),
            Kind.TRANSFER,
            "Giroconto",
            "Proceeds disbursement cancelled",
            Decimal("0.27"),
        ),
        (date(2026, 9, 21), Kind.TRANSFER, "Giroconto", "Proceeds disbursement", Decimal("-0.27")),
    ]


def test_shares_withdrawn_on_the_last_day_can_still_be_in_the_closing_figure() -> None:
    late = read(edited(LAST_ROW, f"{LAST_ROW}9/30/26 Share Withdrawal (3.000)\n"))
    check_balances(late)
    assert late.holdings is not None
    assert (late.holdings[0].quantity, late.holdings[0].value) == (
        Decimal("1.135"),
        Decimal("179.78"),  # at the closing price, not the printed value with the 3 in it
    )
    assert late.values[0] == PrintedValue("Stock", "USD", Decimal("654.98"))


def test_shares_that_do_not_add_up_are_refused() -> None:
    with pytest.raises(ExtractionError, match=r"closes with 4\.136, but its rows leave 4\.135"):
        read(edited("Number of Shares 0.520 4.135", "Number of Shares 0.520 4.136"))
    # Only a withdrawal of the last day can be in transit.
    earlier = edited(LAST_ROW, f"{LAST_ROW}9/29/26 Share Withdrawal (3.000)\n")
    with pytest.raises(ExtractionError, match=r"rows leave 1\.135"):
        read(earlier)


def test_an_account_opened_in_the_period_prints_only_its_closing_figures() -> None:
    opened = edited("Number of Shares 0.520 4.135", "Number of Shares 3.615")
    opened = opened.replace("Share Value $72.80 $654.98", "Share Value $572.62")
    first = read(opened)
    assert first.holdings is not None
    assert (first.holdings[0].quantity, first.holdings[0].value) == (
        Decimal("3.615"),
        Decimal("572.62"),
    )


def test_a_split_is_left_to_the_prices() -> None:
    split = read(edited(LAST_ROW, f"{LAST_ROW}9/26/26 Stock Split GOOG, 20 for 1 Stock Split\n"))
    assert split.transactions == read(TEXT).transactions


def test_recognises_only_a_stock_plan_statement() -> None:
    assert morganstanley.recognises(TEXT)
    assert not morganstanley.recognises(TEXT.replace("SHARE PURCHASE AND HOLDINGS", ""))
    assert not morganstanley.recognises("Swisscard AECS GmbH\nFattura\n")


@pytest.mark.parametrize(
    ("old", "new", "error", "message"),
    [
        (LAST_ROW, f"{LAST_ROW}9/26/26 Sale 1.000 150.0000\n", UnsupportedStatementError, "Sale"),
        (LAST_ROW, f"{LAST_ROW}10/1/26 Release 1.000 150.0000\n", ExtractionError, "outside"),
        (LAST_ROW, f"{LAST_ROW}13/1/26 Release 1.000 150.0000\n", ExtractionError, "13/1/26"),
        ("ALPHABET INC CL C", "ACME CORP", UnsupportedStatementError, "ACME CORP"),
        ("Issuer Description: ", "Issuer: ", ExtractionError, "no issuer"),
        ("(as of 7/1/26) (as of 9/30/26)", "", ExtractionError, "no period"),
        ("SHARE PURCHASE AND HOLDINGS", "", ExtractionError, "no .* section"),
        (
            "Page 4 of 4\nAccount Number: MSX00000001",
            "Page 4 of 4\nAccount Number: MSX2",
            ExtractionError,
            "one account number",
        ),
        ("Cash Value $0.10 $0.00", "Cash Value $0.10 $0.00 $0.00", ExtractionError, "Cash Value"),
        ("Cash Value $0.10 $0.00\n", "", ExtractionError, "found 0 lines 'Cash Value'"),
        ("Cash Value $0.10 $0.00", "Cash Value $0,10 $0.00", ExtractionError, "0,10"),
    ],
)
def test_what_cannot_be_read_is_refused(
    old: str, new: str, error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        read(edited(old, new))
