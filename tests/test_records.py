from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finanze import pipeline, records
from finanze.models import Kind, SecurityTrade, Transaction
from tests.conftest import INSTRUMENTS

TXN = Transaction(
    date=date(2026, 9, 1),
    account="Migros",
    amount=Decimal("-1850.00"),
    currency="CHF",
    description="Affitto",
    kind=Kind.EXPENSE,
    source_file="Migros.csv",
    source_ref="3",
)


def test_record_is_flat_and_keeps_native_types() -> None:
    row = records.record(TXN)
    assert tuple(row) == records.FIELDS
    assert row["id"] == "Migros.csv:3"
    assert (row["date"], row["amount"], row["kind"]) == (TXN.date, TXN.amount, "expense")
    assert (row["ticker"], row["quantity"], row["maturity"]) == (None, None, None)


def test_security_trade_fills_the_trade_columns() -> None:
    trade = SecurityTrade(
        date=date(2026, 9, 2),
        account="IBKR USD",
        amount=Decimal("-1000.00"),
        currency="USD",
        description="BUY 10 WIDG",
        kind=Kind.SECURITY_TRADE,
        source_file="IBKR.csv",
        source_ref="Trades:1",
        ticker="WIDG",
        quantity=Decimal(10),
        price=Decimal(100),
        cost=Decimal("1000.00"),
        asset_tab="ETFs",
    )
    row = records.record(trade)
    assert tuple(row) == records.FIELDS
    assert (row["ticker"], row["quantity"], row["cost"]) == ("WIDG", Decimal(10), trade.cost)


def test_a_whole_month_has_one_distinct_id_per_transaction(folder: Path) -> None:
    extractions, _ = pipeline.load_folder(folder)
    month = pipeline.process(extractions, (), INSTRUMENTS).renamed({"Tastytrade": "Tasty"})
    rows = records.records(month.transactions)
    assert len({row["id"] for row in rows}) == len(rows) == len(month.transactions)
    assert rows == records.records(month.transactions)  # same input, same rows
    assert sum(row["account"] == "Tasty" for row in rows) == 22


def test_duplicate_ids_are_refused() -> None:
    with pytest.raises(records.DuplicateRecordError, match=r"'Migros\.csv:3'"):
        records.records([TXN, replace(TXN, amount=Decimal(1))])
