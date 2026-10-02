"""Normalized transaction: the contract between extractors and sheet writers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class Kind(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"
    TRANSFER = "transfer"
    FX = "fx"
    TRADING = "trading"
    COMMISSION = "commission"
    INTEREST = "interest"
    DIVIDEND = "dividend"
    TAX = "tax"
    SECURITY_TRADE = "security_trade"


@dataclass(frozen=True, slots=True)
class Transaction:
    """One normalized transaction. `amount` is signed, in the native currency."""

    date: date
    account: str
    amount: Decimal
    currency: str
    description: str
    kind: Kind
    source_file: str
    source_ref: str
    counterparty: str = ""
    category: str = ""
    confidence: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        # Runtime guard for untyped inputs (e.g. pdfplumber): annotations can't stop a float.
        amount: object = self.amount
        if not isinstance(amount, Decimal):
            msg = f"amount must be Decimal, got {type(amount).__name__}"
            raise TypeError(msg)
