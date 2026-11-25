"""Interest charged (plan decision 52): a row of category Interest below nothing is what an
account cost, an expense under Banking, and every view reads it so."""

import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest

from scrooge import cashflow, categories, context, income, payouts, query, savings, spending
from scrooge.cashflow import Line
from scrooge.models import Kind
from scrooge.storage import db, store
from tests.storage.helpers import CLOSED_AT, OWNER, close, txn

SPAN = (date(2026, 3, 1), date(2026, 3, 31))
INTEREST = {"kind": Kind.INTEREST, "category": categories.INTEREST}
ROWS = [
    txn(
        "4000", date=date(2026, 3, 25), source_ref="1", kind=Kind.INCOME, category=categories.SALARY
    ),
    txn("-300", date=date(2026, 3, 5), source_ref="2"),
    txn("-12", date=date(2026, 3, 9), source_ref="3", category=categories.FEES),
    txn("25", date=date(2026, 3, 31), source_ref="4", **INTEREST),
    # On a debit balance: what the account cost.
    txn("-7", date=date(2026, 3, 31), source_ref="5", **INTEREST),
]


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    close(connection, OWNER, SPAN, SPAN[1], ("migros", ROWS))
    yield connection
    connection.close()


def test_the_predicate_is_interest_below_nothing() -> None:
    assert categories.charged(categories.INTEREST, -1)
    assert not categories.charged(categories.INTEREST, 0)
    assert not categories.charged(categories.INTEREST, 1)
    assert not categories.charged(categories.FEES, -1)
    assert not categories.charged(None, -1)


def test_the_cash_flow_counts_it_as_an_expense_of_banking(conn: sqlite3.Connection) -> None:
    march = cashflow.run(conn, first=SPAN[0], last=SPAN[1], owner=OWNER)
    assert march.lines == (
        Line("2026-03", "CHF", cashflow.INCOME, categories.SALARY, 1, Decimal(4000)),
        Line("2026-03", "CHF", cashflow.INCOME, categories.INTEREST, 1, Decimal(25)),
        Line("2026-03", "CHF", cashflow.EXPENSES, categories.GROUP_FOOD, 1, Decimal(-300)),
        Line("2026-03", "CHF", cashflow.EXPENSES, categories.GROUP_BANKING, 2, Decimal(-19)),
    )
    [charged] = [row for row in cashflow.counted(conn, OWNER) if row.amount_nano == -7 * 10**9]
    assert (charged.side, charged.top, charged.leaf) == (
        cashflow.EXPENSES,
        categories.GROUP_BANKING,
        categories.INTEREST_CHARGED,
    )


def test_spending_has_a_line_of_its_own_under_banking(conn: sqlite3.Connection) -> None:
    spent = spending.run(conn, first=SPAN[0], last=SPAN[1], owner=OWNER, currency="CHF")
    [banking] = [c for c in spent.categories if c.name == categories.GROUP_BANKING]
    assert [(leaf.name, leaf.count, leaf.spent) for leaf in banking.leaves] == [
        (categories.FEES, 1, Decimal("12.00")),
        (categories.INTEREST_CHARGED, 1, Decimal("7.00")),
    ]
    assert spent.spent == Decimal("319.00")
    # Charged on the account: its merchant is the account, as for fees.
    assert "Migros" in [merchant.name for merchant in spent.merchants]


def test_income_is_the_interest_paid_only(conn: sqlite3.Connection) -> None:
    earned = income.run(conn, first=SPAN[0], last=SPAN[1], owner=OWNER, currency="CHF")
    assert [(s.leaf, s.count, s.total) for s in earned.sources] == [
        (categories.SALARY, 1, Decimal("4000.00")),
        (categories.INTEREST, 1, Decimal("25.00")),
    ]


def test_what_was_saved_does_not_change(conn: sqlite3.Connection) -> None:
    totals = savings.run(conn, first=SPAN[0], last=SPAN[1], owner=OWNER, currency="CHF").totals
    assert totals is not None
    # 4000 + 25 - 7 - 300 - 12, as when the interest charged lowered the income.
    assert (totals.income, totals.spent, totals.saved) == (
        Decimal("4025.00"),
        Decimal("319.00"),
        Decimal("3706.00"),
    )


def test_it_is_no_payout(conn: sqlite3.Connection) -> None:
    shown = payouts.run(conn, first=SPAN[0], last=SPAN[1], owner=OWNER, currency="CHF")
    assert shown.totals.interest == Decimal("25.00")


@pytest.mark.parametrize(
    ("category", "amounts"),
    [
        (categories.INTEREST_CHARGED, [Decimal(-7)]),
        ("Interest charged", [Decimal(-7)]),
        (categories.GROUP_BANKING, [Decimal(-12), Decimal(-7)]),
        (categories.INTEREST, [Decimal(25)]),
    ],
)
def test_the_transactions_page_finds_what_each_line_adds_up(
    conn: sqlite3.Connection, category: str, amounts: list[Decimal]
) -> None:
    found = query.run(conn, query.Filter(owner=OWNER, category=category))
    assert [row.amount for row in found.rows] == amounts
    # Each row keeps the category it was stored with.
    assert {row.category for row in found.rows} <= {categories.FEES, categories.INTEREST}


def test_the_transactions_page_sums_it_under_banking(conn: sqlite3.Connection) -> None:
    rows = query.run(conn, query.Filter(owner=OWNER)).rows
    sums = {s.name: s.total for s in query.by_category(rows, store.category_ranks(conn))}
    assert sums[categories.GROUP_BANKING] == Decimal(-19)
    assert sums[categories.INTEREST] == Decimal(25)


def test_the_filter_offers_it_under_banking_but_no_row_can_be_given_it(
    conn: sqlite3.Connection,
) -> None:
    offered = query.choices(conn)
    assert dict(offered.categories)[categories.GROUP_BANKING][-1] == categories.INTEREST_CHARGED
    assert categories.INTEREST_CHARGED not in dict(offered.leaves)[categories.GROUP_BANKING]
    assert offered.label(categories.INTEREST_CHARGED) == "Interest charged"


def test_its_context_is_money_spent(conn: sqlite3.Connection) -> None:
    found = context.run(conn, "category", "Interest charged", owner=OWNER, currency="CHF")
    assert (found.name, found.earned, found.this_month) == (
        categories.INTEREST_CHARGED,
        False,
        Decimal("7.00"),
    )
    earned = context.run(conn, "category", categories.INTEREST, owner=OWNER, currency="CHF")
    assert (earned.earned, earned.this_month) == (True, Decimal("25.00"))
