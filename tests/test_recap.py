"""One month at a glance: how net worth moved over it, and why (`recap`)."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from scrooge import categories, overview, recap, savings, spending
from scrooge.models import Kind
from scrooge.recap import Category
from scrooge.storage import db
from tests.test_income import SALARY
from tests.test_networth import NOW, account, close, spent
from tests.test_overview import household

D = Decimal
SEPTEMBER, OCTOBER = date(2026, 9, 1), date(2026, 10, 1)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def test_nothing_committed_is_no_month(conn: sqlite3.Connection) -> None:
    assert recap.run(conn, currency="CHF") is None


def test_a_month_is_how_net_worth_moved_and_what_was_saved_of_it(
    conn: sqlite3.Connection,
) -> None:
    household(conn)
    shown = recap.run(conn, currency="USD")
    assert shown is not None
    assert (shown.month, shown.last, shown.over) == (SEPTEMBER, date(2026, 9, 30), True)
    # From the eve of the month to its last day, as the first page has it.
    first_page = overview.run(conn, currency="USD")
    assert (shown.before, shown.after) == (first_page.changes[0].before, first_page.total)
    assert (shown.before, shown.after) == (D("30975.00"), D("30847.50"))
    # Nothing came in; 120 francs on the card, at 0.80 francs a dollar, went out.
    assert (shown.income, shown.spent, shown.rate) == (D(0), D("150.00"), None)
    assert shown.saved == D("-150.00")
    assert shown.categories == (Category(categories.GROUP_FOOD, D("150.00"), None),)
    # Of the 127.50 dollars net worth fell, 150 were spent: the prices made up 22.50.
    assert (shown.moved, shown.other) == (D("-127.50"), D("22.50"))
    assert shown.missing == ()
    assert (shown.earlier, shown.later) == (date(2026, 8, 1), None)


def test_income_and_spending_are_those_of_the_analysis(conn: sqlite3.Connection) -> None:
    household(conn)
    shown = recap.run(conn, owner="Lukas", currency="CHF")
    assert shown is not None
    last = date(2026, 9, 30)
    totals = savings.run(conn, first=SEPTEMBER, last=last, owner="Lukas", currency="CHF").totals
    assert totals is not None
    assert (shown.income, shown.spent) == (totals.income, totals.spent)
    spent = spending.run(conn, first=SEPTEMBER, last=last, owner="Lukas", currency="CHF")
    assert shown.categories == tuple(Category(c.name, c.spent, c.usual) for c in spent.categories)


def test_what_was_saved_is_also_a_part_of_the_salary(conn: sqlite3.Connection) -> None:
    rows = (
        spent("5000", date(2026, 9, 25), **SALARY),
        spent("1000", date(2026, 9, 26), kind=Kind.INCOME, category=categories.INTEREST),
        spent("-1500", date(2026, 9, 10)),
    )
    close(conn, account("migros", "Migros", "CHF", "1000", *rows))
    shown = recap.run(conn, currency="CHF")
    assert shown is not None
    # 4500 saved of 6000 that came in, of which 5000 from work.
    assert (shown.income, shown.work, shown.saved) == (D("6000.00"), D("5000.00"), D("4500.00"))
    assert (shown.rate, shown.rate_salary) == (D("0.750"), D("0.900"))
    # One month of history: no twelve months to measure.
    assert (shown.rate_year, shown.rate_salary_year) == (None, None)


def test_a_month_with_next_to_nothing_in_has_no_savings_rate(conn: sqlite3.Connection) -> None:
    rows = (
        spent("15", date(2026, 9, 26), kind=Kind.INCOME, category=categories.INTEREST),
        spent("-1015", date(2026, 9, 10)),
    )
    close(conn, account("migros", "Migros", "CHF", "2000", *rows))
    shown = recap.run(conn, currency="CHF")
    assert shown is not None
    assert (shown.income, shown.spent, shown.saved) == (D("15.00"), D("1015.00"), D("-1000.00"))
    assert (shown.rate, shown.rate_salary) == (None, None)


def test_the_month_shown_is_the_last_every_account_covers(conn: sqlite3.Connection) -> None:
    household(conn)
    # October of the bank account alone: the others still end on 30 September.
    close(conn, account("migros", "Migros", "CHF", "700", month=OCTOBER), month=OCTOBER)
    shown = recap.run(conn, currency="CHF")
    assert shown is not None
    assert (shown.month, shown.later) == (SEPTEMBER, OCTOBER)
    october = recap.run(conn, currency="CHF", month=date(2026, 10, 15))
    assert october is not None
    assert (october.month, october.earlier, october.later) == (OCTOBER, SEPTEMBER, None)
    assert [(line.owner, line.account) for line in october.missing] == [
        ("Anna", "IBKR USD"),
        ("Anna", "Revolut USD"),
        ("Lukas", "IBKR USD"),
        ("Lukas", "Swisscard"),
    ]


def test_a_month_outside_the_history_is_the_nearest_in_it(conn: sqlite3.Connection) -> None:
    household(conn)
    later = recap.run(conn, currency="CHF", month=date(2030, 1, 1))
    earlier = recap.run(conn, currency="CHF", month=date(2020, 1, 1))
    assert later is not None
    assert earlier is not None
    assert later.month == SEPTEMBER
    # The history starts on the last day of August: how August moved is not known.
    assert (earlier.month, earlier.earlier, earlier.before) == (date(2026, 8, 1), None, None)
    assert (earlier.moved, earlier.other) == (None, None)


def test_a_month_that_has_not_ended_is_so_far(conn: sqlite3.Connection) -> None:
    household(conn)
    shown = recap.run(conn, currency="CHF")
    assert shown is not None
    assert not replace(shown, last=date(2026, 9, 20)).over


@pytest.mark.parametrize(
    ("spent", "usual", "against"),
    [("150", "120", "25.0"), ("80", "120", "-33.3"), ("150", None, None), ("150", "0", None)],
)
def test_a_category_is_set_against_its_usual_month(
    spent: str, usual: str | None, against: str | None
) -> None:
    category = Category(categories.GROUP_FOOD, D(spent), None if usual is None else D(usual))
    assert category.against_usual == (None if against is None else D(against))


def test_what_an_account_brings_when_it_first_counts_is_neither_saved_nor_made(
    conn: sqlite3.Connection,
) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000", spent("-100", date(2026, 9, 10))))
    rows = spent("-20", date(2026, 10, 5), account="Revolut CHF")
    close(conn, account("revolut", "Revolut CHF", "CHF", "500", rows, month=OCTOBER), month=OCTOBER)
    september = recap.run(conn, currency="CHF", month=SEPTEMBER)
    assert september is not None
    # From 1000 to 1400: 100 spent, and the 500 the second account opened with on 30 September.
    assert (september.before, september.after) == (D("1000.00"), D("1400.00"))
    assert (september.saved, september.arrived, september.other) == (
        D("-100.00"),
        D("500.00"),
        D("0.00"),
    )
    october = recap.run(conn, currency="CHF", month=OCTOBER)
    assert october is not None
    assert october.arrived == 0  # it came on the eve of October: October starts with it
