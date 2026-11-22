from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from scrooge.models import Extraction, Kind, Transaction


def _make(amount: Any) -> Transaction:
    return Transaction(
        date=date(2026, 9, 1),
        account="Migros",
        amount=amount,
        currency="CHF",
        description="Test",
        kind=Kind.EXPENSE,
        source_file="fixture.csv",
        source_ref="row-1",
    )


def test_transaction_keeps_decimal_amount() -> None:
    txn = _make(Decimal("-12.50"))
    assert txn.amount == Decimal("-12.50")
    assert txn.confidence == Decimal(1)


def test_booking_date_defaults_to_the_date_and_can_differ() -> None:
    assert _make(Decimal(1)).booking_date == date(2026, 9, 1)
    later = replace(_make(Decimal(1)), booking_date=date(2026, 10, 4))
    assert (later.date, later.booking_date) == (date(2026, 9, 1), date(2026, 10, 4))


def test_transaction_rejects_float_amount() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        _make(12.5)


def test_an_extraction_period_is_exact_or_the_whole_month() -> None:
    month = date(2026, 9, 1)
    assert Extraction(month, ()).period == (date(2026, 9, 1), date(2026, 9, 30))
    part = Extraction(month, (), start=date(2026, 9, 5), end=date(2026, 10, 3))
    assert part.period == (date(2026, 9, 5), date(2026, 10, 3))
