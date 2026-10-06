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
    # Moves money inside one sheet account (or is replaced by another source, like the Swisscard
    # debit on Migros): needed to reconcile the statement, never written to the sheet.
    INTERNAL = "internal"


# Stands for "same as `date`" until `__post_init__` fills it in.
_SAME_DAY = date.min
# Inside `Transaction`, `date` names the field: later annotations need another name for the type.
_Date = date


@dataclass(frozen=True, slots=True)
class Transaction:
    """One normalized transaction. `amount` is signed, in the native currency.

    D19: `date` is the day it really happened (viewing, querying); `booking_date` is the day
    it counts on for its statement, always inside the statement period (reconciliation, paste
    files). They differ only where an extractor says so, e.g. card purchases billed later.
    """

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
    booking_date: _Date = _SAME_DAY

    def __post_init__(self) -> None:
        if self.booking_date == _SAME_DAY:
            object.__setattr__(self, "booking_date", self.date)  # frozen: the only way to default
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
    `asset_tab` is what the statement says the instrument is, when it says so; `instruments.yaml`
    overrides it and must supply it otherwise (D12).
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
class ControlTotal:
    """Net cash flow of one account, for statements that print totals but no balances."""

    account: str
    currency: str
    total: Decimal


@dataclass(frozen=True, slots=True)
class Extraction:
    """What an extractor returns: transactions plus the figures they must reconcile to.

    `unreconciled` names accounts for which the statement prints nothing to check against
    (Revolut Savings); callers must surface them to the user.
    """

    month: date  # first day of the calendar month the statement covers
    transactions: tuple[Transaction, ...]
    balances: tuple[CashBalance, ...] = ()
    controls: tuple[ControlTotal, ...] = ()
    unreconciled: tuple[str, ...] = ()
