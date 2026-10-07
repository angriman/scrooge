from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finanze.storage import money

AMOUNTS = st.decimals(min_value=-10_000_000, max_value=10_000_000, places=9)


@pytest.mark.parametrize(
    ("amount", "nano"),
    [
        ("12.5", 12_500_000_000),
        ("-0.000000001", -1),
        ("0", 0),
        ("-19.55044", -19_550_440_000),
        ("-0.36382575", -363_825_750),
    ],
)
def test_amounts_are_stored_in_billionths(amount: str, nano: int) -> None:
    assert money.to_nano(Decimal(amount)) == nano
    assert money.from_nano(nano) == Decimal(amount)


def test_an_amount_with_more_than_nine_decimals_is_refused_not_rounded() -> None:
    with pytest.raises(money.PrecisionError, match="9 decimals"):
        money.to_nano(Decimal("0.0000000001"))


@pytest.mark.parametrize("convert", [money.to_nano, money.to_text])
def test_floats_are_refused(convert: Any) -> None:
    with pytest.raises(TypeError, match="Decimal"):
        convert(12.5)


def test_amounts_read_back_print_plainly() -> None:
    assert str(money.from_nano(1_000_000_000_000_000)) == "1000000"
    assert str(money.from_nano(0)) == "0"
    assert money.to_text(money.from_nano(-1)) == "-0.000000001"


def test_decimals_are_stored_as_plain_digits() -> None:
    assert money.to_text(Decimal("1E-5")) == "0.00001"
    assert money.from_text("0.00001") == Decimal("0.00001")


@given(AMOUNTS)
def test_amounts_round_trip_exactly(amount: Decimal) -> None:
    assert money.from_nano(money.to_nano(amount)) == amount


@given(st.lists(AMOUNTS, max_size=20))
def test_a_sum_of_stored_amounts_is_the_stored_sum(amounts: list[Decimal]) -> None:
    assert sum(money.to_nano(a) for a in amounts) == money.to_nano(sum(amounts, Decimal(0)))
