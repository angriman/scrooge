"""Reconciliation: a mismatch blocks the write. Nothing here may be caught and ignored."""

from __future__ import annotations

from collections import defaultdict
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


class MissingBalanceError(ReconciliationError):
    """Transactions exist for an account whose balance the statement never printed."""


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
