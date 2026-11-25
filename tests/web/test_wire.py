"""The one way the pages and the JSON take a currency that has no stored rate: the figure
is left out, the reason is said, and nothing else is lost. And a person as the JSON sends them."""

from datetime import date
from decimal import Decimal

import pytest

from scrooge import networth, query
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


def test_a_side_keeps_its_italian_name_on_the_wire() -> None:
    assert [wire.side(name) for name in ("expenses", "income", "transfer")] == [
        "uscite",
        "entrate",
        "transfer",
    ]
    assert [wire.side_sent(name) for name in ("uscite", "entrate", "transfer")] == [
        "expenses",
        "income",
        "transfer",
    ]


def test_parts_of_a_whole_are_sent_in_whole_percents_that_add_up() -> None:
    # 48.5% and 51.5% each rounded would be 49% and 52%.
    assert wire.whole_percents([Decimal("0.485"), Decimal("0.515")]) == ["49", "51"]
    # A part below zero: the others add up to more than 100, all of them to 100.
    assert wire.whole_percents([Decimal("-0.070575"), Decimal("1.070575")]) == ["-7", "107"]
    assert wire.whole_percents([None, None]) == [None, None]
