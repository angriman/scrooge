"""What the positions cost (first in, first out), the ladder of bills and the totals."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finanze import networth, portfolio
from finanze.models import Holding, Kind, SecurityTrade
from finanze.networth import BILLS, ETFS, PENSION, STOCKS, LotTrade
from finanze.portfolio import Portfolio, Position
from finanze.storage import db, holdings, store
from finanze.storage.bills import Event, fifo_costs, open_lots
from finanze.storage.money import from_nano
from tests.storage.helpers import OCTOBER, OWNER, PARTNER, take_back
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


def test_in_the_currency_shown_each_lot_cost_what_it_did_at_the_rate_of_its_day(
    conn: sqlite3.Connection,
) -> None:
    """A gain in francs is the change of the price and of the dollar since each purchase."""
    rows = [
        bought("WIDG", "10", "-500", day(5)),
        bought("WIDG", "10", "-600", day(16)),
        bought("WIDG", "-15", "900", day(20)),  # all of the first lot and half of the second
    ]
    close(conn, ibkr(*rows))
    price(conn, "WIDG", day(30), "61.5")
    rate(conn, "USD", day(1), "0.7")
    rate(conn, "USD", day(10), "0.75")
    rate(conn, "USD", day(29), "0.8")
    [position] = agrees_with_net_worth(conn, None, "CHF").positions
    # 300 dollars of the lot of the 16th, at 0.75 then; 307.50 dollars at 0.8 now
    assert (position.cost_native, position.cost) == (D("300.00"), D("225.00"))
    assert (position.value, position.gain) == (D("246.00"), D("21.00"))
    assert position.gain_native == D("7.50")
    assert position.return_percent == D("9.33")  # 2.50% of it is the price, the rest the dollar
    [in_dollars] = portfolio.run(conn, currency="USD").positions
    assert (in_dollars.cost, in_dollars.gain) == (D("300.00"), D("7.50"))


def test_a_cost_not_known_is_not_known_in_any_currency(conn: sqlite3.Connection) -> None:
    base = ibkr(bought("WIDG", "4", "-200", day(15)))
    printed = (Holding("IBKR USD", "WIDG", "USD", D(10), D(55), D(550), "ETFs"),)
    close(conn, replace(base, extraction=replace(base.extraction, holdings=printed)))
    rate(conn, "USD", day(1), "0.8")
    [position] = portfolio.run(conn, currency="CHF").positions
    assert (position.cost, position.gain, position.return_percent) == (None, None, None)


def test_the_gain_of_everything_held_is_that_of_what_has_a_known_cost(
    conn: sqlite3.Connection,
) -> None:
    rows = [bought("WIDG", "10", "-500", day(15)), bought("GADG", "3", "-900", day(16))]
    base = ibkr(*rows)
    printed = (
        Holding("IBKR USD", "WIDG", "USD", D(10), D("53.5"), D(535), "ETFs"),
        Holding("IBKR USD", "GADG", "USD", D(3), D(290), D(870), "Stocks"),
        Holding("IBKR USD", "OLDS", "USD", D(2), D(50), D(100), "Stocks"),  # bought before
    )
    close(conn, replace(base, extraction=replace(base.extraction, holdings=printed)))
    for symbol, close_ in (("WIDG", "53.5"), ("GADG", "290"), ("OLDS", "50")):
        price(conn, symbol, day(30), close_)
    held = portfolio.run(conn, currency="USD")
    assert (held.cost, held.gain, held.unknown_cost) == (D(1400), D(5), 1)
    assert held.return_percent == D("0.36")
    assert portfolio.Portfolio("USD", None, (), ()).return_percent is None


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
    # her statements end on 30 September: the split doubles what she held then, and her
    # cost stays what it was
    everyone = agrees_with_net_worth(conn, None, "USD")
    assert [(p.quantity, p.cost_native) for p in everyone.positions if p.symbol == "WIDG"] == [
        (D(14), D("350.00")),
        (D(2), D("50.00")),
        (D(25), D("800.00")),
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
    take_back(conn, OWNER)
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
    found = portfolio._lots([*trades, later], splits, held, last)
    assert found is not None
    assert sum((cost for _, cost in found), D(0)) == from_nano(left)
    assert all(bought <= last for bought, _ in found)
    assert portfolio._lots(trades, splits, held + 1, last) is None


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
    # what is earned when each is repaid: its face less what it cost
    assert [rung.interest for rung in ladder] == [D(50), D(50), D(50), D(150), D(50)]


def test_the_ladder_is_valued_on_the_day_asked_for_at_the_last_rate_by_then(
    conn: sqlite3.Connection,
) -> None:
    close(
        conn,
        ibkr(
            bill("10000", "-9850", day(15)),  # 100 days to 24 December: 1.5 a day
            # 20 days to 6 October: 2.5 a day, repaid before the day asked for
            bought(
                "912797WW4",
                "5000",
                "-4950",
                day(16),
                asset_tab="Bonds",
                price=D(99),
                maturity=day(6, 10),
            ),
            opening="50000",
        ),
    )
    rate(conn, "USD", day(1), "0.8")
    rate(conn, "USD", day(20, 10), "0.9")  # after the last day committed, before the one asked
    held = portfolio.run(conn, currency="CHF", today=day(21, 10))
    assert (held.through, held.day, held.ladder_day) == (day(30), day(20, 10), day(21, 10))
    due, later = held.ladder
    assert (due.position.symbol, due.days, due.value_native, due.value) == (
        "912797WW4",
        -15,
        D(5000),
        D(4500),
    )
    # 36 days after 15 September; at the rate of 20 October, not that of the last day
    assert (later.days, later.value_native, later.value) == (64, D(9904), D("8913.60"))
    assert later.position.value_native == D("9902.50")  # the positions are of the last day
    assert later.interest == D(150)


def test_the_ladder_is_never_valued_before_the_last_day_committed(
    conn: sqlite3.Connection,
) -> None:
    close(conn, ibkr(bill("10000", "-9850", day(15)), opening="50000"))
    for today in (None, day(20), day(30)):
        held = portfolio.run(conn, currency="USD", today=today)
        [rung] = held.ladder
        assert (held.ladder_day, rung.days, rung.value_native, rung.value) == (
            day(30),
            85,
            D("9872.50"),
            D("9872.50"),
        )


def test_the_interest_of_a_bill_whose_cost_is_not_known_is_not_known_either(
    conn: sqlite3.Connection,
) -> None:
    close(conn, ibkr(bill("10000", "-9850", day(15)), opening="50000"))
    [rung] = agrees_with_net_worth(conn, None, "USD").ladder
    assert rung.interest == D(150)
    unknown = replace(rung, position=replace(rung.position, cost_native=None))
    assert unknown.interest is None


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


# ---------------------------------------------------------------------------------- page


def test_the_kinds_of_asset_group_everyone_s_positions_largest_first(
    conn: sqlite3.Connection,
) -> None:
    family(conn)
    held = agrees_with_net_worth(conn, None, "USD")
    groups = held.groups
    assert [(group.asset, group.value, group.share) for group in groups] == [
        (ETFS, D(1400), D("11.13")),
        (STOCKS, D("1305.30"), D("10.38")),
        (BILLS, D("9872.50"), D("78.49")),
    ]
    assert {group.asset: group.value for group in groups} == held.by_asset
    assert sum(group.share for group in groups) == 100
    assert [(p.owner, p.symbol, p.account) for p in groups[0].positions] == [
        (OWNER, "BOLT", "IBKR USD"),  # worth the same: by symbol
        (OWNER, "WIDG", "IBKR USD"),
        (PARTNER, "WIDG", "IBKR USD"),
        (PARTNER, "WIDG", "Revolut USD"),
    ]
    assert {p for group in groups for p in group.positions} == set(held.positions)
    assert Portfolio("USD", None, (), ()).groups == ()


def test_an_instrument_is_one_holding_whoever_holds_it_and_wherever(
    conn: sqlite3.Connection,
) -> None:
    family(conn)
    held = agrees_with_net_worth(conn, None, "USD")
    holdings = held.holdings
    assert [(h.symbol, h.asset, h.positions, h.value) for h in holdings] == [
        ("912797ZZ1", BILLS, 1, D("9872.50")),
        ("GADG", STOCKS, 1, D("903.90")),
        ("WIDG", ETFS, 3, D(900)),  # of both people, at two brokers
        ("BOLT", ETFS, 1, D(500)),
        ("SPRK", STOCKS, 1, D("401.40")),
    ]
    widg = [p for p in held.positions if p.symbol == "WIDG"]
    [mine] = [h for h in holdings if h.symbol == "WIDG"]
    assert mine.value == sum(p.value for p in widg)
    assert mine.share == sum(held.shares[p] for p in widg)
    assert sum(h.share for h in holdings) == 100
    assert sum(h.value for h in holdings) == held.total
    for holding in holdings:
        parts = [p for p in held.positions if p.symbol == holding.symbol]
        if any(p.cost is None for p in parts):
            assert (holding.cost, holding.gain, holding.return_percent) == (None, None, None)
        else:
            assert holding.cost == sum(p.cost or 0 for p in parts)
            assert holding.gain == holding.value - holding.cost
    assert Portfolio("USD", None, (), ()).holdings == ()


def test_one_position_of_an_unknown_cost_leaves_its_instrument_s_unknown(
    conn: sqlite3.Connection,
) -> None:
    base = ibkr(bought("WIDG", "4", "-200", day(15)))
    printed = (Holding("IBKR USD", "WIDG", "USD", D(10), D(55), D(550), "ETFs"),)
    close(
        conn,
        replace(base, extraction=replace(base.extraction, holdings=printed)),
        account(
            "revolut",
            "Revolut USD",
            "USD",
            "100",
            bought("WIDG", "2", "-100", day(15), account="Revolut USD"),
        ),
    )
    price(conn, "WIDG", day(30), "55")
    [holding] = portfolio.run(conn, currency="USD").holdings
    assert (holding.value, holding.positions, holding.cost, holding.gain) == (D(660), 2, None, None)


def test_a_gain_is_also_a_part_of_what_was_paid(conn: sqlite3.Connection) -> None:
    rows = [bought("WIDG", "10", "-500", day(15)), bought("GADG", "3", "-900", day(16))]
    close(conn, ibkr(*rows, moved("FREE", "5", "0", day(17))))
    price(conn, "WIDG", day(30), "53.5")
    price(conn, "GADG", day(30), "290")
    price(conn, "FREE", day(30), "10")
    held = portfolio.run(conn, currency="USD").positions
    # what cost nothing has no percentage
    assert {p.symbol: (p.cost_native, p.gain_percent) for p in held} == {
        "WIDG": (D(500), D("7.00")),
        "GADG": (D(900), D("-3.33")),
        "FREE": (D(0), None),
    }


def test_a_gain_that_is_not_known_has_no_percentage(conn: sqlite3.Connection) -> None:
    base = ibkr(bought("WIDG", "4", "-200", day(15)))
    printed = (Holding("IBKR USD", "WIDG", "USD", D(10), D(55), D(550), "ETFs"),)
    close(conn, replace(base, extraction=replace(base.extraction, holdings=printed)))
    [position] = portfolio.run(conn, currency="USD").positions
    assert (position.cost_native, position.gain_percent) == (None, None)


def test_the_cash_of_a_pension_account_is_what_its_line_holds_beyond_the_units(
    conn: sqlite3.Connection,
) -> None:
    unit = bought(
        "CH0000000001", "2", "-300", day(15), asset_tab="Funds", account="Pillar 3a", currency="CHF"
    )
    close(conn, account("finpension", "Pillar 3a", "CHF", "1000", unit))
    close(
        conn,
        account("finpension", "Pillar 3a", "CHF", "250.55", filename="hers.csv"),
        owner=PARTNER,
    )
    price(conn, "CH0000000001", day(15), "155")
    rate(conn, "EUR", day(1), "0.9473")
    for owner, cash in ((None, D("950.55")), (OWNER, D(700)), (PARTNER, D("250.55"))):
        assert portfolio.run(conn, owner=owner, currency="CHF").pension_cash == cash
    for owner in (None, OWNER, PARTNER):
        held = portfolio.run(conn, owner=owner, currency="EUR")
        lines = networth.run(conn, owner=owner, currency="EUR").lines
        assert {line.asset for line in lines} == {PENSION}
        # with the units it is the line of net worth, to the cent
        assert held.pension_cash == sum(line.values[-1] for line in lines) - held.total


def test_without_a_pension_account_there_is_no_cash_of_one(conn: sqlite3.Connection) -> None:
    assert portfolio.run(conn).pension_cash is None
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15))))
    assert portfolio.run(conn, currency="USD").pension_cash is None
