"""Every list of categories follows the taxonomy's order (`categories.py`, kept as sort_order
in the database), never the names: renamed into another language, the order stays.

The rows tie on purpose, so only the order of the taxonomy can tell them apart: by name,
"Eating out" would come before "Food" and "Home", "Gifts received" before "Refunds".
"""

import sqlite3
from collections.abc import Iterator
from datetime import date

import pytest

from scrooge import cashflow, categories, income, months, paidby, query, spending
from scrooge.models import Kind, Transaction
from scrooge.storage import db, store
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close, stage_rows, txn

JANUARY = (date(2026, 1, 1), date(2026, 1, 31))
FEBRUARY = (date(2026, 2, 1), date(2026, 2, 28))


def _rows(month: int) -> list[Transaction]:
    def on(amount: str, ref: str, **fields: object) -> Transaction:
        return txn(amount, date=date(2026, month, 10), source_ref=f"{month}-{ref}", **fields)

    return [
        on("-50", "1", category=categories.EATING_OUT),
        on("-50", "2"),  # Groceries, under Food
        on("-25", "3", category=categories.UTILITIES),
        on("-25", "4", category=categories.HOUSEHOLD),
        on("100", "5", kind=Kind.INCOME, category=categories.SALARY),
        on("10", "6", kind=Kind.INCOME, category=categories.REFUNDS),
        on("10", "7", kind=Kind.INCOME, category=categories.GIFTS_RECEIVED),
        on("-5", "8", kind=Kind.TRADING, category=categories.TRADING),
        on("-5", "9", kind=Kind.SECURITY_TRADE, category=categories.INVESTMENTS),
    ]


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    close(connection, OWNER, JANUARY, JANUARY[1], ("migros", _rows(1)))
    close(connection, PARTNER, JANUARY, JANUARY[1], ("migros", _rows(1)))
    stage_rows(connection, OWNER, FEBRUARY, ("migros", _rows(2)), opening="-40")
    yield connection
    connection.close()


def _january_lines(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    found = cashflow.run(conn, first=JANUARY[0], last=JANUARY[1], owner=OWNER)
    return [(line.side, line.category) for line in found.lines]


def test_cash_flow_lines_follow_the_taxonomy(conn: sqlite3.Connection) -> None:
    assert _january_lines(conn) == [
        ("income", categories.SALARY),
        ("income", categories.REFUNDS),
        ("income", categories.GIFTS_RECEIVED),
        ("investing", categories.TRADING),
        ("investing", categories.INVESTMENTS),
        ("expenses", categories.GROUP_HOME),
        ("expenses", categories.GROUP_FOOD),
        ("expenses", categories.GROUP_EATING_OUT),
    ]
    expenses = [name for side, name in _january_lines(conn) if side == "expenses"]
    assert expenses == [name for name in categories.EXPENSE_GROUPS if name in expenses]


def test_labels_in_another_language_change_no_line_and_no_stored_row() -> None:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    close(connection, OWNER, JANUARY, JANUARY[1], ("migros", _rows(1)))
    english = _january_lines(connection)
    stored = "SELECT * FROM transactions ORDER BY id"
    rows = [tuple(row) for row in connection.execute(stored)]
    for key, italian in categories.LABELS_IT.items():
        connection.execute("UPDATE categories SET name = ? WHERE key = ?", (italian, key))
    assert _january_lines(connection) == english  # the lines name keys, in the same order
    assert [tuple(row) for row in connection.execute(stored)] == rows
    label = categories.Labels(language="it")  # what the pages would show in Italian
    assert [label(name) for _, name in english[-3:]] == ["Casa", "Alimentari", "Ristoranti e bar"]
    connection.close()


def test_spending_ties_follow_the_taxonomy(conn: sqlite3.Connection) -> None:
    shown = spending.run(conn, first=JANUARY[0], last=JANUARY[1], owner=OWNER, currency="CHF")
    assert [category.name for category in shown.categories] == [
        categories.GROUP_HOME,
        categories.GROUP_FOOD,
        categories.EATING_OUT,
    ]
    home = shown.categories[0]
    assert [leaf.name for leaf in home.leaves] == [categories.UTILITIES, categories.HOUSEHOLD]


def test_who_paid_ties_follow_the_taxonomy(conn: sqlite3.Connection) -> None:
    shown = paidby.run(conn, first=JANUARY[0], last=JANUARY[1], currency="CHF")
    assert shown is not None
    assert [split.name for split in shown.categories] == [
        categories.GROUP_HOME,
        categories.GROUP_FOOD,
        categories.EATING_OUT,
    ]


def test_income_source_ties_follow_the_taxonomy(conn: sqlite3.Connection) -> None:
    shown = income.run(conn, first=JANUARY[0], last=JANUARY[1], owner=OWNER, currency="CHF")
    leaves = [source.leaf for source in shown.sources]
    assert leaves == [
        categories.SALARY,
        categories.TRADING,
        categories.REFUNDS,
        categories.GIFTS_RECEIVED,
    ]


def test_transaction_totals_by_category_tie_in_the_taxonomy_order(
    conn: sqlite3.Connection,
) -> None:
    rows = query.run(conn, query.Filter(owner=OWNER)).rows
    found = query.by_category(rows, store.category_ranks(conn))
    assert [each.name for each in found] == [
        categories.SALARY,
        categories.GROUP_HOME,
        categories.GROUP_FOOD,
        categories.EATING_OUT,
        categories.INVESTMENTS,  # what was traded and bought, both under it
        categories.REFUNDS,
        categories.GIFTS_RECEIVED,
    ]


def test_the_choices_follow_the_taxonomy(conn: sqlite3.Connection) -> None:
    found = query.choices(conn)
    headings = [heading for heading, _ in found.categories]
    assert headings == [*categories.EXPENSE_GROUPS, "Income", "Transfer"]
    assert found.groups == tuple(categories.EXPENSE_GROUPS)


def test_staged_and_committed_totals_follow_the_taxonomy(conn: sqlite3.Connection) -> None:
    order = [
        categories.UTILITIES,
        categories.HOUSEHOLD,
        categories.GROCERIES,
        categories.EATING_OUT,
        categories.TRADING,
        categories.INVESTMENTS,
    ]
    incomes = [categories.SALARY, categories.REFUNDS, categories.GIFTS_RECEIVED]
    staged = months.draft(conn, OWNER).totals
    assert [total.category for total in staged] == [*order, *incomes]
    committed = months.checklist(conn, OWNER, JANUARY[0]).totals
    assert [total.category for total in committed] == [*order, *incomes]
