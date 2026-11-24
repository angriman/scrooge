import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scrooge import cashflow, query
from scrooge.cashflow import Balance, Grouping, Line
from scrooge.models import Kind
from scrooge.storage import db
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close, txn

START, END = date(2025, 1, 1), date(2026, 8, 31)
EUR = {"account": "Revolut EUR", "currency": "EUR", "source_file": "revolut.tsv"}
GIFT = "Anna Meier"  # the other person: recognised whatever the case
SALARY = {"kind": Kind.INCOME, "category": "Salary"}
BILL = {"kind": Kind.SECURITY_TRADE, "category": "Investments"}

LUKAS = [
    txn("-45.20", date=date(2025, 5, 3), source_ref="1"),
    txn("-80", date=date(2025, 5, 20), source_ref="2", category="Eating out"),
    txn("20", date=date(2025, 5, 25), source_ref="3", kind=Kind.INCOME),  # a refund
    txn("6000", date=date(2025, 5, 25), source_ref="4", **SALARY),
    txn("-1850", date=date(2025, 6, 2), source_ref="5", category="Rent"),
    # Bought on the last day of June, booked in July.
    txn("-15", date=date(2026, 6, 30), booking_date=date(2026, 7, 2), source_ref="6"),
    txn(
        "-1000", date=date(2026, 7, 2), source_ref="7", kind=Kind.TRANSFER, category="Own transfer"
    ),
    txn("-500", date=date(2026, 7, 3), source_ref="8", kind=Kind.FX, category="Buy EUR"),
    txn("-70", date=date(2026, 7, 4), source_ref="9", kind=Kind.INTERNAL, category=""),
    txn("-200", date=date(2026, 7, 10), source_ref="10", category="Gifts", counterparty=GIFT),
    txn("6100", date=date(2026, 7, 25), source_ref="11", **SALARY),
    # A bill bought and repaid with its interest, and a futures trade closed at a loss.
    txn("-9920", date=date(2026, 8, 3), source_ref="12", **BILL),
    txn("9920", date=date(2026, 8, 20), source_ref="13", **BILL),
    txn("80", date=date(2026, 8, 20), source_ref="14", kind=Kind.INTEREST, category="Interest"),
    txn("-35", date=date(2026, 8, 21), source_ref="15", kind=Kind.TRADING, category="Trading"),
]
LUKAS_EUR = [txn("-30", date=date(2026, 7, 5), source_ref="1", category="Eating out", **EUR)]
ANNA = [
    txn(
        "200",
        date=date(2026, 7, 10),
        source_ref="1",
        kind=Kind.INCOME,
        category="Refunds",
        counterparty="LUKAS MEIER",
    ),
    txn("-60", date=date(2026, 7, 12), source_ref="2"),
]


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    close(connection, OWNER, (START, END), END, ("migros", LUKAS), ("revolut", LUKAS_EUR))
    close(connection, PARTNER, (date(2026, 7, 1), END), END, ("migros", ANNA))
    yield connection
    connection.close()


def july(conn: sqlite3.Connection, owner: str | None) -> cashflow.CashFlow:
    return cashflow.run(conn, first=date(2026, 7, 1), last=date(2026, 7, 31), owner=owner)


def line(where: str, category: str, count: int, total: str) -> Line:
    """`where` is the period, the currency and the side: `2025-05 CHF entrate`."""
    period, currency, side = where.rpartition(" ")[0].rpartition(" ")[0], *where.split()[-2:]
    return Line(period, currency, side, category, count, Decimal(total))


def balance(period: str, currency: str, income: str, expenses: str) -> Balance:
    return Balance(period, currency, Decimal(income), Decimal(expenses), Decimal(0))


def test_a_month_is_income_and_expenses_by_top_level_category(conn: sqlite3.Connection) -> None:
    may = cashflow.run(conn, first=date(2025, 5, 1), last=date(2025, 5, 31), owner=OWNER)
    assert may.lines == (
        line("2025-05 CHF entrate", "Salary", 1, "6000"),
        line("2025-05 CHF uscite", "Eating out", 1, "-80"),
        line("2025-05 CHF uscite", "Food", 2, "-25.2"),  # the refund lowers it
    )
    assert may.balances == (balance("2025-05", "CHF", "6000", "-105.2"),)
    assert may.balances[0].net == Decimal("5894.8")


def test_transfers_exchanges_and_internal_rows_are_never_counted(conn: sqlite3.Connection) -> None:
    his = july(conn, OWNER)
    assert his.lines == (
        line("2026-07 CHF entrate", "Salary", 1, "6100"),
        line("2026-07 CHF uscite", "Gifts", 1, "-200"),
        line("2026-07 EUR uscite", "Eating out", 1, "-30"),
    )
    assert his.balances == (
        balance("2026-07", "CHF", "6100", "-200"),
        balance("2026-07", "EUR", "0", "-30"),
    )


def test_what_the_two_pay_each_other_counts_for_one_and_not_for_both(
    conn: sqlite3.Connection,
) -> None:
    hers = july(conn, PARTNER)
    assert hers.balances == (balance("2026-07", "CHF", "200", "-60"),)
    both = july(conn, None)
    assert both.lines == (
        line("2026-07 CHF entrate", "Salary", 1, "6100"),
        line("2026-07 CHF uscite", "Food", 1, "-60"),
        line("2026-07 EUR uscite", "Eating out", 1, "-30"),
    )


def test_securities_and_trading_are_investing_not_income_or_expenses(
    conn: sqlite3.Connection,
) -> None:
    august = cashflow.run(conn, first=date(2026, 8, 1), last=END)
    assert august.lines == (
        line("2026-08 CHF entrate", "Interest", 1, "80"),
        line("2026-08 CHF investing", "Investments", 2, "0"),
        line("2026-08 CHF investing", "Trading", 1, "-35"),
    )
    (month,) = august.balances
    assert month == Balance("2026-08", "CHF", Decimal(80), Decimal(0), Decimal(-35))
    assert month.net == Decimal(80)


def test_the_whole_range_is_added_up_per_category(conn: sqlite3.Connection) -> None:
    result = cashflow.run(conn, first=date(2025, 1, 1), last=date(2025, 12, 31), owner=OWNER)
    assert result.totals == (
        line(" CHF entrate", "Salary", 1, "6000"),
        line(" CHF uscite", "Eating out", 1, "-80"),
        line(" CHF uscite", "Food", 2, "-25.2"),
        line(" CHF uscite", "Home", 1, "-1850"),
    )


def test_a_row_counts_on_the_day_it_really_happened(conn: sqlite3.Connection) -> None:
    june = cashflow.run(conn, first=date(2026, 6, 1), last=date(2026, 6, 30))
    assert june.lines == (line("2026-06 CHF uscite", "Food", 1, "-15"),)


def test_both_ends_of_the_range_are_included(conn: sqlite3.Connection) -> None:
    result = cashflow.run(conn, first=date(2025, 5, 3), last=date(2025, 5, 20))
    assert [str(item.total) for item in result.lines] == ["-80", "-45.2"]


@pytest.mark.parametrize(
    ("grouping", "periods"),
    [
        (Grouping.WEEK, ["2025-W18", "2025-W21", "2025-W23"]),
        (Grouping.MONTH, ["2025-05", "2025-06"]),
        (Grouping.QUARTER, ["2025-Q2"]),
        (Grouping.YEAR, ["2025"]),
    ],
)
def test_periods_can_be_weeks_months_quarters_or_years(
    conn: sqlite3.Connection, grouping: Grouping, periods: list[str]
) -> None:
    result = cashflow.run(conn, last=date(2025, 12, 31), grouping=grouping)
    assert [item.period for item in result.balances] == periods
    assert sum(item.net for item in result.balances) == Decimal("4044.8")


def test_everything_is_read_without_a_range(conn: sqlite3.Connection) -> None:
    years = cashflow.run(conn, grouping=Grouping.YEAR).balances
    assert [(item.period, item.currency) for item in years] == [
        ("2025", "CHF"),
        ("2026", "CHF"),
        ("2026", "EUR"),
    ]


@pytest.mark.parametrize(
    ("day", "grouping", "name"),
    [
        (date(2024, 12, 30), Grouping.WEEK, "2025-W01"),  # the week belongs to the next year
        (date(2027, 1, 3), Grouping.WEEK, "2026-W53"),
        (date(2026, 3, 31), Grouping.QUARTER, "2026-Q1"),
        (date(2026, 4, 1), Grouping.QUARTER, "2026-Q2"),
        (date(2026, 12, 31), Grouping.QUARTER, "2026-Q4"),
        (date(2026, 1, 9), Grouping.MONTH, "2026-01"),
        (date(2026, 1, 9), Grouping.YEAR, "2026"),
    ],
)
def test_period_names(day: date, grouping: Grouping, name: str) -> None:
    assert cashflow.period(day, grouping) == name


@given(day=st.dates(date(2000, 1, 1), date(2100, 12, 31)), grouping=st.sampled_from(Grouping))
def test_a_period_covers_the_days_named_after_it(day: date, grouping: Grouping) -> None:
    first, last = cashflow.days(cashflow.period(day, grouping))
    assert first <= day <= last
    assert cashflow.period(first, grouping) == cashflow.period(last, grouping)
    before, after = date.fromordinal(first.toordinal() - 1), date.fromordinal(last.toordinal() + 1)
    assert cashflow.period(before, grouping) != cashflow.period(day, grouping)
    assert cashflow.period(after, grouping) != cashflow.period(day, grouping)


def test_an_unknown_owner_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(query.QueryError, match="no owner named 'Lucas'"):
        cashflow.run(conn, owner="Lucas")


@given(
    first=st.dates(START, END),
    days=st.integers(0, 700),
    owner=st.sampled_from([OWNER, PARTNER]),
    grouping=st.sampled_from(Grouping),
)
def test_lines_and_balances_add_up_to_the_counted_transactions(
    conn: sqlite3.Connection, first: date, days: int, owner: str, grouping: Grouping
) -> None:
    last = date.fromordinal(first.toordinal() + days)
    result = cashflow.run(conn, first=first, last=last, owner=owner, grouping=grouping)
    rows = query.run(conn, query.Filter(first=first, last=last, owner=owner)).rows
    moved = (Kind.TRANSFER, Kind.FX, Kind.INTERNAL)
    for currency in ("CHF", "EUR"):
        counted = [r.amount for r in rows if r.kind not in moved and r.currency == currency]
        lines = [item for item in result.lines if item.currency == currency]
        balances = [item for item in result.balances if item.currency == currency]
        assert sum(item.total for item in lines) == sum(counted)
        assert sum(item.count for item in lines) == len(counted)
        assert sum(item.net + item.investing for item in balances) == sum(counted)
        assert sum(item.total for item in result.totals if item.currency == currency) == sum(
            counted
        )
