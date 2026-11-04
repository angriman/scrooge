"""Normalized transaction: the contract between extractors and the database."""

from __future__ import annotations

import calendar
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
    # debit on Migros): needed to reconcile the statement, never a row of income or expenses.
    INTERNAL = "internal"


# Stands for "same as `date`" until `__post_init__` fills it in.
_SAME_DAY = date.min
# Inside `Transaction`, `date` names the field: later annotations need another name for the type.
_Date = date


@dataclass(frozen=True, slots=True)
class Transaction:
    """One normalized transaction. `amount` is signed, in the native currency.

    D19: `date` is the day it really happened (viewing, querying); `booking_date` is the day
    it counts on for its statement, always inside the statement period (reconciliation
    and booking). They differ only where an extractor says so, e.g. card purchases billed later.
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
    `price` is None for shares moved in kind where the statement prints no price.
    `asset_tab` is what the statement says the instrument is, when it says so; `instruments.yaml`
    overrides it and must supply it otherwise (D12).
    """

    ticker: str
    quantity: Decimal
    price: Decimal | None
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
class Holding:
    """What a statement says an account held of one instrument at the end of its period.

    `quantity` is face value for a bill, whose `price` is then a percentage of it. `value` is
    the statement's own figure, in the instrument's currency. With `unvested` the quantity
    includes shares awarded and not yet vested, which the transactions do not count yet.
    """

    account: str
    ticker: str
    currency: str
    quantity: Decimal
    price: Decimal
    value: Decimal
    asset_tab: str = ""
    unvested: bool = False


@dataclass(frozen=True, slots=True)
class PrintedValue:
    """One line of the statement's own valuation at the end of its period (IBKR: Stock, Bonds)."""

    label: str
    currency: str
    value: Decimal


@dataclass(frozen=True, slots=True)
class Rate:
    """What one unit of `currency` was worth in `base` at the end of the statement period."""

    currency: str
    base: str
    rate: Decimal


@dataclass(frozen=True, slots=True)
class PensionCertificate:
    """What a second-pillar certificate states: the retirement assets on one day.

    A certificate has no transactions and no period: `balance` is what the pension fund held
    for the person on `as_of`, a day of its own choosing (often the first of a month).
    `mandatory` is the part of `balance` under the legal minimum (BVG), never more than it.
    `opened` is the day and the balance the year behind it began with, on a certificate that
    prints them: what the certificate of that day stated, or would have.
    """

    source: str
    as_of: date
    currency: str
    balance: Decimal
    mandatory: Decimal
    insured_since: date  # nothing was held there before this day
    opened: tuple[date, Decimal] | None = None


@dataclass(frozen=True, slots=True)
class Extraction:
    """What an extractor returns: transactions plus the figures they must reconcile to.

    `unreconciled` names accounts for which the statement prints nothing to check against
    (Revolut Savings); callers must surface them to the user.
    """

    month: date  # first day of the calendar month of the first day the statement covers
    transactions: tuple[Transaction, ...]
    balances: tuple[CashBalance, ...] = ()
    controls: tuple[ControlTotal, ...] = ()
    unreconciled: tuple[str, ...] = ()
    # Last day covered, any day; None: the end of `month`.
    end: _Date | None = None
    # The institution's own number for the account, where one person may have several there.
    account_id: str = ""
    # What the statement says was held at the end of its period. None: it does not say, which
    # is not the same as saying that nothing was held.
    holdings: tuple[Holding, ...] | None = None
    values: tuple[PrintedValue, ...] = ()
    rates: tuple[Rate, ...] = ()
    # First day covered, any day; None: the first of `month`.
    start: _Date | None = None

    @property
    def period(self) -> tuple[_Date, _Date]:
        """First and last day the statement covers, exactly as it prints or its rows show."""
        last = calendar.monthrange(self.month.year, self.month.month)[1]
        return self.start or self.month, self.end or self.month.replace(day=last)
