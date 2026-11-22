import re
from datetime import date
from decimal import Decimal

import pytest

from scrooge.errors import ExtractionError
from scrooge.extractors import common
from scrooge.models import Kind, SecurityTrade, Transaction


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234.50", "1234.50"),
        ("1'234.50", "1234.50"),
        (" -12.3 ", "-12.3"),
        ("0", "0"),
        ("-1,234,567", "-1234567"),
        ("0.10125", "0.10125"),
    ],
)
def test_parse_decimal_drops_thousands_separators(text: str, expected: str) -> None:
    assert common.parse_decimal(text, "t") == Decimal(expected)


@pytest.mark.parametrize("text", ["", "--", "abc", "1.2.3", "7411,61", "12,5", "1,23,456", "1e3"])
def test_parse_decimal_rejects_garbage(text: str) -> None:
    with pytest.raises(ExtractionError, match="amount"):
        common.parse_decimal(text, "t")


@pytest.mark.parametrize(
    "text", ["2026-09-30", "2026-09-30 12:00:00", "2026-09-30T23:59:00+0200", "2026-09-30, 10:00"]
)
def test_parse_iso_date_ignores_the_time(text: str) -> None:
    assert common.parse_iso_date(text, "t") == date(2026, 9, 30)


def test_parse_swiss_date() -> None:
    assert common.parse_swiss_date("30.09.2026", "t") == date(2026, 9, 30)


@pytest.mark.parametrize("text", ["30.09.26", "31.02.2026", "2026-09-30", ""])
def test_bad_dates_raise(text: str) -> None:
    with pytest.raises(ExtractionError, match="date"):
        common.parse_swiss_date(text, "t")
    with pytest.raises(ExtractionError, match="date"):
        common.parse_iso_date("x" + text, "t")


def test_month_bounds() -> None:
    assert common.month_start(date(2024, 2, 17)) == date(2024, 2, 1)
    assert common.month_end(date(2024, 2, 17)) == date(2024, 2, 29)


def test_months_between() -> None:
    assert common.months_between(date(2025, 11, 17), date(2026, 2, 3)) == [
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]
    assert common.months_between(date(2026, 9, 30), date(2026, 9, 1)) == [date(2026, 9, 1)]


def test_day_span() -> None:
    assert common.day_span([date(2026, 9, 20), date(2026, 9, 3)], "x") == (
        date(2026, 9, 3),
        date(2026, 9, 20),
    )
    with pytest.raises(ExtractionError, match="no rows"):
        common.day_span([], "x")


def test_read_table_requires_columns_and_fills_short_rows() -> None:
    rows = common.read_table("\ufeffA;B\n1;2\n3\n", ";", {"A", "B"}, "x")
    assert rows == [{"A": "1", "B": "2"}, {"A": "3", "B": ""}]
    with pytest.raises(ExtractionError, match=r"missing column\(s\) \['C'\]"):
        common.read_table("A;B\n1;2\n", ";", {"A", "C"}, "x")
    with pytest.raises(ExtractionError, match="missing"):
        common.read_table("", ";", {"A"}, "x")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.00", "0.00"),
        ("950.40", "950.40"),
        ("12'345.65", "12345.65"),
        ("1'234'567.05", "1234567.05"),
    ],
)
def test_parse_swiss_amount_reads_printed_money(text: str, expected: str) -> None:
    assert common.parse_swiss_amount(text, "t") == Decimal(expected)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "12345.65",
        "12,345.65",
        "12'345",
        "12'345.6",
        "12'345.650",
        "-1.00",
        "1'23.00",
        "1234'567.00",
        " 1.00",
        "1.0O",
        "12\u2019345.65",
        "'123.00",
    ],
)
def test_parse_swiss_amount_rejects_anything_else(text: str) -> None:
    with pytest.raises(ExtractionError, match="amount"):
        common.parse_swiss_amount(text, "t")


def test_only_match_wants_exactly_one() -> None:
    pattern = re.compile(r"Total (\d+)")
    assert common.only_match(pattern, "a\nTotal 12\nb", "the total")[1] == "12"
    with pytest.raises(ExtractionError, match="the total: found 0 times, expected once"):
        common.only_match(pattern, "nothing", "the total")
    with pytest.raises(ExtractionError, match="the total: found 2 times, expected once"):
        common.only_match(pattern, "Total 1\nTotal 2", "the total")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Caff\u00c3\u00a8 Nero", "Caff\u00e8 Nero"),
        ("\u00c3\u0096BB", "\u00d6BB"),  # Ö, whose second byte is a control character
        ("\u00c3\u0084ss Bar", "\u00c4ss Bar"),
        ("Bistr\u00c3\u00b3 \u00e2\u0082\u00ac5", "Bistr\u00f3 \u20ac5"),  # three bytes: €
        ("\u00f0\u009f\u008d\u0095 Pizza", "\U0001f355 Pizza"),  # four bytes
        ("L\u00e4derach L\u00c3\u00a4derach", "L\u00e4derach L\u00e4derach"),  # one of each
        ("Caf\u00e9 \u00d6lberg", "Caf\u00e9 \u00d6lberg"),  # right already
        ("\u00e0\u0080\u0080", "\u00e0\u0080\u0080"),  # no UTF-8: an overlong zero
    ],
)
def test_undo_double_encoding(text: str, expected: str) -> None:
    assert common.undo_double_encoding(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$1,234.50", "1234.50"),
        ("-$88.75", "-88.75"),
        ("(0.13)", "-0.13"),
        ("$(0.13)", "-0.13"),
        ("12", "12"),
    ],
)
def test_us_amounts_read_a_minus_or_parentheses_as_negative(text: str, expected: str) -> None:
    assert common.parse_us_amount(text, "t") == Decimal(expected)


@pytest.mark.parametrize("text", ["1$", "$1,09", "+$1", "$"])
def test_a_us_amount_that_cannot_be_read_is_refused(text: str) -> None:
    with pytest.raises(ExtractionError, match="cannot read amount"):
        common.parse_us_amount(text, "t")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10/28/2026", date(2026, 10, 28)),
        ("9/30/26", date(2026, 9, 30)),
        (" 1/2/2025", date(2025, 1, 2)),
    ],
)
def test_us_dates_are_month_day_year(text: str, expected: date) -> None:
    assert common.parse_us_date(text, "t") == expected


@pytest.mark.parametrize("text", ["2026-09-14", "02/30/2026", "13/01/2026", "1/2/026", ""])
def test_a_us_date_that_cannot_be_read_is_refused(text: str) -> None:
    with pytest.raises(ExtractionError, match=r"cannot read date .* \(t\)"):
        common.parse_us_date(text, "t")


def test_a_share_vest_is_salary_in_shares_and_their_purchase_at_the_price() -> None:
    salary, purchase = common.share_vest(
        day=date(2026, 3, 15),
        account="Plan",
        currency="USD",
        source_file="plan.csv",
        source_ref="7",
        ticker="ACME",
        quantity=Decimal(10),
        price=Decimal("12.5"),
        description="Vest of 10 ACME @ 12.5",
        asset_tab="Stocks",
    )
    assert salary == Transaction(
        date=date(2026, 3, 15),
        account="Plan",
        amount=Decimal("125.0"),
        currency="USD",
        description="Vest of 10 ACME @ 12.5: salary in shares",
        kind=Kind.INCOME,
        source_file="plan.csv",
        source_ref="7:salary",
        category="Stipendio",
    )
    assert purchase == SecurityTrade(
        date=date(2026, 3, 15),
        account="Plan",
        amount=Decimal("-125.0"),
        currency="USD",
        description="Vest of 10 ACME @ 12.5",
        kind=Kind.SECURITY_TRADE,
        source_file="plan.csv",
        source_ref="7",
        category="Investimenti",
        ticker="ACME",
        quantity=Decimal(10),
        price=Decimal("12.5"),
        cost=Decimal("125.0"),
        asset_tab="Stocks",
    )
