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


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityTrade(Transaction):
    """A buy/sell of an instrument that also lands in an asset tab (D11).

    `amount` is the signed cash flow excluding commissions (buys negative). `quantity` is signed
    (sells negative). `cost` is the asset tabs' "total paid": `-amount`, so positive for buys.
    It equals price x quantity for shares, but not for bonds (accrued interest is included).
    `asset_tab` is resolved later from `instruments.yaml` (D12), so extractors leave it empty.
    """

    ticker: str
    quantity: Decimal
    price: Decimal
    cost: Decimal
    asset_tab: str = ""
    maturity: date | None = None


@dataclass(frozen=True, slots=True)
class CashBalance:
    """Opening and closing cash of one account, as printed on the statement."""

    account: str
    currency: str
    opening: Decimal
    closing: Decimal


@dataclass(frozen=True, slots=True)
class Extraction:
    """What an extractor returns: transactions plus the balances they must reconcile to."""

    month: date  # first day of the calendar month the statement covers
    transactions: tuple[Transaction, ...]
    balances: tuple[CashBalance, ...]
