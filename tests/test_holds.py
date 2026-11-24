"""What each account holds now by part, and its balance at the last month-ends (`holds.of`)."""

import sqlite3
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal

import pytest

from scrooge import holds, networth
from scrooge.holds import Cash, Holds, MonthEnd
from scrooge.models import CashBalance, Transaction
from scrooge.networth import CASH, ETFS, MissingRateError
from scrooge.pipeline import Statement
from scrooge.storage import db, store
from tests.storage.helpers import AUGUST, OWNER, SEPTEMBER, statement
from tests.test_networth import NOW, account, bought, close, price, rate, spent

D = Decimal


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def broker(month: date, dollars: tuple[str, str], euros: str, *rows: Transaction) -> Statement:
    """An IBKR statement of one month: its dollar account opens and closes as `dollars` say,
    its euro account keeps `euros`."""
    file = f"IBKR {month:%Y-%m}.csv"
    balances = [
        CashBalance("IBKR USD", "USD", D(dollars[0]), D(dollars[1])),
        CashBalance("IBKR EUR", "EUR", D(euros), D(euros)),
    ]
    return statement("ibkr", rows, balances, filename=file, month=month)


def two_months(conn: sqlite3.Connection) -> None:
    """August and September 2026 of Lukas: a bank account in francs, and a broker that keeps
    dollars, euros and an ETF bought in August. A dollar is 0.80 francs, a euro 0.90."""
    widgets = bought("WIDG", "10", "-500", date(2026, 8, 20), source_file="IBKR 2026-08.csv")
    close(
        conn,
        account("migros", "Migros", "CHF", "1000", spent("-200", date(2026, 8, 5)), month=AUGUST),
        broker(AUGUST, ("5000", "4500"), "2000", widgets),
        month=AUGUST,
    )
    close(
        conn,
        account("migros", "Migros", "CHF", "800", spent("-100", date(2026, 9, 5))),
        broker(SEPTEMBER, ("4500", "4500"), "2000"),
    )
    price(conn, "WIDG", date(2026, 8, 31), "50")
    price(conn, "WIDG", date(2026, 9, 30), "60")
    rate(conn, "USD", date(2026, 8, 1), "0.8")
    rate(conn, "EUR", date(2026, 8, 1), "0.9")


def test_each_account_holds_its_cash_and_its_securities_apart(conn: sqlite3.Connection) -> None:
    two_months(conn)
    valued = networth.value(conn, OWNER)
    found = holds.of(conn, valued, "CHF")
    # The history starts on the eve of August: what the first statements open with.
    ends = (date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30))
    assert found[OWNER, "Migros"] == Holds(
        (Cash("CHF", D("700.00"), D("700.00")),),
        None,
        tuple(
            MonthEnd(end, D(v))
            for end, v in zip(ends, ("1000.00", "800.00", "700.00"), strict=True)
        ),
    )
    assert found[OWNER, "IBKR USD"] == Holds(
        (Cash("USD", D("4500.00"), D("3600.00")),),
        D("480.00"),
        tuple(
            MonthEnd(end, D(v))
            for end, v in zip(ends, ("4000.00", "4000.00", "4080.00"), strict=True)
        ),
    )
    assert found[OWNER, "IBKR EUR"].cash == (Cash("EUR", D("2000.00"), D("1800.00")),)
    assert found[OWNER, "IBKR EUR"].securities is None
    # The parts add up to the balance the page shows.
    for key, balance in networth.balances(conn, valued, "CHF").items():
        held = found[key]
        parts = sum((c.converted for c in held.cash), held.securities or D(0))
        assert (parts, held.months[-1].balance) == (balance, balance)
    in_dollars = holds.of(conn, valued, "USD")[OWNER, "IBKR USD"]
    assert in_dollars.cash == (Cash("USD", D("4500.00"), D("4500.00")),)
    assert in_dollars.securities == D("600.00")


def test_nothing_valued_holds_nothing_and_a_missing_rate_is_raised(
    conn: sqlite3.Connection,
) -> None:
    assert holds.of(conn, None, "CHF") == {}
    two_months(conn)
    with pytest.raises(MissingRateError):
        holds.of(conn, networth.value(conn, OWNER), "JPY")


def test_a_broker_lists_its_cash_by_currency_and_the_last_twelve_month_ends(
    conn: sqlite3.Connection,
) -> None:
    # An invented broker that keeps francs and dollars in one account, beside a share, over
    # fourteen months; it holds nothing in the first two.
    owner = store.ensure_owner(conn, OWNER)
    acme = store.ensure_account(
        conn, owner, name="ACME", kind="broker_cash", currency="CHF", source="ibkr"
    )
    rate(conn, "USD", date(2025, 8, 1), "0.8")
    first = date(2025, 8, 1)
    days = tuple(first + timedelta(days=n) for n in range((date(2026, 9, 30) - first).days + 1))
    late = date(2025, 10, 1)

    def flat(amount: str) -> list[Decimal]:
        return [D(0) if day < late else D(amount) for day in days]

    series = (
        networth.Series(OWNER, acme, CASH, "CHF", flat("100")),
        networth.Series(OWNER, acme, CASH, "USD", flat("1000")),
        networth.Series(OWNER, acme, CASH, "USD", flat("250")),
        networth.Series(OWNER, acme, ETFS, "USD", flat("500"), instrument_id=1),
        networth.Series(OWNER, acme, ETFS, "USD", flat("0"), instrument_id=2),  # sold
    )
    valued = networth.Valued(days, series, (), (), {OWNER: days[-1]})
    held = holds.of(conn, valued, "CHF")[OWNER, "ACME"]
    assert held.cash == (
        Cash("USD", D("1250.00"), D("1000.00")),
        Cash("CHF", D("100.00"), D("100.00")),
    )
    assert held.securities == D("400.00")
    assert len(held.months) == holds.MONTHS
    assert (held.months[0], held.months[-1]) == (
        MonthEnd(date(2025, 10, 31), D("1500.00")),
        MonthEnd(date(2026, 9, 30), D("1500.00")),
    )
    # From the first month-end it holds anything: two months fewer than kept.
    early = networth.Valued(days[:120], tuple(_cut(s, 120) for s in series), (), (), {})
    assert [m.day for m in holds.of(conn, early, "CHF")[OWNER, "ACME"].months] == [
        date(2025, 10, 31),
        date(2025, 11, 28),
    ]


def _cut(series: networth.Series, length: int) -> networth.Series:
    return networth.Series(
        series.owner,
        series.account_id,
        series.asset,
        series.currency,
        series.values[:length],
        series.instrument_id,
    )


def test_a_month_ends_on_its_last_day_or_the_last_day_there_is() -> None:
    days = [date(2026, 8, 30), date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2)]
    assert holds.ends(days) == [1, 3]
    assert holds.ends([]) == []
