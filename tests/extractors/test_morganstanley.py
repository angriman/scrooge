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


SPLIT_ROW = "{} Stock Split GOOG, 20 for 1 Stock Split\n"
RELEASE_BEFORE = "8/25/26 Release 4.200 160.5000\n"


def with_closing_shares(text: str, shares: str) -> str:
    return text.replace("Number of Shares 0.520 4.135", f"Number of Shares 0.520 {shares}")


def test_a_split_multiplies_the_shares_held_and_is_no_transaction_of_its_own() -> None:
    # After every other row: the 4.135 held are 82.700 at the end.
    late = read(
        with_closing_shares(edited(LAST_ROW, LAST_ROW + SPLIT_ROW.format("9/26/26")), "82.700")
    )
    assert late.transactions == read(TEXT).transactions
    assert late.holdings is not None
    assert late.holdings[0].quantity == Decimal("82.700")
    assert late.values == read(TEXT).values
    check_balances(late)


def test_shares_booked_before_a_split_count_at_the_old_ratio_and_after_it_at_the_new() -> None:
    # 0.520 + 4.210 + 2.105 - 6 = 0.835 before the split, 16.7 after it, then +4.2 - 4 + 3.1.
    middle = read(
        with_closing_shares(
            edited(RELEASE_BEFORE, SPLIT_ROW.format("8/1/26") + RELEASE_BEFORE), "20.000"
        )
    )
    assert middle.holdings is not None
    assert middle.holdings[0].quantity == Decimal("20.000")
    assert [(t.date, t.amount, t.description) for t in middle.transactions] == [
        (t.date, t.amount, t.description) for t in read(TEXT).transactions
    ]
    # The same closing figure with the split after the release is another arithmetic.
    with pytest.raises(ExtractionError, match=r"closes with 20\.000, but its rows leave 99\.8"):
        read(
            with_closing_shares(
                edited(RELEASE_BEFORE, RELEASE_BEFORE + SPLIT_ROW.format("8/26/26")), "20.000"
            )
        )


def test_a_split_of_shares_held_from_the_opening() -> None:
    bare = "\n".join(
        line
        for line in TEXT.splitlines()
        if not line[:1].isdigit() or not ("Release" in line or "Share Withdrawal" in line)
    )
    bare = bare.replace("Number of Shares 0.520 4.135", "Number of Shares 3.000 60.000")
    bare = bare.replace("9/14/26 Dividend", SPLIT_ROW.format("9/2/26") + "9/14/26 Dividend")
    split = read(bare)
    assert split.holdings is not None
    assert split.holdings[0].quantity == Decimal("60.000")
    assert not any(isinstance(t, SecurityTrade) for t in split.transactions)


def test_a_reverse_split_divides_the_shares_held() -> None:
    reverse = edited(LAST_ROW, LAST_ROW + "9/26/26 Stock Split GOOG, 1 for 4 Stock Split\n")
    assert read(with_closing_shares(reverse, "1.03375")).holdings is not None


def test_a_split_that_does_not_lead_to_the_closing_shares_is_refused() -> None:
    row = SPLIT_ROW.format("9/26/26")
    with pytest.raises(ExtractionError, match=r"closes with 4\.135, but its rows leave 82\.7"):
        read(edited(LAST_ROW, LAST_ROW + row))


@pytest.mark.parametrize("row", ["Stock Split GOOG", "Stock Split GOOG, 0 for 1 Stock Split"])
def test_a_split_without_a_ratio_is_refused(row: str) -> None:
    with pytest.raises(ExtractionError, match="Stock Split"):
        read(edited(LAST_ROW, f"{LAST_ROW}9/26/26 {row}\n"))


def test_shares_withdrawn_on_the_last_day_after_a_split_are_in_the_new_count() -> None:
    row = SPLIT_ROW.format("9/26/26") + "9/30/26 Share Withdrawal (60.000)\n"
    late = read(with_closing_shares(edited(LAST_ROW, LAST_ROW + row), "82.700"))
    assert late.holdings is not None
    assert late.holdings[0].quantity == Decimal("22.700")


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
