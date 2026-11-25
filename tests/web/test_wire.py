"""The one way the pages and the JSON take a currency that has no stored rate: the figure
is left out, the reason is said, and nothing else is lost. And a person as the JSON sends them."""

from datetime import date
from decimal import Decimal

import pytest

from scrooge import networth, portfolio, query
from scrooge.web import wire


def test_the_figure_is_returned_and_nothing_is_said() -> None:
    errors: list[str] = []
    assert wire.unless_rate_missing(errors, lambda: 42, None) == 42
    assert errors == []


def test_a_missing_rate_gives_the_fallback_and_one_sentence() -> None:
    errors = ["Earlier."]

    def worth() -> int:
        raise networth.MissingRateError("no rate is stored for JPY")

    assert wire.unless_rate_missing(errors, worth, None) is None
    assert errors == ["Earlier.", "No rate is stored for JPY."]


def test_any_other_error_is_not_swallowed() -> None:
    def broken() -> int:
        raise ValueError("a bug")

    with pytest.raises(ValueError, match="a bug"):
        wire.unless_rate_missing([], broken, None)


def test_a_person_is_sent_without_a_day_covered_unless_one_is_given() -> None:
    anna = query.Person("Anna", committed=0, first=None, last=None, staged=0, transactions=0)
    assert wire.person(anna)["covered"] is None
    assert wire.person(anna, date(2026, 9, 30))["covered"] == "2026-09-30"


def test_parts_of_a_whole_are_sent_in_whole_percents_that_add_up() -> None:
    # 48.5% and 51.5% each rounded would be 49% and 52%.
    assert wire.whole_percents([Decimal("0.485"), Decimal("0.515")]) == ["49", "51"]
    # A part below zero: the others add up to more than 100, all of them to 100.
    assert wire.whole_percents([Decimal("-0.070575"), Decimal("1.070575")]) == ["-7", "107"]
    assert wire.whole_percents([None, None]) == [None, None]


def test_parts_of_a_whole_are_sent_to_the_tenth_rounded_once_and_adding_up() -> None:
    # Sent to the hundredth, 48.449 would be 48.45 and the app's tenth 48.5: rounded twice.
    # And 48.5 with 51.6 is 100.1.
    whole = Decimal(100)
    assert wire.tenths([Decimal("48.449"), Decimal("51.551")], whole) == ["48.4", "51.6"]
    # What is owed is below zero: the others add up to more than 100, all of them to 100.
    assert wire.tenths([Decimal(-70), Decimal(1070)], Decimal(1000)) == ["-7.0", "107.0"]
    assert wire.tenths([Decimal(1), Decimal(2)], Decimal(3)) == ["33.3", "66.7"]
    assert wire.tenths([Decimal(5)], Decimal(0)) == [None]
    assert wire.tenths([Decimal(5), Decimal(-10)], Decimal(-5)) == [None, None]


def test_securities_worth_nothing_in_all_have_shares_of_zero() -> None:
    held = portfolio.Portfolio("CHF", None, (), ())
    sent = wire.holdings(held)
    assert sent["holdings"] == []
    assert sent["spread"] == {by: [] for by in portfolio.SPREADS}
    assert wire._of_securities([Decimal(5), Decimal(-5)], Decimal(0)) == ["0", "0"]
