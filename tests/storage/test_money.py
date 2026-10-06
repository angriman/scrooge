from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finanze.storage import money

AMOUNTS = st.decimals(min_value=-10_000_000, max_value=10_000_000, places=6)


@pytest.mark.parametrize(
    ("amount", "micro"),
    [("12.5", 12_500_000), ("-0.000001", -1), ("0", 0), ("-19.55044", -19_550_440)],
)
def test_amounts_are_stored_in_millionths(amount: str, micro: int) -> None:
    assert money.to_micro(Decimal(amount)) == micro
    assert money.from_micro(micro) == Decimal(amount)


def test_an_amount_with_more_than_six_decimals_is_refused_not_rounded() -> None:
    with pytest.raises(money.PrecisionError, match="6 decimals"):
        money.to_micro(Decimal("0.0000001"))


@pytest.mark.parametrize("convert", [money.to_micro, money.to_text])
def test_floats_are_refused(convert: Any) -> None:
    with pytest.raises(TypeError, match="Decimal"):
        convert(12.5)


def test_amounts_read_back_print_plainly() -> None:
    assert str(money.from_micro(1_000_000_000_000)) == "1000000"
    assert str(money.from_micro(0)) == "0"
    assert str(money.from_micro(-1)) == "-0.000001"


def test_decimals_are_stored_as_plain_digits() -> None:
    assert money.to_text(Decimal("1E-5")) == "0.00001"
    assert money.from_text("0.00001") == Decimal("0.00001")


@given(AMOUNTS)
def test_amounts_round_trip_exactly(amount: Decimal) -> None:
    assert money.from_micro(money.to_micro(amount)) == amount


@given(st.lists(AMOUNTS, max_size=20))
def test_a_sum_of_stored_amounts_is_the_stored_sum(amounts: list[Decimal]) -> None:
    assert sum(money.to_micro(a) for a in amounts) == money.to_micro(sum(amounts, Decimal(0)))
