from datetime import date
from decimal import Decimal

import pytest

from finanze.models import CashBalance, ControlTotal, Extraction, Kind, Transaction
from finanze.validation import (
    BalanceMismatchError,
    ControlTotalMismatchError,
    MissingBalanceError,
    ReconciliationError,
    check_balances,
)

SEPTEMBER = date(2026, 9, 1)


def _txn(amount: str, account: str = "IBKR USD", currency: str = "USD") -> Transaction:
    return Transaction(
        date=date(2026, 9, 1),
        account=account,
        amount=Decimal(amount),
        currency=currency,
        description="t",
        kind=Kind.INCOME,
        source_file="f.csv",
        source_ref="r",
    )


def _balance(opening: str, closing: str, account: str = "IBKR USD") -> CashBalance:
    return CashBalance(account, "USD", Decimal(opening), Decimal(closing))


def test_balances_that_reconcile_pass() -> None:
    extraction = Extraction(
        month=SEPTEMBER,
        transactions=(_txn("10.50"), _txn("-3.25")),
        balances=(_balance("100", "107.25"),),
    )
    check_balances(extraction)


def test_account_without_transactions_must_still_be_flat() -> None:
    check_balances(Extraction(month=SEPTEMBER, transactions=(), balances=(_balance("5", "5"),)))
    with pytest.raises(BalanceMismatchError):
        check_balances(Extraction(month=SEPTEMBER, transactions=(), balances=(_balance("5", "6"),)))


def test_mismatch_reports_account_and_difference() -> None:
    extraction = Extraction(
        month=SEPTEMBER, transactions=(_txn("10"),), balances=(_balance("100", "111"),)
    )
    with pytest.raises(BalanceMismatchError) as exc:
        check_balances(extraction)
    err = exc.value
    assert err.account == "IBKR USD"
    assert err.expected == Decimal(111)
    assert err.actual == Decimal(110)
    assert "IBKR USD" in str(err)
    assert isinstance(err, ReconciliationError)


def test_tolerance_is_exact_not_rounded() -> None:
    extraction = Extraction(
        month=SEPTEMBER, transactions=(_txn("0.01"),), balances=(_balance("0", "0.02"),)
    )
    with pytest.raises(BalanceMismatchError):
        check_balances(extraction)


def test_transactions_without_a_balance_block() -> None:
    extraction = Extraction(
        month=SEPTEMBER,
        transactions=(_txn("1", account="IBKR EUR", currency="EUR"),),
        balances=(_balance("0", "0"),),
    )
    with pytest.raises(MissingBalanceError, match="IBKR EUR"):
        check_balances(extraction)


def test_each_account_is_checked_independently() -> None:
    extraction = Extraction(
        month=SEPTEMBER,
        transactions=(_txn("5"), _txn("7", account="IBKR EUR", currency="EUR")),
        balances=(
            _balance("0", "5"),
            CashBalance("IBKR EUR", "EUR", Decimal(0), Decimal(8)),
        ),
    )
    with pytest.raises(BalanceMismatchError) as exc:
        check_balances(extraction)
    assert exc.value.account == "IBKR EUR"


def test_control_total_must_match_the_sum_of_transactions() -> None:
    control = ControlTotal("IBKR USD", "USD", Decimal("7.25"))
    both = (_txn("10.50"), _txn("-3.25"))
    check_balances(Extraction(month=SEPTEMBER, transactions=both, controls=(control,)))
    bad = Extraction(month=SEPTEMBER, transactions=(_txn("10.50"),), controls=(control,))
    with pytest.raises(ControlTotalMismatchError, match=r"off by 3\.25") as exc:
        check_balances(bad)
    assert (exc.value.expected, exc.value.actual) == (Decimal("7.25"), Decimal("10.50"))
    assert isinstance(exc.value, ReconciliationError)


def test_unreconciled_accounts_are_allowed_without_a_balance() -> None:
    extraction = Extraction(month=SEPTEMBER, transactions=(_txn("1"),), unreconciled=("IBKR USD",))
    check_balances(extraction)
    with pytest.raises(MissingBalanceError):
        check_balances(Extraction(month=SEPTEMBER, transactions=(_txn("1"),)))
