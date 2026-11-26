import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scrooge import categories, savings
from scrooge.cashflow import Grouping
from scrooge.models import Kind
from scrooge.savings import Totals
from scrooge.storage import db, store
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close, txn
from tests.test_income import salaries
from tests.test_spending import monthly

START, END = date(2025, 1, 1), date(2026, 8, 31)
JULY = date(2026, 7, 10)
LUKAS = [
    *salaries("5000", START, 20),
    *monthly("-1800", 1, "rent", category=categories.RENT),
    txn("300", date=JULY, source_ref="1", kind=Kind.TRADING, category=categories.TRADING),
    txn(
        "-9920",
        date=JULY,
        source_ref="2",
        kind=Kind.SECURITY_TRADE,
        category=categories.INVESTMENTS,
    ),
    # A present for her, and her share of the rent from her: each leg also on her side.
    txn("-200", date=JULY, source_ref="3", category=categories.GIFTS, counterparty="ANNA MEIER"),
    txn(
        "900",
        date=JULY,
        source_ref="4",
        kind=Kind.INCOME,
        category=categories.REFUNDS,
        counterparty="ANNA MEIER",
    ),
]
ANNA = [
    *salaries("4000", date(2026, 1, 1), 8),
    txn("-60", date=JULY, source_ref="1"),
    txn(
        "200",
        date=JULY,
        source_ref="2",
        kind=Kind.INCOME,
        category=categories.GIFTS_RECEIVED,
        counterparty="LUKAS MEIER",
    ),
    txn("-900", date=JULY, source_ref="3", category=categories.RENT, counterparty="LUKAS MEIER"),
]


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    close(connection, OWNER, (START, END), END, ("migros", LUKAS))
    close(connection, PARTNER, (date(2026, 1, 1), END), END, ("migros", ANNA))
    yield connection
    connection.close()


def saved(
    conn: sqlite3.Connection,
    *,
    first: date | None = date(2026, 1, 1),
    last: date | None = END,
    owner: str | None = OWNER,
    grouping: Grouping = Grouping.MONTH,
) -> savings.Savings:
    return savings.run(conn, first=first, last=last, owner=owner, grouping=grouping, currency="CHF")


def D(value: str | int) -> Decimal:  # reads like the type it makes
    return Decimal(value)


def test_the_range_against_the_same_range_a_year_before(conn: sqlite3.Connection) -> None:
    result = saved(conn)
    assert (result.first, result.last, result.through) == (date(2026, 1, 1), END, END)
    # Her share of the rent lowers what he spent; the present for her is spent.
    assert result.totals == Totals(D(40300), D(13700), D(40000))
    assert result.totals.saved == D(26600)
    assert (result.totals.rate, result.totals.from_work) == (D(26600) / 40300, D(40000) / 40300)
    # 26600 saved of 40000 of salary.
    assert result.totals.rate_salary == D("0.665")
    assert result.year_before == Totals(D(40000), D(14400), D(40000))


def test_each_period_with_the_rate_of_its_twelve_months(conn: sqlite3.Connection) -> None:
    by_name = {period.name: period for period in saved(conn).periods}
    july = by_name["2026-07"]
    assert july.totals == Totals(D(5300), D(1100), D(5000))
    # August 2025 to July 2026: 39400 of 60300 saved.
    assert july.rate == D(39400) / 60300
    # Of the 60000 the salary brought in over those months.
    assert july.rate_salary == D(39400) / 60000
    november = saved(conn, first=date(2025, 11, 1), last=date(2025, 11, 30)).periods[0]
    assert (november.rate, november.rate_salary) == (None, None)


def test_for_one_the_other_persons_money_is_spending_and_for_both_nothing(
    conn: sqlite3.Connection,
) -> None:
    first, last = date(2026, 7, 1), date(2026, 7, 31)
    hers = saved(conn, owner=PARTNER, first=first, last=last).totals
    assert hers == Totals(D(4000), D(760), D(4000))
    both = saved(conn, owner=None, first=first, last=last).totals
    assert both == Totals(D(9300), D(1860), D(9000))
    # Her months before January are not known: no year before for both.
    assert saved(conn, owner=None).year_before is None


def test_nothing_to_show() -> None:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    result = savings.run(connection, currency="CHF")
    assert (result.totals, result.periods, result.first) == (None, (), None)
    assert Totals(D(0), D(10), D(0)).rate is None
    assert Totals(D(0), D(10), D(0)).from_work is None
    # Income that is not from work leaves no rate on salary.
    assert Totals(D(100), D(10), D(0)).rate_salary is None


def test_next_to_nothing_in_leaves_no_savings_rate() -> None:
    # 15 in and 1,015 spent is -1,000 saved, not a -6,667% rate.
    assert Totals(D(15), D(1015), D(0)).rate is None
    assert Totals(D("99.99"), D(1000), D(0)).rate is None
    assert Totals(D(100), D(20), D(0)).rate == D("0.8")
    # The same floor holds for the rate on salary; from_work still reads the share of income.
    tiny_work = Totals(D(500), D(200), D(50))
    assert (tiny_work.rate, tiny_work.rate_salary, tiny_work.from_work) == (
        D("0.6"),
        None,
        D("0.1"),
    )
    assert Totals(D(500), D(200), D(100)).rate_salary == D(3)


def test_the_floor_follows_the_currency_asked_for() -> None:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    day = date(2026, 8, 15)
    rows = [
        txn("15", date=day, source_ref="1", kind=Kind.INCOME, category=categories.INTEREST),
        txn("-1015", date=day, source_ref="2"),
    ]
    close(
        connection,
        OWNER,
        (date(2026, 8, 1), date(2026, 8, 31)),
        date(2026, 8, 31),
        ("migros", rows),
    )
    store.set_rate(connection, date(2026, 8, 1), "EUR", D("0.94"), source="fetched:yahoo")
    store.set_rate(connection, date(2026, 8, 1), "JPY", D("0.0054"), source="fetched:yahoo")
    # 15 CHF is over 2,700 JPY, yet still below the 100 CHF floor converted into JPY.
    for currency in ("CHF", "EUR", "JPY"):
        shown = savings.run(connection, owner=OWNER, currency=currency)
        assert shown.totals is not None
        assert (shown.totals.rate, shown.periods[0].totals.rate) == (None, None)

    # At the floor: 100 CHF counts in both CHF and EUR (106.38 EUR).
    at_floor = db.connect(":memory:")
    db.migrate(at_floor, CLOSED_AT)
    close(
        at_floor,
        OWNER,
        (date(2026, 8, 1), date(2026, 8, 31)),
        date(2026, 8, 31),
        (
            "migros",
            [
                txn(
                    "100", date=day, source_ref="1", kind=Kind.INCOME, category=categories.INTEREST
                ),
                txn("-20", date=day, source_ref="2"),
            ],
        ),
    )
    store.set_rate(at_floor, date(2026, 8, 1), "EUR", D("0.94"), source="fetched:yahoo")
    eur = savings.run(at_floor, owner=OWNER, currency="EUR")
    assert eur.totals is not None
    assert eur.totals.rate is not None
    assert eur.periods[0].totals.rate is not None

    # Without an FX rate stored for the display currency, fall back to 100 of that currency.
    usd_only = db.connect(":memory:")
    db.migrate(usd_only, CLOSED_AT)
    close(
        usd_only,
        OWNER,
        (date(2026, 8, 1), date(2026, 8, 31)),
        date(2026, 8, 31),
        (
            "migros",
            [
                txn(
                    "99",
                    date=day,
                    source_ref="1",
                    kind=Kind.INCOME,
                    category=categories.INTEREST,
                    currency="USD",
                ),
                txn("-50", date=day, source_ref="2", currency="USD"),
            ],
        ),
    )
    usd = savings.run(usd_only, owner=OWNER, currency="USD")
    assert usd.totals is not None
    assert usd.totals.rate is None
    for db_conn in (connection, at_floor, usd_only):
        db_conn.close()


@given(
    first=st.dates(date(2024, 6, 1), date(2026, 12, 31)),
    days=st.integers(0, 700),
)
def test_the_two_peoples_savings_add_up_to_the_familys(
    conn: sqlite3.Connection, first: date, days: int
) -> None:
    last = date.fromordinal(first.toordinal() + days)
    each = [saved(conn, first=first, last=last, owner=owner).totals for owner in (OWNER, PARTNER)]
    both = saved(conn, first=first, last=last, owner=None).totals
    his_and_hers = sum((totals.saved for totals in each if totals), D(0))
    assert his_and_hers == (both.saved if both else 0)
