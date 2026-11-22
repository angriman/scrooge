"""The filters the templates print numbers with."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scrooge.web import app as webapp

SCALE = {"": Decimal(1), "k": Decimal(1_000), "M": Decimal(1_000_000)}


@pytest.mark.parametrize(
    ("amount", "shown"),
    [
        ("0", "0"),
        ("0.49", "0"),
        ("-0.4", "0"),  # nothing left to be below zero
        ("850", "850"),
        ("850.5", "851"),  # half up
        ("999.4", "999"),
        ("999.6", "1.0k"),  # rounds to a thousand: written in thousands
        ("1000", "1.0k"),
        ("12345", "12.3k"),
        ("12350", "12.4k"),
        ("123449.99", "123.4k"),
        ("999949.99", "999.9k"),
        ("999950", "1.0M"),  # not 1000.0k
        ("1000000", "1.0M"),
        ("1234567.89", "1.2M"),
        ("1250000", "1.3M"),
        ("1234567890", "1,234.6M"),
        ("-850", "-850"),
        ("-1450", "-1.5k"),
        ("-999950", "-1.0M"),
    ],
)
def test_short_is_an_amount_at_a_glance(amount: str, shown: str) -> None:
    assert webapp._short(Decimal(amount)) == shown


@given(st.decimals(min_value=-(10**12), max_value=10**12, places=2))
def test_short_is_within_half_its_last_digit_of_the_amount(amount: Decimal) -> None:
    shown = webapp._short(amount)
    letter = shown[-1] if shown[-1] in "kM" else ""
    figure = Decimal(shown.removesuffix(letter).replace(",", ""))
    last_digit = SCALE[letter] if letter == "" else SCALE[letter] / 10
    assert abs(figure * SCALE[letter] - amount) <= last_digit / 2
    # Each unit only while the figure needs it: under a thousand whole, thousands under a
    # thousand of them, millions from one.
    assert abs(figure) < 1000 or letter == "M"
    assert abs(figure) >= 1 or letter == ""
