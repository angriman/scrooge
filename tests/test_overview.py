"""The first page's figures: net worth on the last day, its parts, and the largest holdings."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal

import pytest

from finanze import networth, overview, portfolio, query
from finanze.models import Kind
from finanze.networth import BILLS, CARD, CASH, ETFS, STOCKS, MissingRateError
from finanze.overview import Change, Holding, Overview, Part, Rest
from finanze.storage import db
from tests.storage.helpers import OWNER, PARTNER
from tests.test_networth import NOW, account, bill, bought, close, day, ibkr, price, rate, spent
from tests.test_portfolio import family

D = Decimal
HUNDRED = D("100.00")


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def household(conn: sqlite3.Connection) -> None:
    """September 2026 for both; a dollar and a euro are 0.80 francs.

    Lukas has a bank account, a card he owes 120 francs on, and a broker with an ETF, a share
    and a bill; Anna holds the same ETF at two brokers.
    """
    debit = spent("-300", day(5), kind=Kind.INTERNAL, category="")
    payment = spent("300", day(6), account="Swisscard", kind=Kind.INTERNAL, category="")
    purchase = spent("-120", day(12), account="Swisscard")
    close(
        conn,
        account("migros", "Migros", "CHF", "1000", debit),
        account("swisscard", "Swisscard", "CHF", "-300", payment, purchase),
        ibkr(
            bought("WIDG", "10", "-500", day(15)),
            bought("GADG", "3", "-900", day(16), asset_tab="Stocks"),
            bill("10000", "-9850", day(15)),
            opening="20000",
        ),
    )
    close(
        conn,
        ibkr(bought("WIDG", "7", "-350", day(10))),
        account(
            "revolut",
            "Revolut USD",
            "USD",
            "100",
            bought("WIDG", "1", "-50", day(10), account="Revolut USD"),
        ),
        owner=PARTNER,
    )
    conn.execute("UPDATE instruments SET name = 'Widget Index Fund' WHERE symbol = 'WIDG'")
    price(conn, "WIDG", day(30), "50")
    price(conn, "GADG", day(30), "300")
    rate(conn, "USD", day(1), "0.8")
    rate(conn, "EUR", day(1), "0.8")


def test_nothing_committed_is_nothing_to_show(conn: sqlite3.Connection) -> None:
    shown = overview.run(conn, currency="CHF")
    assert shown == Overview(
        "CHF", None, D(0), (), (), (), (), (), None, Part("", D(0), None), shown.worth
    )
    assert shown.worth == networth.NetWorth("CHF", (), (), ())


def test_an_unknown_person_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(query.QueryError, match="Nobody"):
        overview.run(conn, owner="Nobody")


def test_a_currency_without_a_rate_is_refused(conn: sqlite3.Connection) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000"))
    with pytest.raises(MissingRateError, match="EUR"):
        overview.run(conn)


def test_everyone_s_net_worth_is_cut_by_kind_by_person_and_by_holding(
    conn: sqlite3.Connection,
) -> None:
    household(conn)
    shown = overview.run(conn, currency="USD")
    assert (shown.currency, shown.day, shown.total) == ("USD", day(30), D("30847.50"))
    # In the order the chart stacks them; each with what it was 30 days and a year before.
    assert shown.assets == (
        Part(CASH, D("19325.00"), D("62.65"), (D("31350.00"), None)),
        Part(BILLS, D("9872.50"), D("32.00"), (D("0.00"), None)),
        Part(ETFS, D("900.00"), D("2.92"), (D("0.00"), None)),
        Part(STOCKS, D("900.00"), D("2.92"), (D("0.00"), None)),
        Part(CARD, D("-150.00"), D("-0.49"), (D("-375.00"), None)),  # owed: a negative share
    )
    assert shown.people == (
        Part(PARTNER, D("10100.00"), D("32.74"), (D("10100.00"), None)),
        Part(OWNER, D("20747.50"), D("67.26"), (D("20875.00"), None)),
    )
    assert shown.top == (
        Holding("912797ZZ1", "", BILLS, D("9872.50"), D("32.00")),
        Holding("GADG", "", STOCKS, D("900.00"), D("2.92")),  # worth the same: by symbol
        # Lukas's ten and Anna's eight at two brokers: one row
        Holding("WIDG", "Widget Index Fund", ETFS, D("900.00"), D("2.92")),
    )
    assert shown.rest is None
    assert shown.securities == Part("Securities", D("11672.50"), D("37.84"))
    # The ring: what is held without the card, shares of its 30,997.50 that add up to 100.
    assert shown.held == (
        Part(CASH, D("19325.00"), D("62.35")),
        Part(BILLS, D("9872.50"), D("31.85")),
        Part(ETFS, D("900.00"), D("2.90")),
        Part(STOCKS, D("900.00"), D("2.90")),
    )


def test_net_worth_is_set_against_what_it_was_as_far_back_as_the_history_goes(
    conn: sqlite3.Connection,
) -> None:
    household(conn)
    month, year = overview.run(conn, currency="USD").changes
    # On 31 August: 1000 francs, 300 owed on the card, and 30,100 dollars at the brokers.
    assert month == Change(30, D("30975.00"), D("-127.50"), D("-0.41"))
    assert year == Change(365, None, None, None)


def test_one_person_is_not_cut_by_person(conn: sqlite3.Connection) -> None:
    household(conn)
    hers = overview.run(conn, owner=PARTNER, currency="CHF")
    assert hers.total == D("8080.00")
    assert hers.people == ()
    assert hers.assets == (
        Part(CASH, D("7760.00"), D("96.04"), (D("8080.00"), None)),
        Part(ETFS, D("320.00"), D("3.96"), (D("0.00"), None)),
    )
    assert hers.top == (Holding("WIDG", "Widget Index Fund", ETFS, D("320.00"), D("3.96")),)


def test_everyone_is_not_cut_by_person_when_they_are_one(conn: sqlite3.Connection) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000"))
    shown = overview.run(conn, currency="CHF")
    assert shown.people == ()
    assert shown.assets == (Part(CASH, D("1000.00"), HUNDRED, (D("1000.00"), None)),)
    assert (shown.top, shown.rest) == ((), None)
    assert shown.securities == Part("Securities", D(0), D("0.00"))


def test_the_holdings_beyond_the_largest_are_one_line(conn: sqlite3.Connection) -> None:
    household(conn)
    shown = overview.run(conn, currency="USD", top=1)
    assert [holding.symbol for holding in shown.top] == ["912797ZZ1"]
    assert shown.rest == Rest(2, D("1800.00"), D("5.84"))
    assert overview.run(conn, currency="USD", top=3).rest is None
    assert overview.TOP == 10


def test_what_is_worth_nothing_on_the_last_day_is_not_listed(conn: sqlite3.Connection) -> None:
    close(
        conn,
        ibkr(bought("WIDG", "10", "-500", day(15)), bought("WIDG", "-10", "520", day(20))),
    )
    shown = overview.run(conn, currency="USD")
    assert [(part.name, part.value) for part in shown.assets] == [(CASH, D("10020.00"))]
    assert shown.held == (Part(CASH, D("10020.00"), HUNDRED),)
    assert shown.top == ()


def test_a_net_worth_that_is_not_above_zero_has_no_shares_and_no_percent(
    conn: sqlite3.Connection,
) -> None:
    close(
        conn,
        account(
            "swisscard", "Swisscard", "CHF", "-300", spent("-120", day(12), account="Swisscard")
        ),
        ibkr(bought("WIDG", "2", "-100", day(15)), opening="100"),
    )
    rate(conn, "USD", day(1), "0.8")
    shown = overview.run(conn, currency="USD")
    assert shown.total == D("-425.00")
    assert shown.assets == (
        Part(ETFS, D("100.00"), None, (D("0.00"), None)),
        Part(CARD, D("-525.00"), None, (D("-375.00"), None)),
    )
    # What is held is above zero whatever is owed: it has its shares.
    assert shown.held == (Part(ETFS, D("100.00"), HUNDRED),)
    assert shown.top == (Holding("WIDG", "", ETFS, D("100.00"), None),)
    assert shown.securities == Part("Securities", D("100.00"), None)
    assert overview.run(conn, currency="USD", top=0).rest == Rest(1, D("100.00"), None)
    # 31 August: 300 francs owed and 100 dollars, less than nothing to take a percent of.
    assert shown.changes[0] == Change(30, D("-275.00"), D("-150.00"), None)


@pytest.mark.parametrize("owner", [None, OWNER, PARTNER])
@pytest.mark.parametrize("currency", ["EUR", "CHF", "USD"])
def test_the_figures_are_those_of_net_worth_and_of_the_holdings_page(
    conn: sqlite3.Connection, owner: str | None, currency: str
) -> None:
    family(conn)
    shown = overview.run(conn, owner=owner, currency=currency, top=1)
    worth = networth.run(conn, owner=owner, currency=currency)
    held = portfolio.run(conn, owner=owner, currency=currency)
    assert (shown.day, shown.total) == (worth.days[-1], worth.total[-1])
    assert sum(part.value for part in shown.assets) == shown.total
    assert sum(part.share or 0 for part in shown.assets) == HUNDRED
    assert [part.name for part in shown.assets] == [
        asset for asset in networth.STACK if asset in worth.by("asset")
    ]
    assert shown.held == tuple(
        replace(part, share=held_share, then=())
        for part, held_share in zip(
            [part for part in shown.assets if part.value > 0],
            [part.share for part in shown.held],
            strict=True,
        )
    )
    assert sum(part.share or 0 for part in shown.held) == HUNDRED
    assert shown.worth == worth
    if owner is None:
        assert sum(part.value for part in shown.people) == shown.total
        assert sum(part.share or 0 for part in shown.people) == HUNDRED
    rest = shown.rest or Rest(0, D(0), D(0))
    assert (shown.rest is None) == (owner == PARTNER)  # she holds one instrument only
    listed = sum(holding.value for holding in shown.top) + rest.value
    assert listed == shown.securities.value == held.total
    shares = sum(holding.share or 0 for holding in shown.top) + (rest.share or 0)
    assert shares == shown.securities.share
    assert len(shown.top) + rest.count == len({p.symbol for p in held.positions})
