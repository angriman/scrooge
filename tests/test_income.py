import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scrooge import categories, income, query
from scrooge.cashflow import Grouping
from scrooge.income import Source
from scrooge.models import Kind, Transaction
from scrooge.networth import MissingRateError
from scrooge.storage import db, store
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close, txn

START, END = date(2025, 1, 1), date(2026, 8, 31)
SALARY = {"kind": Kind.INCOME, "category": categories.SALARY}
BILL = {"kind": Kind.SECURITY_TRADE, "category": categories.INVESTMENTS}
TRADE = {"kind": Kind.TRADING, "category": categories.TRADING}
USD = {"account": "IBKR USD", "currency": "USD", "source_file": "ibkr.csv"}


def salaries(amount: str, first: date, months: int) -> list[Transaction]:
    return [
        txn(
            amount,
            date=date(first.year + (first.month - 1 + n) // 12, (first.month - 1 + n) % 12 + 1, 25),
            source_ref=f"s{n}",
            **SALARY,
        )
        for n in range(months)
    ]


LUKAS = [
    *salaries("5000", START, 20),
    txn(
        "10",
        date=date(2026, 6, 30),
        source_ref="1",
        kind=Kind.INTEREST,
        category=categories.INTEREST,
    ),
    txn(
        "-4",
        date=date(2026, 7, 31),
        source_ref="2",
        kind=Kind.INTEREST,
        category=categories.INTEREST,
    ),
    # Options closed at a gain, then at a loss: income is what is left of the two.
    txn("300", date=date(2026, 7, 10), source_ref="3", **TRADE),
    txn("-120", date=date(2026, 7, 20), source_ref="4", **TRADE),
    # A bill bought and repaid: the money only came back.
    txn("-9920", date=date(2026, 7, 3), source_ref="5", **BILL),
    txn("9920", date=date(2026, 7, 24), source_ref="6", **BILL),
    txn(
        "15", date=date(2026, 7, 5), source_ref="7", kind=Kind.INCOME, category=categories.CASHBACK
    ),
    # Paid back by the other person, and a refund that lowers an expense: neither is income.
    txn(
        "50",
        date=date(2026, 7, 6),
        source_ref="8",
        kind=Kind.INCOME,
        category=categories.REFUNDS,
        counterparty="ANNA MEIER",
    ),
    txn("20", date=date(2026, 7, 7), source_ref="9", kind=Kind.INCOME),
    txn("-1850", date=date(2026, 7, 2), source_ref="10", category=categories.RENT),
]
LUKAS_USD = [
    txn(
        "100",
        date=date(2025, 3, 15),
        source_ref="1",
        kind=Kind.DIVIDEND,
        category=categories.DIVIDENDS,
        **USD,
    ),
    txn(
        "100",
        date=date(2026, 3, 16),
        source_ref="2",
        kind=Kind.DIVIDEND,
        category=categories.DIVIDENDS,
        **USD,
    ),
]
ANNA = [
    *salaries("4000", date(2026, 1, 1), 8),
    txn(
        "200",
        date=date(2026, 7, 10),
        source_ref="1",
        kind=Kind.INCOME,
        category=categories.REFUNDS,
        counterparty="LUKAS MEIER",
    ),
]


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    close(connection, OWNER, (START, END), END, ("migros", LUKAS), ("ibkr", LUKAS_USD))
    close(connection, PARTNER, (date(2026, 1, 1), END), END, ("migros", ANNA))
    for day, currency, rate in (
        (date(2025, 1, 1), "EUR", "0.95"),
        (date(2025, 3, 14), "USD", "0.80"),
        (date(2026, 3, 16), "USD", "0.90"),
    ):
        store.set_rate(connection, day, currency, Decimal(rate), source="fetched:yahoo")
    yield connection
    connection.close()


def his(  # the keywords of `run`, with this file's defaults
    conn: sqlite3.Connection,
    *,
    first: date | None = date(2026, 1, 1),
    last: date | None = END,
    owner: str | None = OWNER,
    grouping: Grouping = Grouping.MONTH,
    currency: str = "CHF",
) -> income.Income:
    return income.run(
        conn, first=first, last=last, owner=owner, grouping=grouping, currency=currency
    )


def groups(lavoro: str, investimenti: str = "0", altro: str = "0") -> dict[str, Decimal]:
    return dict(zip(income.GROUPS, map(Decimal, (lavoro, investimenti, altro)), strict=True))


def test_every_period_of_the_range_is_there_even_without_income(conn: sqlite3.Connection) -> None:
    result = his(conn, first=date(2026, 9, 1), last=date(2026, 10, 31))
    assert [p.name for p in result.periods] == ["2026-09", "2026-10"]
    assert all(p.total == 0 for p in result.periods)


def test_income_is_grouped_by_where_it_comes_from(conn: sqlite3.Connection) -> None:
    result = his(conn)
    by_month = {p.name: p.groups for p in result.periods}
    assert list(by_month) == [f"2026-{m:02}" for m in range(1, 9)]
    assert by_month["2026-01"] == groups("5000")
    assert by_month["2026-03"] == groups("5000", "90")  # 100 USD at 0.90 that day
    assert by_month["2026-06"] == groups("5000", "10")
    # Trading net (300 less 120); the interest charged is an expense (plan decision 52), and
    # the bill, the refund and what the other person paid back are not there.
    assert by_month["2026-07"] == groups("5000", "180", "15")


def test_the_sources_add_up_the_range_leaf_by_leaf(conn: sqlite3.Connection) -> None:
    assert his(conn).sources == (
        Source(income.WORK, categories.SALARY, 8, Decimal(40000)),
        Source(income.INVESTMENTS, categories.TRADING, 2, Decimal(180)),
        Source(income.INVESTMENTS, categories.DIVIDENDS, 1, Decimal(90)),
        Source(income.INVESTMENTS, categories.INTEREST, 1, Decimal(10)),
        Source(income.OTHER, categories.CASHBACK, 1, Decimal(15)),
    )


def test_each_row_is_converted_at_the_rate_of_its_own_day(conn: sqlite3.Connection) -> None:
    year = his(conn, first=date(2025, 1, 1), last=date(2025, 12, 31), grouping=Grouping.YEAR)
    assert [p.groups for p in year.periods] == [groups("60000", "80")]  # at 0.80 in 2025
    july = his(conn, first=date(2026, 7, 1), last=date(2026, 7, 31), currency="EUR")
    # Francs into euros at 0.95, rounded to the cent once added up.
    assert july.periods[0].groups == groups("5263.16", "189.47", "15.79")
    assert july.currency == "EUR"


def test_the_employers_share_is_of_the_twelve_months_ending_with_each_period(
    conn: sqlite3.Connection,
) -> None:
    share = {p.name: p.employer for p in his(conn, first=START).periods}
    # Before twelve months of income are known, there is no share.
    assert share["2025-01"] is None
    assert share["2025-11"] is None
    assert share["2025-12"] == Decimal(60000) / 60080  # the 2025 dividend
    assert share["2026-07"] == share["2026-08"] == Decimal(60000) / 60295


def test_a_period_after_the_statements_takes_the_twelve_months_up_to_them(
    conn: sqlite3.Connection,
) -> None:
    quarters = his(conn, grouping=Grouping.QUARTER, last=date(2026, 12, 31))
    share = {p.name: p.employer for p in quarters.periods}
    assert share == {
        "2026-Q1": Decimal(60000) / 60090,  # the 2026 dividend replaced 2025's
        "2026-Q2": Decimal(60000) / 60100,
        "2026-Q3": Decimal(60000) / 60295,  # up to 31 August, the last day committed
        "2026-Q4": None,  # no day of it is committed
    }
    assert quarters.through == END


def test_both_people_without_what_they_pay_each_other(conn: sqlite3.Connection) -> None:
    both = his(conn, owner=None, first=date(2026, 7, 1), last=date(2026, 7, 31))
    assert both.periods[0].groups == groups("9000", "180", "15")
    # Her income is known from January only: twelve months of it are not there yet.
    assert both.periods[0].employer is None
    hers = his(conn, owner=PARTNER, first=date(2026, 7, 1), last=date(2026, 7, 31))
    assert hers.periods[0].groups == groups("4000")


def test_without_a_range_it_runs_from_the_first_income_to_the_statements_end(
    conn: sqlite3.Connection,
) -> None:
    result = his(conn, first=None, last=None, grouping=Grouping.YEAR)
    assert [p.name for p in result.periods] == ["2025", "2026"]


def test_an_empty_database_has_no_periods() -> None:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    result = income.run(connection, currency="CHF")
    assert result.periods == ()
    assert result.sources == ()
    assert result.through is None


def test_a_currency_without_rates_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(MissingRateError, match="GBP"):
        his(conn, currency="GBP")


def test_an_unknown_person_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(query.QueryError):
        his(conn, owner="Nobody")


@given(
    first=st.dates(date(2024, 6, 1), date(2026, 12, 31)),
    days=st.integers(0, 700),
    grouping=st.sampled_from(list(Grouping)),
)
def test_the_periods_add_up_to_the_sources(
    conn: sqlite3.Connection, first: date, days: int, grouping: Grouping
) -> None:
    last = date.fromordinal(first.toordinal() + days)
    result = his(conn, first=first, last=last, grouping=grouping, currency="EUR")
    by_periods = sum((p.total for p in result.periods), Decimal(0))
    by_sources = sum((s.total for s in result.sources), Decimal(0))
    # Each is rounded to the cent on its own.
    assert abs(by_periods - by_sources) <= Decimal("0.01") * (len(result.periods) + 5)
    for p in result.periods:
        assert p.employer is None or 0 < p.employer <= 1
