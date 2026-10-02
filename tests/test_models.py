from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from finanze.models import Kind, Transaction


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


def test_transaction_rejects_float_amount() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        _make(12.5)
