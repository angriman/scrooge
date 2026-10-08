"""Reconciliation: a mismatch blocks the write. Nothing here may be caught and ignored."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from finanze.models import Extraction


class ReconciliationError(Exception):
    """Base class for every reconciliation failure."""


class BalanceMismatchError(ReconciliationError):
    """opening + sum(transactions) != closing for one account."""

    def __init__(self, account: str, currency: str, expected: Decimal, actual: Decimal) -> None:
        self.account = account
        self.currency = currency
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"{account} ({currency}): closing balance {expected} on the statement, "
            f"but opening + transactions = {actual} (off by {actual - expected})"
        )


class ControlTotalMismatchError(ReconciliationError):
    """sum(transactions) != the statement's net total for one account."""

    def __init__(self, account: str, currency: str, expected: Decimal, actual: Decimal) -> None:
        self.account = account
        self.currency = currency
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"{account} ({currency}): statement total {expected}, "
            f"but transactions sum to {actual} (off by {actual - expected})"
        )


class ChainMismatchError(ReconciliationError):
    """A statement balance does not follow from the previous one plus what was booked between.

    Either the statements of two periods disagree, or a period in between has no statement.
    """

    def __init__(
        self,
        account: str,
        currency: str,
        span: tuple[date, date],
        expected: Decimal,
        actual: Decimal,
    ) -> None:
        self.account = account
        self.currency = currency
        self.span = span
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"{account} ({currency}): balance {expected} on {span[1]}, but the balance of "
            f"{span[0]} + transactions since = {actual} (off by {actual - expected})"
        )


class MissingStatementError(ReconciliationError):
    """An account's balance changed over a period that no statement covers."""

    def __init__(
        self,
        account: str,
        currency: str,
        span: tuple[date, date],
        balances: tuple[Decimal, Decimal],
    ) -> None:
        self.account = account
        self.currency = currency
        self.span = span
        self.balances = balances
        super().__init__(
            f"{account} ({currency}): no statement covers the days after {span[0]} up to "
            f"{span[1]}, yet the balance went from {balances[0]} to {balances[1]}"
        )


class MissingLotsError(ReconciliationError):
    """A bill matured or was sold, but its purchases are not all stored: no cost to split."""

    def __init__(self, account: str, symbol: str, day: date) -> None:
        self.account = account
        self.symbol = symbol
        self.day = day
        super().__init__(
            f"{account}: {symbol} matured or was sold on {day}, but its purchases are not all "
            "stored (import the earlier statements): the interest cannot be split from the cost"
        )


class HoldingsMismatchError(ReconciliationError):
    """A statement prints another quantity of an instrument than the rows before it lead to."""

    def __init__(
        self, account: str, symbol: str, day: date, expected: Decimal, actual: Decimal
    ) -> None:
        self.account = account
        self.symbol = symbol
        self.day = day
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"{account}: the statement says {format(expected, 'f')} {symbol} were held on {day}, "
            f"but the holdings before it and what was booked since give {format(actual, 'f')} "
            "(a statement in between is missing, or a split is not stored)"
        )


class MissingBalanceError(ReconciliationError):
    """Transactions exist for an account whose balance the statement never printed."""


class CardBillMismatchError(ReconciliationError):
    """D3: the card invoice and the debit on the bank account disagree, or one is missing."""


def check_card_bill(debit: Decimal | None, invoice: Decimal | None) -> None:
    """D3: sum(purchases) - cashback must equal the bank's card debit of the same month.

    Both are signed as cash leaving the bank account (negative). A debit without an invoice
    would silently drop the month's card spending; an invoice without a debit would double it.
    """
    if debit == invoice:
        return
    if invoice is None:
        msg = f"the bank statement has a card debit of {debit}, but there is no card invoice"
    elif debit is None:
        msg = f"the card invoice totals {invoice}, but the bank statement has no card debit"
    else:
        msg = f"the card invoice totals {invoice}, but the bank debited {debit}"
    raise CardBillMismatchError(msg)


def check_balances(extraction: Extraction) -> None:
    """Check every account against its statement balance or control total, exactly."""
    flows: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for txn in extraction.transactions:
        flows[txn.account, txn.currency] += txn.amount

    known = {(b.account, b.currency) for b in extraction.balances}
    known |= {(c.account, c.currency) for c in extraction.controls}
    for account, currency in flows:
        if (account, currency) not in known and account not in extraction.unreconciled:
            msg = f"{account} ({currency}) has transactions but no balance on the statement"
            raise MissingBalanceError(msg)

    for balance in extraction.balances:
        actual = balance.opening + flows[balance.account, balance.currency]
        if actual != balance.closing:
            raise BalanceMismatchError(balance.account, balance.currency, balance.closing, actual)

    for control in extraction.controls:
        actual = flows[control.account, control.currency]
        if actual != control.total:
            raise ControlTotalMismatchError(
                control.account, control.currency, control.total, actual
            )
