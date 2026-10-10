"""What the positions cost (first in, first out), the ladder of bills and the totals."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finanze import monthclose, networth, portfolio
from finanze.models import Holding, Kind, SecurityTrade
from finanze.networth import BILLS, ETFS, PENSION, STOCKS, LotTrade
from finanze.portfolio import Portfolio, Position
from finanze.storage import db, holdings, store
from finanze.storage.bills import Event, fifo_costs, open_lots
from finanze.storage.money import from_nano
from tests.storage.helpers import OCTOBER, OWNER, PARTNER, SEPTEMBER
from tests.test_networth import NOW, account, bill, bought, close, day, ibkr, price, rate
from tests.test_portfolio import agrees_with_net_worth, family

D = Decimal


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def moved(ticker: str, quantity: str, worth: str, on: date, **fields: object) -> SecurityTrade:
    """Shares moved in kind, as the broker's extractor books them: no cash, worth `worth`."""
    row = bought(ticker, quantity, "0", on, kind=Kind.TRANSFER, category="Giroconto", **fields)
    return replace(row, cost=D(worth), price=abs(D(worth) / D(quantity)))


def costs(held: Portfolio) -> dict[tuple[str, str], tuple[Decimal, Decimal | None]]:
    return {(p.account, p.symbol): (p.quantity, p.cost_native) for p in held.positions}


# ---------------------------------------------------------------------------------- cost


def test_the_cost_is_what_was_paid_for_the_lots_still_held_oldest_sold_first(
    conn: sqlite3.Connection,
) -> None:
    rows = [
        bought("WIDG", "10", "-500", day(15)),
        bought("WIDG", "10", "-600", day(16)),
        bought("WIDG", "-15", "900", day(20)),
    ]
    close(conn, ibkr(*rows))
    price(conn, "WIDG", day(30), "61.5")
    [position] = agrees_with_net_worth(conn, None, "USD").positions
    # all of the first lot is sold and half of the second
    assert (position.quantity, position.cost_native) == (D(5), D("300.00"))
    assert (position.value_native, position.gain_native) == (D("307.50"), D("7.50"))


def test_the_cost_stays_in_the_currency_of_the_instrument(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15))))
    price(conn, "WIDG", day(30), "53.5")
    rate(conn, "USD", day(1), "0.8")
    [position] = agrees_with_net_worth(conn, None, "CHF").positions
    assert (position.value, position.value_native) == (D(428), D(535))
    assert (position.cost_native, position.gain_native) == (D(500), D(35))


def test_a_loss_is_a_gain_below_zero(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bought("WIDG", "3", "-100", day(15))))
    price(conn, "WIDG", day(30), "30.111")
    [position] = portfolio.run(conn, currency="USD").positions
    assert (position.cost_native, position.gain_native) == (D("100.00"), D("-9.67"))


def test_a_split_changes_the_quantity_of_a_lot_and_not_its_cost(conn: sqlite3.Connection) -> None:
    rows = [
        bought("WIDG", "10", "-2000", day(3), asset_tab="Stocks"),
        bought("WIDG", "4", "-208", day(10), asset_tab="Stocks"),  # the day of the split: new
        bought("WIDG", "-10", "510", day(12), asset_tab="Stocks"),  # a quarter of the first lot
    ]
    close(conn, ibkr(*rows))
    price(conn, "WIDG", day(30), "50.5")
    instrument = conn.execute("SELECT id FROM instruments").fetchone()[0]
    store.add_split(conn, instrument, day(10), 4, 1)
    [position] = agrees_with_net_worth(conn, None, "USD").positions
    assert (position.quantity, position.cost_native) == (D(34), D("1708.00"))
    assert position.gain_native == D(34) * D("50.5") - D(1708)


def test_three_shares_for_two_need_no_division(conn: sqlite3.Connection) -> None:
    rows = [bought("WIDG", "10", "-900", day(3)), bought("WIDG", "-5", "310", day(12))]
    close(conn, ibkr(*rows))
    price(conn, "WIDG", day(30), "62")
    instrument = conn.execute("SELECT id FROM instruments").fetchone()[0]
    store.add_split(conn, instrument, day(10), 3, 2)
    [position] = agrees_with_net_worth(conn, None, "USD").positions
    assert (position.quantity, position.cost_native) == (D(10), D("600.00"))


def test_a_split_that_leaves_no_exact_quantity_leaves_no_cost(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-900", day(3))))
    price(conn, "WIDG", day(30), "270")
    instrument = conn.execute("SELECT id FROM instruments").fetchone()[0]
    store.add_split(conn, instrument, day(10), 1, 3)
    [position] = agrees_with_net_worth(conn, None, "USD").positions
    assert position.quantity * 3 != 10  # three and a third: no decimal says it
    assert (position.cost_native, position.gain_native) == (None, None)


def test_only_what_happened_up_to_the_day_shown_makes_the_cost(conn: sqlite3.Connection) -> None:
    family(conn)
    october = ibkr(bought("WIDG", "5", "-300", day(5, 10)), opening="8150", month=OCTOBER)
    close(conn, account("migros", "Migros", "CHF", "1000", month=OCTOBER), october, month=OCTOBER)
    instrument = conn.execute("SELECT id FROM instruments WHERE symbol = 'WIDG'").fetchone()[0]
    store.add_split(conn, instrument, day(3, 10), 2, 1)
    mine = agrees_with_net_worth(conn, OWNER, "USD")
    assert costs(mine)["IBKR USD", "WIDG"] == (D(25), D("800.00"))
    # everyone is shown on 30 September: before the split, and before the purchase of October
    everyone = agrees_with_net_worth(conn, None, "USD")
    assert [(p.quantity, p.cost_native) for p in everyone.positions if p.symbol == "WIDG"] == [
        (D(7), D("350.00")),
        (D(1), D("50.00")),
        (D(10), D("500.00")),
    ]


def test_shares_held_before_the_history_starts_have_no_known_cost(
    conn: sqlite3.Connection,
) -> None:
    base = ibkr(bought("WIDG", "4", "-200", day(15)))
    printed = (Holding("IBKR USD", "WIDG", "USD", D(10), D(55), D(550), "ETFs"),)
    close(conn, replace(base, extraction=replace(base.extraction, holdings=printed)))
    [position] = agrees_with_net_worth(conn, None, "USD").positions
    # six were there already: what they cost is nowhere
    assert (position.quantity, position.value_native) == (D(10), D(550))
    assert (position.cost_native, position.gain_native) == (None, None)


def test_a_lot_that_came_in_without_a_price_has_no_known_cost_while_it_is_held(
    conn: sqlite3.Connection,
) -> None:
    rows = [moved("WIDG", "10", "510", day(15)), bought("WIDG", "5", "-260", day(16))]
    close(conn, ibkr(*rows))
    conn.execute("UPDATE trades SET price = NULL WHERE quantity = '10'")
    price(conn, "WIDG", day(30), "53")
    [position] = agrees_with_net_worth(conn, None, "USD").positions
    assert (position.quantity, position.cost_native, position.gain_native) == (D(15), None, None)
    # once those shares are sold, the oldest first, what is left was all bought
    monthclose.reopen(conn, owner=OWNER, month=SEPTEMBER)
    close(conn, ibkr(*rows, bought("WIDG", "-10", "530", day(20))))
    conn.execute("UPDATE trades SET price = NULL WHERE quantity = '10'")
    [position] = agrees_with_net_worth(conn, None, "USD").positions
    assert (position.quantity, position.cost_native) == (D(5), D("260.00"))


def test_shares_moved_in_kind_cost_what_they_were_worth_the_day_they_came(
    conn: sqlite3.Connection,
) -> None:
    close(
        conn,
        ibkr(bought("WIDG", "10", "-500", day(15)), moved("WIDG", "-4", "-220", day(20))),
        account(
            "revolut",
            "Revolut USD",
            "USD",
            "100",
            moved("WIDG", "4", "220", day(20), account="Revolut USD"),
        ),
    )
    price(conn, "WIDG", day(30), "56")
    held = agrees_with_net_worth(conn, None, "USD")
    assert costs(held) == {
        ("IBKR USD", "WIDG"): (D(6), D("300.00")),  # the four that left took their cost along
        # nothing links the two rows: not the 200 once paid, but what the row that brought
        # them says they were worth
        ("Revolut USD", "WIDG"): (D(4), D("220.00")),
    }
    conn.execute("UPDATE trades SET price = NULL WHERE cost_nano = 220000000000")
    held = agrees_with_net_worth(conn, None, "USD")
    assert costs(held) == {
        ("IBKR USD", "WIDG"): (D(6), D("300.00")),
        ("Revolut USD", "WIDG"): (D(4), None),
    }


def test_a_bill_costs_what_its_lots_still_held_cost(conn: sqlite3.Connection) -> None:
    rows = [
        bill("10000", "-9850", day(15)),
        bill("10000", "-9900", day(20)),
        bill("-15000", "14800", day(25)),
    ]
    close(conn, ibkr(*rows, opening="30000"))
    [position] = agrees_with_net_worth(conn, None, "USD").positions
    assert (position.quantity, position.cost_native) == (D(5000), D("4950.00"))
    assert position.gain_native == position.value_native - D(4950)


def test_fund_units_of_a_pension_account_have_a_cost_too(conn: sqlite3.Connection) -> None:
    unit = bought(
        "CH0000000001", "2", "-300", day(15), asset_tab="Funds", account="Pillar 3a", currency="CHF"
    )
    close(conn, account("finpension", "Pillar 3a", "CHF", "1000", unit))
    price(conn, "CH0000000001", day(15), "155")
    [position] = portfolio.run(conn, currency="CHF").positions
    assert (position.cost_native, position.gain_native) == (D(300), D(10))


STEPS = st.lists(
    st.tuples(
        st.sampled_from([None, (2, 1), (4, 1), (3, 2), (1, 2)]),  # a split that morning
        st.integers(1, 40),  # bought
        st.integers(0, 10_000_000_000_000),  # paid for it
        st.integers(0, 4),  # quarters of what is held that are sold
    ),
    min_size=1,
    max_size=8,
)


@given(STEPS)
def test_lots_follow_the_quantity_held_through_splits_and_no_cost_is_lost(
    steps: list[tuple[tuple[int, int] | None, int, int, int]],
) -> None:
    trades: list[LotTrade] = []
    splits: list[tuple[date, int, int]] = []
    changes: list[holdings.Change] = []
    held, paid = D(0), 0
    for n, (split, quantity, cost, quarters) in enumerate(steps):
        on = day(1) + timedelta(days=n)
        if split is not None:
            splits.append((on, *split))
            changes.append(holdings.Change(on, holdings._SPLIT, D(split[0]), D(split[1])))
            held = held * split[0] / split[1]
        sold = (held + quantity) * quarters / 4
        trades.append(LotTrade(on, Event(f"buy{n}", D(quantity), cost), True))
        trades.append(LotTrade(on, Event(f"sale{n}", -sold), True))
        changes += [holdings.Change(on, holdings._TRADE, D(quantity) - sold)]
        held += quantity - sold
        paid += cost
    last = day(1) + timedelta(days=len(steps) - 1)
    assert holdings.timeline(changes)[1][-1].quantity == held
    later = LotTrade(last + timedelta(days=1), Event("later", D(7), 7), True)
    events, per_share = portfolio._lot_events([*trades, later], splits, last)
    lots = open_lots(events)
    assert sum((left for _, left, _ in lots), D(0)) == held * per_share
    handed_out = fifo_costs(events)
    assert None not in handed_out.values()
    left = sum(cost for _, _, cost in lots)
    assert sum(cost or 0 for cost in handed_out.values()) + left == paid
    assert portfolio._cost([*trades, later], splits, held, last) == from_nano(left).quantize(
        D("0.01")
    )
    assert portfolio._cost(trades, splits, held + 1, last) is None


# -------------------------------------------------------------------------------- ladder


def test_the_ladder_is_the_bills_held_by_maturity(conn: sqlite3.Connection) -> None:
    def other(cusip: str, on: date, maturity: date) -> SecurityTrade:
        return bought(cusip, "5000", "-4950", on, asset_tab="Bonds", price=D(99), maturity=maturity)

    mine = [
        bill("10000", "-9850", day(15)),  # matures on 24 December
        other("912797XX3", day(16), date(2026, 11, 5)),
        other("912797WW4", day(16), date(2026, 9, 28)),  # due, repaid in October
        other("912797VV5", day(17), date(2027, 1, 7)),
        bought("WIDG", "10", "-500", day(15)),
    ]
    close(conn, ibkr(*mine, opening="50000"))
    close(conn, ibkr(other("912797AA9", day(16), date(2026, 11, 5))), owner=PARTNER)
    conn.execute("UPDATE instruments SET maturity = NULL WHERE symbol = '912797VV5'")
    held = agrees_with_net_worth(conn, None, "USD")
    ladder = held.ladder
    assert [(r.position.symbol, r.position.owner, r.position.maturity, r.days) for r in ladder] == [
        ("912797WW4", OWNER, date(2026, 9, 28), -2),
        ("912797AA9", PARTNER, date(2026, 11, 5), 36),
        ("912797XX3", OWNER, date(2026, 11, 5), 36),
        ("912797ZZ1", OWNER, date(2026, 12, 24), 85),
        ("912797VV5", OWNER, None, None),
    ]
    assert {rung.position for rung in ladder} == {p for p in held.positions if p.asset == BILLS}
    due = ladder[0].position
    assert (due.account, due.currency, due.quantity) == ("IBKR USD", "USD", D(5000))
    assert (due.cost_native, due.value_native, due.value) == (D(4950), D(5000), D(5000))
    assert portfolio.run(conn, owner=PARTNER, currency="USD").ladder == ladder[1:2]


def test_without_bills_there_is_no_ladder(conn: sqlite3.Connection) -> None:
    assert portfolio.run(conn).ladder == ()
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15))))
    assert portfolio.run(conn, currency="USD").ladder == ()


# -------------------------------------------------------------------------------- totals


def test_the_positions_add_up_by_kind_and_overall(conn: sqlite3.Connection) -> None:
    family(conn)
    held = agrees_with_net_worth(conn, None, "USD")
    assert held.by_asset == {ETFS: D(1400), STOCKS: D("1305.30"), BILLS: D("9872.50")}
    assert list(held.by_asset) == [ETFS, STOCKS, BILLS]
    assert held.total == D("12577.80")
    worth = networth.run(conn, currency="USD").by("asset")
    assert all(worth[asset][-1] == value for asset, value in held.by_asset.items())
    shares = held.shares
    assert list(shares) == list(held.positions)
    assert [shares[p] for p in held.positions] == [
        D("2.78"),
        D("0.40"),
        D("3.98"),  # 3.975: the two of 500 lost as much, the first of them gets the hundredth
        D("3.97"),
        D("7.19"),
        D("3.19"),
        D("78.49"),
    ]
    assert sum(shares.values()) == 100


def test_of_a_pension_account_only_the_fund_units_are_added_up(conn: sqlite3.Connection) -> None:
    unit = bought(
        "CH0000000001", "2", "-300", day(15), asset_tab="Funds", account="Pillar 3a", currency="CHF"
    )
    close(conn, account("finpension", "Pillar 3a", "CHF", "1000", unit))
    price(conn, "CH0000000001", day(15), "155")
    held = portfolio.run(conn, currency="CHF")
    assert (held.by_asset, held.total) == ({PENSION: D(310)}, D(310))  # not the 700 of cash
    assert list(held.shares.values()) == [D(100)]


def test_nothing_held_adds_up_to_nothing(conn: sqlite3.Connection) -> None:
    held = portfolio.run(conn)
    assert (held.total, held.by_asset, held.shares) == (D(0), {}, {})


def test_positions_that_cancel_out_have_no_share(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15)), bought("GADG", "-10", "500", day(15))))
    held = portfolio.run(conn, currency="USD")
    assert [p.value for p in held.positions] == [D(500), D(-500)]
    assert (held.total, set(held.shares.values())) == (D(0), {D(0)})


@given(st.lists(st.decimals("0.01", 100_000, places=2), min_size=1, max_size=8))
def test_shares_add_up_to_a_hundred(values: list[Decimal]) -> None:
    template = Position(
        OWNER, "IBKR USD", "", "", "etf", ETFS, "USD", D(1), None, None, False, None, D(0), D(0)
    )
    positions = tuple(
        replace(template, symbol=str(n), value=value) for n, value in enumerate(values)
    )
    shares = Portfolio("USD", day(30), positions, ()).shares
    assert sum(shares.values()) == 100
    for position, share in shares.items():
        assert abs(share - position.value * 100 / sum(values)) < D("0.01")
