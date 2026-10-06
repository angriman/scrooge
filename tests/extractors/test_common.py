from datetime import date
from decimal import Decimal

import pytest

from finanze.errors import ExtractionError
from finanze.extractors import common


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


def test_single_month() -> None:
    assert common.single_month([date(2026, 9, 3), date(2026, 9, 30)], "x") == date(2026, 9, 1)
    with pytest.raises(ExtractionError, match="2026-09, 2026-10"):
        common.single_month([date(2026, 9, 3), date(2026, 10, 1)], "x")
    with pytest.raises(ExtractionError, match="no rows"):
        common.single_month([], "x")


def test_read_table_requires_columns_and_fills_short_rows() -> None:
    rows = common.read_table("\ufeffA;B\n1;2\n3\n", ";", {"A", "B"}, "x")
    assert rows == [{"A": "1", "B": "2"}, {"A": "3", "B": ""}]
    with pytest.raises(ExtractionError, match=r"missing column\(s\) \['C'\]"):
        common.read_table("A;B\n1;2\n", ";", {"A", "C"}, "x")
    with pytest.raises(ExtractionError, match="missing"):
        common.read_table("", ";", {"A"}, "x")
