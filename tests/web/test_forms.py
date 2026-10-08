from datetime import date
from decimal import Decimal

import pytest

from finanze.models import Kind
from finanze.query import Filter
from finanze.web import forms

NOT_A_DAY = "is not a day, a month or a year like 2025-05-17 or 2025-05."


def test_nothing_asked_is_the_empty_filter() -> None:
    assert forms.transactions_filter({}) == forms.Parsed(Filter(), ())
    blank = dict.fromkeys(["from", "to", "owner", "account", "category", "kind", "min"], " ")
    assert forms.transactions_filter(blank) == forms.Parsed(Filter(), ())


def test_every_field_is_read() -> None:
    params = {
        "from": "2025-05",
        "to": "2025-05",
        "booking": "1",
        "owner": "Lukas",
        "account": "Migros",
        "category": "Ristoranti e bar",
        "kind": "expense",
        "min": "10",
        "max": "1'250.50",
        "text": " konsum ",
    }
    assert forms.transactions_filter(params) == forms.Parsed(
        Filter(
            first=date(2025, 5, 1),
            last=date(2025, 5, 31),
            booking=True,
            owner="Lukas",
            account="Migros",
            category="Ristoranti e bar",
            kind=Kind.EXPENSE,
            at_least=Decimal(10),
            at_most=Decimal("1250.50"),
            text="konsum",
        ),
        (),
    )


def test_a_year_or_a_day_works_as_either_end_and_a_size_has_no_sign() -> None:
    parsed = forms.transactions_filter({"from": "2025", "to": "2026-02-03", "min": "-1,000"})
    assert parsed.wanted == Filter(date(2025, 1, 1), date(2026, 2, 3), at_least=Decimal(1000))


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"from": "May"}, f"'May' {NOT_A_DAY}"),
        ({"to": "2025-13"}, f"'2025-13' {NOT_A_DAY}"),
        ({"kind": "gift"}, "'gift' is not a kind of transaction."),
        ({"min": "a lot"}, "'a lot' is not an amount like 1234.56."),
        ({"max": "NaN"}, "'NaN' is not an amount like 1234.56."),
        ({"max": "Infinity"}, "'Infinity' is not an amount like 1234.56."),
    ],
)
def test_a_field_that_cannot_be_read_is_reported_and_left_unset(
    params: dict[str, str], error: str
) -> None:
    assert forms.transactions_filter(params) == forms.Parsed(Filter(), (error,))
