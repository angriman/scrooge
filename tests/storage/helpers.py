"""Builders for month-close tests: statements and months made by hand, no files needed."""

import hashlib
import re
import sqlite3
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from finanze.models import CashBalance, Extraction, Kind, SecurityTrade, Transaction
from finanze.monthclose import Batch
from finanze.pipeline import Month, Statement

AUGUST = date(2026, 8, 1)
SEPTEMBER = date(2026, 9, 1)
OCTOBER = date(2026, 10, 1)
OWNER = "Lukas"
PARTNER = "Anna"
PEOPLE = {OWNER: [re.compile("LUKAS MEIER")], PARTNER: [re.compile("ANNA MEIER", re.IGNORECASE)]}


def txn(amount: str, **fields: Any) -> Transaction:
    defaults: dict[str, Any] = {
        "date": date(2026, 9, 10),
        "account": "Migros",
        "currency": "CHF",
        "description": "KONSUM",
        "kind": Kind.EXPENSE,
        "source_file": "migros.csv",
        "source_ref": "1",
        "category": "Spesa",
    }
    return Transaction(amount=Decimal(amount), **(defaults | fields))


def trade(amount: str, ticker: str, quantity: str, **fields: Any) -> SecurityTrade:
    defaults: dict[str, Any] = {
        "date": date(2026, 9, 15),
        "account": "IBKR USD",
        "currency": "USD",
        "description": f"{quantity} {ticker}",
        "kind": Kind.SECURITY_TRADE,
        "source_file": "ibkr.csv",
        "source_ref": "Trades:1",
        "category": "Investimenti",
        "price": Decimal("101.25"),
        "asset_tab": "ETFs",
    }
    return SecurityTrade(
        amount=Decimal(amount),
        ticker=ticker,
        quantity=Decimal(quantity),
        cost=-Decimal(amount),
        **(defaults | fields),
    )


def statement(
    source: str,
    rows: Sequence[Transaction],
    balances: Sequence[CashBalance] = (),
    *,
    filename: str = "",
    month: date = SEPTEMBER,
    content: str = "",
) -> Statement:
    name = filename or (rows[0].source_file if rows else f"{source}.csv")
    extraction = Extraction(month=month, transactions=tuple(rows), balances=tuple(balances))
    digest = hashlib.sha256(f"{name}{month}{content}".encode()).hexdigest()
    return Statement(source, name, digest, extraction)


def bill(amount: str, quantity: str, day: date, **fields: Any) -> SecurityTrade:
    """A Tastytrade trade in the bill that matures in the fixture month (face 10,000)."""
    defaults: dict[str, Any] = {
        "account": "Tastytrade",
        "date": day,
        "source_file": f"tastytrade {day:%Y-%m}.csv",
        "source_ref": f"{day}",
        "price": Decimal(99),
        "asset_tab": "Bonds",
        "maturity": date(2026, 9, 16),
    }
    return trade(amount, "912797YY2", quantity, **(defaults | fields))


def tastytrade(*rows: SecurityTrade, month: date = SEPTEMBER) -> Batch:
    """A month with just a Tastytrade statement."""
    return batch(statement("tastytrade", rows, month=month), month=month)


def batch(*statements: Statement, owner: str = OWNER, month: date = SEPTEMBER) -> Batch:
    rows = tuple(row for s in statements for row in s.extraction.transactions)
    return Batch(owner, statements, Month(month, rows, (), ()), PEOPLE)


def migros(opening: str, *amounts: str, month: date = SEPTEMBER, **fields: Any) -> Statement:
    """A Migros statement whose closing balance follows from its rows."""
    day = month.replace(day=10)
    rows = [
        txn(amount, date=day, source_ref=str(n), **fields) for n, amount in enumerate(amounts, 1)
    ]
    closing = Decimal(opening) + sum((row.amount for row in rows), Decimal(0))
    balance = CashBalance("Migros", "CHF", Decimal(opening), closing)
    return statement("migros", rows, [balance], filename="migros.csv", month=month)


def dump(conn: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of every table: two databases with equal dumps hold the same data."""
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    return {
        name: [tuple(row) for row in conn.execute(f"SELECT * FROM {name} ORDER BY 1, 2")]  # noqa: S608  # table names come from sqlite_master
        for (name,) in tables.fetchall()
    }


def rows(conn: sqlite3.Connection, sql: str, *args: Any) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in conn.execute(sql, args).fetchall()]
