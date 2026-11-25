"""What each holding returned: over a range and since bought, realised, unrealised and paid."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal

import pytest

from scrooge import holding_returns, networth, portfolio
from scrooge.holding_returns import Period, Returns
from scrooge.models import Holding, Kind
from scrooge.performance import instrument_ranges
from scrooge.storage import db, store
from tests.storage.helpers import OWNER, PARTNER
from tests.test_networth import NOW, bought, close, day, ibkr, price, rate, spent

D = Decimal
USD = {"account": "IBKR USD", "currency": "USD"}


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def returned(conn: sqlite3.Connection, currency: str = "USD") -> holding_returns.Found:
    valued = networth.value(conn, None)
    held = portfolio.positions_of(conn, valued, currency)
    return holding_returns.run(conn, valued, held)


def only(found: holding_returns.Found, symbol: str = "WIDG") -> Returns:
    [returns] = [r for (name, _, _), r in found.holdings.items() if name == symbol]
    return returns


def test_a_holding_bought_this_month_has_a_return_since_bought_only(
    conn: sqlite3.Connection,
) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(10))))
    price(conn, "WIDG", day(10), "50")
    price(conn, "WIDG", day(20), "55")
    returns = only(returned(conn))
    # 500 paid at the start of the 10th, worth 550 from the 20th.
    assert returns.periods == {
        "1m": None,
        "ytd": None,
        "1y": None,
        "all": Period(day(10), D("10.00"), D("50.00")),
    }
    assert (returns.realised, returns.unrealised) == (D(0), D("50.00"))
    assert (returns.dividends, returns.tax) == (D(0), D(0))


def test_a_partial_sale_and_a_dividend_are_taken_out_of_the_holding(
    conn: sqlite3.Connection,
) -> None:
    rows = (
        bought("WIDG", "10", "-1000", day(10)),
        bought("WIDG", "-4", "480", day(20)),
        spent("12", day(25), kind=Kind.DIVIDEND, category="Dividendi", counterparty="WIDG", **USD),
        spent("-2", day(25), kind=Kind.TAX, category="Ritenute", counterparty="WIDG", **USD),
        # Another holding's dividend is not this one's.
        spent("7", day(26), kind=Kind.DIVIDEND, category="Dividendi", counterparty="ACME", **USD),
    )
    close(conn, ibkr(*rows))
    for on, close_ in (
        (day(10), "100"),
        (day(20), "120"),
        (day(1, 10), "125"),
        (day(31, 10), "130"),
    ):
        price(conn, "WIDG", on, close_)
    returns = only(returned(conn))
    # 1000 in on the 10th; the 20th returns (720 + 480) / 1000; the 25th takes 10 out of 720;
    # 750 on 1 October, 780 on the 31st: 1.2 x 730/720 x 750/720 x 1.04 = 1.318055...
    since = returns.periods["all"]
    assert since == Period(day(10), D("31.81"), D("270.00"))
    # The past month, from the end of 1 October: 750 to 780.
    assert returns.periods["1m"] == Period(day(2, 10), D("4.00"), D("30.00"))
    assert returns.periods["ytd"] is None
    # 480 for 4 shares that cost 400; 6 at 130 that cost 600; 12 paid, 2 withheld: 270.
    assert (returns.realised, returns.unrealised) == (D("80.00"), D("180.00"))
    assert (returns.dividends, returns.tax) == (D("12.00"), D("-2.00"))


def test_a_split_changes_the_lots_and_not_the_return(conn: sqlite3.Connection) -> None:
    close(
        conn,
        ibkr(bought("WIDG", "10", "-2000", day(3)), bought("WIDG", "-20", "1100", day(20))),
    )
    price(conn, "WIDG", day(9), "200")
    price(conn, "WIDG", day(10), "50.5")
    price(conn, "WIDG", day(20), "55")
    instrument = conn.execute("SELECT id FROM instruments").fetchone()[0]
    store.add_split(conn, instrument, day(10), 4, 1)
    returns = only(returned(conn))
    # 2000 to 2020 on the day of the split, then (1100 + 1100) / 2020: 10% in all.
    assert returns.periods["all"] == Period(day(3), D("10.00"), D("200.00"))
    # 20 of the 40 shares the 10 became, for 1100: half the lots, 1000.
    assert (returns.realised, returns.unrealised) == (D("100.00"), D("100.00"))


def test_a_sale_realises_at_its_own_rate_and_the_lots_at_theirs(
    conn: sqlite3.Connection,
) -> None:
    close(
        conn,
        ibkr(bought("WIDG", "10", "-1000", day(10)), bought("WIDG", "-5", "500", day(20))),
    )
    price(conn, "WIDG", day(10), "100")
    rate(conn, "USD", day(10), "0.9")
    rate(conn, "USD", day(20), "0.8")
    returns = only(returned(conn, "CHF"))
    # 400 francs for shares that cost 450.
    assert returns.realised == D("-50.00")
    # Through a rate that divides into no exact decimal: 400 / 0.7 - 450 / 0.7.
    rate(conn, "EUR", day(1), "0.7")
    assert only(returned(conn, "EUR")).realised == D("-71.43")


def test_a_holding_in_two_accounts_is_measured_together_and_each_on_its_own(
    conn: sqlite3.Connection,
) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-1000", day(10))))
    close(conn, ibkr(bought("WIDG", "10", "-1100", day(15))), owner=PARTNER)
    price(conn, "WIDG", day(10), "100")
    price(conn, "WIDG", day(15), "110")
    price(conn, "WIDG", day(30), "121")
    found = returned(conn)
    by_owner = {p.owner: r.periods["all"] for p, r in found.positions.items()}
    assert by_owner == {
        OWNER: Period(day(10), D("21.00"), D("210.00")),
        PARTNER: Period(day(15), D("10.00"), D("110.00")),
    }
    # 1000 to 1100, then 2200 on 2100 put in, then 10%: 1.1/1.05 x 1.1.
    assert only(found).periods["all"] == Period(day(10), D("15.24"), D("320.00"))


def test_shares_held_when_the_history_starts_count_from_then_and_realise_nothing_known(
    conn: sqlite3.Connection,
) -> None:
    base = ibkr(bought("WIDG", "-4", "240", day(20)))
    # The statement prints the 6 left at its end, at a close of 60.
    held = (Holding("IBKR USD", "WIDG", "USD", D(6), D(60), D(360), "ETFs"),)
    close(conn, replace(base, extraction=replace(base.extraction, holdings=held)))
    price(conn, "WIDG", day(1), "50")
    price(conn, "WIDG", day(20), "60")
    returns = only(returned(conn))
    # The 10 shares come in at the close of the 1st, 500, and earn from the 2nd; 4 sold for
    # 240 on the 20th and 6 left at 60: (360 + 240) / 500.
    assert returns.periods["all"] == Period(day(2), D("20.00"), D("100.00"))
    assert (returns.realised, returns.unrealised) == (None, None)


def test_shares_moved_in_kind_come_in_at_the_close_of_their_day(
    conn: sqlite3.Connection,
) -> None:
    moved = bought("WIDG", "10", "0", day(15), kind=Kind.TRANSFER, category="Giroconto")
    close(conn, ibkr(moved))
    price(conn, "WIDG", day(15), "52")
    price(conn, "WIDG", day(20), "57.2")
    returns = only(returned(conn))
    # They came at the end of the 15th, worth 520: the return counts from the 16th.
    assert returns.periods["all"] == Period(day(16), D("10.00"), D("52.00"))


def test_nothing_held_has_no_returns(conn: sqlite3.Connection) -> None:
    assert returned(conn) == holding_returns.Found()
    series = networth.Series(OWNER, 1, "ETFs", "USD", [D(0), D(0)], instrument_id=1)
    found = instrument_ranges(
        [series], [], [day(1), day(2)], ("1m", "all"), currency="USD", rates={}
    )
    assert found == {"1m": None, "all": None}
