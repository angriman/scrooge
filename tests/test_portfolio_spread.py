"""What is held, cut by kind, currency, account and person (`portfolio.Portfolio.spread`)."""

import sqlite3
from collections.abc import Iterator
from decimal import Decimal

import pytest

from scrooge import portfolio
from scrooge.networth import BILLS, ETFS, STOCKS
from scrooge.portfolio import Slice
from scrooge.storage import db
from tests.storage.helpers import OWNER, PARTNER
from tests.test_networth import NOW
from tests.test_overview import household

D = Decimal


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def test_what_is_held_is_spread_by_kind_currency_account_and_person(
    conn: sqlite3.Connection,
) -> None:
    household(conn)
    held = portfolio.run(conn, currency="USD")
    # By kind in the order of net worth, the rest largest first; the shares add up to 100.
    assert held.spread("asset") == (
        Slice(ETFS, D("900.00"), D("7.71")),
        Slice(STOCKS, D("900.00"), D("7.71")),
        Slice(BILLS, D("9872.50"), D("84.58")),
    )
    assert held.spread("currency") == (Slice("USD", D("11672.50"), D("100.00")),)
    # The same account name of two people is two accounts, each named with its owner's.
    assert [s.name for s in held.spread("account")] == [
        "IBKR USD (Lukas)",
        "IBKR USD (Anna)",
        "Revolut USD (Anna)",
    ]
    assert held.spread("owner") == (
        Slice(OWNER, D("11272.50"), D("96.57")),
        Slice(PARTNER, D("400.00"), D("3.43")),
    )
    for by in portfolio.SPREADS:
        assert sum(s.share for s in held.spread(by)) == 100
    # One person's accounts go without their name.
    hers = portfolio.run(conn, owner=PARTNER, currency="USD").spread("account")
    assert [s.name for s in hers] == ["IBKR USD", "Revolut USD"]


def test_nothing_held_is_spread_over_nothing(conn: sqlite3.Connection) -> None:
    assert portfolio.run(conn, currency="USD").spread("currency") == ()
