"""The one way the pages and the JSON take a currency that has no stored rate: the figure
is left out, the reason is said, and nothing else is lost."""

import pytest

from scrooge import networth
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
