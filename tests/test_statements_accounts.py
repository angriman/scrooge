"""Each account's statements and what it holds now (`months.by_account`, `networth.balances`)."""

import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest

from scrooge import months, networth, overview
from scrooge.storage import db
from tests.storage.helpers import OWNER, PARTNER
from tests.test_networth import NOW, account, close
from tests.test_overview import household

D = Decimal


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def test_what_each_account_holds_adds_up_to_net_worth(conn: sqlite3.Connection) -> None:
    household(conn)
    held = networth.balances(conn, networth.value(conn, None), "USD")
    assert held == {
        (OWNER, "Migros"): D("875.00"),
        (OWNER, "Swisscard"): D("-150.00"),
        (OWNER, "IBKR USD"): D("20022.50"),
        (PARTNER, "IBKR USD"): D("10000.00"),
        (PARTNER, "Revolut USD"): D("100.00"),
    }
    assert sum(held.values()) == overview.run(conn, currency="USD").total
    assert networth.balances(conn, None, "USD") == {}


def test_each_account_lists_its_statements_newest_first(conn: sqlite3.Connection) -> None:
    household(conn)
    october = date(2026, 10, 1)
    close(conn, account("migros", "Migros", "CHF", "700", month=october), month=october)
    listed = months.by_account(conn)
    assert [(s.first, s.last) for s in listed[OWNER, "Migros"]] == [
        (october, date(2026, 10, 31)),
        (date(2026, 9, 1), date(2026, 9, 30)),
    ]
    assert set(months.by_account(conn, PARTNER)) == {
        (PARTNER, "IBKR USD"),
        (PARTNER, "Revolut USD"),
    }
