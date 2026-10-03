from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finanze.aggregate import BudgetRow, UncategorizedError, aggregate
from finanze.models import Kind, Transaction

MONTH = date(2026, 9, 1)


def _txn(
    category: str, amount: str, account: str = "Migros", kind: Kind = Kind.EXPENSE
) -> Transaction:
    return Transaction(
        date=MONTH,
        account=account,
        amount=Decimal(amount),
        currency="CHF",
        description="d",
        kind=kind,
        source_file="f",
        source_ref="r",
        category=category,
    )


def test_sign_of_each_sum_decides_the_side() -> None:
    budget = aggregate(
        MONTH,
        [
            _txn("Spesa", "-20.50"),
            _txn("Spesa", "-9.50"),
            _txn("Spesa", "5"),  # a refund nets against the category
            _txn("Stipendio", "7200"),
            _txn("Trading", "-100", "IBKR USD"),
            _txn("Trading", "250", "Tastytrade"),
        ],
    )
    assert budget.month == MONTH
    assert budget.uscite == (
        BudgetRow("Spesa", "Migros", Decimal("25.00")),
        BudgetRow("Trading", "IBKR USD", Decimal("100.00")),
    )
    assert budget.entrate == (
        BudgetRow("Stipendio", "Migros", Decimal("7200.00")),
        BudgetRow("Trading", "Tastytrade", Decimal("250.00")),
    )


def test_rows_follow_taxonomy_order_then_account() -> None:
    rows = [_txn("Commissioni", "-1", "IBKR USD"), _txn("Commissioni", "-1", "IBKR CHF")]
    budget = aggregate(MONTH, [*rows, _txn("Affitto", "-1850"), _txn("Topup IBKR", "-5")])
    assert [(r.category, r.account) for r in budget.uscite] == [
        ("Affitto", "Migros"),
        ("Commissioni", "IBKR CHF"),
        ("Commissioni", "IBKR USD"),
        ("Topup IBKR", "Migros"),
    ]


def test_internal_rows_and_zero_sums_are_left_out() -> None:
    budget = aggregate(
        MONTH,
        [
            _txn("", "-1288.18", kind=Kind.INTERNAL),
            _txn("Prelievi", "-100"),
            _txn("Prelievi", "100"),
            _txn("Interessi", "0.004", "Revolut EUR"),  # rounds to nothing
        ],
    )
    assert (budget.uscite, budget.entrate) == ((), ())


def test_fractions_of_a_cent_are_rounded_after_summing() -> None:
    budget = aggregate(MONTH, [_txn("Interessi", "0.0812"), _txn("Interessi", "0.0574")])
    assert budget.entrate == (BudgetRow("Interessi", "Migros", Decimal("0.14")),)


def test_uncategorized_rows_block() -> None:
    with pytest.raises(UncategorizedError, match="no category"):
        aggregate(MONTH, [_txn("", "-5")])


_amounts = st.decimals(min_value=-10_000, max_value=10_000, places=2)
_transactions = st.lists(
    st.builds(
        _txn,
        st.sampled_from(["Spesa", "Trading", "Commissioni", "Stipendio"]),
        _amounts.map(str),
        st.sampled_from(["Migros", "IBKR USD", "Revolut EUR"]),
    ),
    max_size=40,
)


@given(_transactions)
def test_sum_of_rows_equals_sum_of_transactions(transactions: list[Transaction]) -> None:
    budget = aggregate(MONTH, transactions)
    rows = sum(r.amount for r in budget.entrate) - sum(r.amount for r in budget.uscite)
    assert rows == sum(t.amount for t in transactions)
    assert all(r.amount > 0 for r in budget.uscite + budget.entrate)


@given(_transactions)
def test_order_of_transactions_does_not_matter(transactions: list[Transaction]) -> None:
    assert aggregate(MONTH, transactions) == aggregate(MONTH, list(reversed(transactions)))
