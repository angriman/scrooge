"""What is held on the last committed day: the same figures as net worth, one per position."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from scrooge import categories, networth, portfolio, query, staging
from scrooge.models import Holding, Kind, TypedHolding
from scrooge.networth import BILLS, ETFS, PENSION, STOCKS, MissingRateError
from scrooge.portfolio import Portfolio, Position
from scrooge.storage import db, store
from tests.storage.helpers import (
    OCTOBER,
    OWNER,
    PARTNER,
    batch,
    certificate,
    certified,
    take_back,
)
from tests.test_networth import NOW, account, bill, bought, close, day, ibkr, price, rate

D = Decimal
SECURITIES = (ETFS, STOCKS, BILLS)  # the lines of net worth made of positions only


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def agrees_with_net_worth(conn: sqlite3.Connection, owner: str | None, currency: str) -> Portfolio:
    """The positions of each person and kind add up to the last value of their line."""
    held = portfolio.run(conn, owner=owner, currency=currency)
    worth = networth.run(conn, owner=owner, currency=currency)
    assert held.day == worth.days[-1]
    sums: dict[tuple[str, str], Decimal] = {}
    for position in held.positions:
        key = (position.owner, position.asset)
        sums[key] = sums.get(key, D(0)) + position.value
    lines = {(x.owner, x.asset): x.values[-1] for x in worth.lines if x.asset in SECURITIES}
    assert {key: total for key, total in sums.items() if key[1] in SECURITIES} == {
        key: total for key, total in lines.items() if key in sums
    }
    assert all(total == 0 for key, total in lines.items() if key not in sums)
    return held


# ------------------------------------------------------------------------------- nothing


def test_nothing_committed_is_nothing_held(conn: sqlite3.Connection) -> None:
    assert portfolio.run(conn) == Portfolio("EUR", None, (), ())
    staging.stage(conn, batch(ibkr(bought("WIDG", "10", "-500", day(15)))), NOW)
    assert portfolio.run(conn, currency="CHF") == Portfolio("CHF", None, (), ())


def test_an_unknown_person_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(query.QueryError, match="Nobody"):
        portfolio.run(conn, owner="Nobody")


def test_cash_alone_is_no_position(conn: sqlite3.Connection) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000"))
    staging.type_balance(
        conn, owner=OWNER, account="Old bank", day=day(5), amount=D(250), currency="CHF"
    )
    take_back(conn, OWNER)
    staging.stage(conn, certified(certificate(day(5), "35802.00")), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    assert len(networth.run(conn, currency="CHF").notes) == 2
    # neither the bank, nor what is typed, nor the pension fund; and none of their notes
    assert portfolio.run(conn, currency="CHF") == Portfolio("CHF", day(30), (), (), through=day(30))


def type_shares(conn: sqlite3.Connection, owner: str) -> None:
    """Three shares of ABCD typed for `owner` on the 1st, valued by a close of the 5th."""
    holding = TypedHolding("ABCD", D(3), "USD")
    staging.type_holding(conn, owner=owner, account="Broker", day=day(1), holding=holding)
    price(conn, "ABCD", day(5), "10")
    rate(conn, "USD", day(1), "0.9")


def test_a_person_with_only_typed_figures_holds_them_through_the_day_typed(
    conn: sqlite3.Connection,
) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000"))
    type_shares(conn, PARTNER)
    held = portfolio.run(conn, owner=PARTNER, currency="CHF")
    assert held.through == day(1)
    assert [(p.owner, p.symbol, p.quantity, p.value) for p in held.positions] == [
        (PARTNER, "ABCD", D(3), D("27.00"))
    ]
    # the one who has statements is covered further: the earliest day is the one shown
    assert portfolio.run(conn, currency="CHF").through == day(1)
    assert portfolio.run(conn, owner=OWNER, currency="CHF").through == day(30)


def test_when_nobody_has_statements_the_typed_figures_are_all_there_is(
    conn: sqlite3.Connection,
) -> None:
    type_shares(conn, OWNER)
    held = portfolio.run(conn, currency="CHF")
    assert held.through == day(1)
    assert [p.symbol for p in held.positions] == ["ABCD"]


# -------------------------------------------------------------------------------- shares


def test_shares_are_listed_with_the_close_they_are_valued_at(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15))))
    conn.execute("UPDATE instruments SET name = 'Widget Index Fund'")
    price(conn, "WIDG", day(15), "51")
    price(conn, "WIDG", day(18), "53.5")
    assert agrees_with_net_worth(conn, None, "USD") == Portfolio(
        "USD",
        day(30),
        (
            Position(
                owner=OWNER,
                account="IBKR USD",
                symbol="WIDG",
                name="Widget Index Fund",
                type="etf",
                asset=ETFS,
                currency="USD",
                quantity=D(10),
                price=D("53.5"),
                price_day=day(18),  # stale: the last close before the day
                price_is_trade=False,
                maturity=None,
                value_native=D("535.00"),
                value=D("535.00"),
                cost_native=D("500.00"),
                gain_native=D("35.00"),
                cost=D("500.00"),
                gain=D("35.00"),
            ),
        ),
        (),
        through=day(30),
    )


def test_without_any_close_the_price_of_the_last_trade_is_shown_as_such(
    conn: sqlite3.Connection,
) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15)), bought("WIDG", "-4", "220", day(20))))
    held = agrees_with_net_worth(conn, None, "USD")
    [position] = held.positions
    assert (position.quantity, position.price, position.price_day) == (D(6), D(55), day(20))
    assert position.price_is_trade
    assert position.value == D(330)
    assert held.notes == (
        "No close is stored for WIDG: valued at the price of the last trade. Run `scrooge prices`.",
    )


def test_a_trade_after_the_last_close_is_the_price_of_the_day(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15)), bought("WIDG", "-4", "220", day(20))))
    price(conn, "WIDG", day(16), "52")
    held = agrees_with_net_worth(conn, None, "USD")
    [position] = held.positions
    assert (position.price, position.price_day, position.price_is_trade) == (D(55), day(20), True)
    assert held.notes == ()  # net worth only says so where no close is stored at all


def test_what_was_sold_is_not_held(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15)), bought("WIDG", "-10", "520", day(20))))
    price(conn, "WIDG", day(30), "53")
    assert agrees_with_net_worth(conn, None, "USD").positions == ()


def test_shares_without_any_price_are_not_listed_and_a_note_says_so(
    conn: sqlite3.Connection,
) -> None:
    close(
        conn,
        ibkr(
            bought("WIDG", "10", "0", day(15), kind=Kind.TRANSFER, category=categories.OWN_TRANSFER)
        ),
    )
    conn.execute("UPDATE trades SET price = NULL")
    held = agrees_with_net_worth(conn, None, "USD")
    assert held.positions == ()
    assert held.notes == ("WIDG has no price at all: it is not counted.",)


def test_the_quantity_is_the_one_after_a_split(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-2000", day(3), asset_tab="Stocks")))
    price(conn, "WIDG", day(10), "50.5")
    instrument = conn.execute("SELECT id FROM instruments").fetchone()[0]
    store.add_split(conn, instrument, day(10), 4, 1)
    [position] = agrees_with_net_worth(conn, None, "USD").positions
    assert (position.type, position.asset) == ("stock", STOCKS)
    assert (position.quantity, position.price, position.value) == (D(40), D("50.5"), D(2020))


def test_holdings_a_statement_prints_are_held_at_the_close_it_prints(
    conn: sqlite3.Connection,
) -> None:
    base = ibkr()
    printed = (Holding("IBKR USD", "WIDG", "USD", D(10), D(55), D(550), "ETFs"),)
    close(conn, replace(base, extraction=replace(base.extraction, holdings=printed)))
    [position] = agrees_with_net_worth(conn, None, "USD").positions
    assert (position.quantity, position.price, position.price_day) == (D(10), D(55), day(30))
    assert not position.price_is_trade


# --------------------------------------------------------------------------------- bills


def test_a_bill_has_no_price_but_a_maturity(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bill("10000", "-9850", day(15))))
    held = agrees_with_net_worth(conn, None, "USD")
    [position] = held.positions
    assert position == Position(
        owner=OWNER,
        account="IBKR USD",
        symbol="912797ZZ1",
        name="",
        type="bill",
        asset=BILLS,
        currency="USD",
        quantity=D(10000),
        price=None,
        price_day=None,
        price_is_trade=False,
        maturity=date(2026, 12, 24),
        value_native=D("9872.50"),  # in a straight line from its cost to its face
        value=D("9872.50"),
        cost_native=D("9850.00"),
        gain_native=D("22.50"),  # the interest earned so far
        cost=D("9850.00"),
        gain=D("22.50"),
    )
    [holding] = held.holdings
    assert (holding.symbol, holding.type, holding.maturity) == (
        "912797ZZ1",
        "bill",
        date(2026, 12, 24),
    )


def test_a_bill_bought_before_the_history_is_not_listed_and_a_note_says_so(
    conn: sqlite3.Connection,
) -> None:
    base = ibkr()
    printed = (Holding("IBKR USD", "912797YY2", "USD", D(10000), D("98.5"), D(9850), "Bonds"),)
    close(conn, replace(base, extraction=replace(base.extraction, holdings=printed)))
    held = agrees_with_net_worth(conn, None, "USD")
    assert held.positions == ()
    assert held.notes == ("912797YY2 was bought before the history starts: it is not counted.",)


# ------------------------------------------------------------------------------- pension


def test_fund_units_of_a_pension_account_are_held_and_its_cash_is_not(
    conn: sqlite3.Connection,
) -> None:
    unit = bought(
        "CH0000000001", "2", "-300", day(15), asset_tab="Funds", account="Pillar 3a", currency="CHF"
    )
    close(conn, account("finpension", "Pillar 3a", "CHF", "1000", unit))
    price(conn, "CH0000000001", day(15), "155")
    [position] = portfolio.run(conn, currency="CHF").positions
    assert (position.type, position.asset, position.account) == ("fund", PENSION, "Pillar 3a")
    assert (position.quantity, position.value) == (D(2), D(310))
    # the one line of net worth with cash in it: the 700 left in the account
    [line] = networth.run(conn, currency="CHF").lines
    assert line.values[-1] - position.value == D(700)


# ---------------------------------------------------------------------------- currencies


def test_a_position_is_converted_at_the_rate_of_the_day(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15))))
    price(conn, "WIDG", day(30), "53.5")
    with pytest.raises(MissingRateError, match="no exchange rate is stored for USD"):
        portfolio.run(conn, currency="CHF")
    rate(conn, "USD", day(1), "0.7")
    rate(conn, "USD", day(29), "0.8")
    rate(conn, "EUR", day(1), "0.9")
    [in_francs] = agrees_with_net_worth(conn, None, "CHF").positions
    assert (in_francs.currency, in_francs.value_native, in_francs.value) == (
        "USD",
        D(535),
        D(428),
    )
    [in_euros] = agrees_with_net_worth(conn, None, "EUR").positions
    assert (in_euros.value_native, in_euros.value) == (D(535), D("475.56"))


def test_the_cents_of_the_positions_add_up_to_the_cents_of_net_worth(
    conn: sqlite3.Connection,
) -> None:
    # Half a cent each: on its own neither rounds up, together they are a cent.
    rows = [bought(ticker, "1", "-0.005", day(15)) for ticker in ("WIDG", "GADG", "SPRK")]
    close(conn, ibkr(*rows[:2]))
    held = agrees_with_net_worth(conn, None, "USD")
    assert [(p.symbol, p.value_native, p.value) for p in held.positions] == [
        ("WIDG", D("0.00"), D("0.01")),  # the first of two that lost as much
        ("GADG", D("0.00"), D("0.00")),
    ]
    take_back(conn, OWNER)
    close(conn, ibkr(*rows))
    held = agrees_with_net_worth(conn, None, "USD")
    assert sorted(p.value for p in held.positions) == [D("0.00"), D("0.01"), D("0.01")]


@given(st.lists(st.decimals(-10_000, 10_000, places=6), max_size=8))
def test_sharing_out_keeps_the_rounded_sum_and_moves_nothing_by_a_cent(
    exact: list[Decimal],
) -> None:
    parts = portfolio.shared_out(exact)
    assert sum(parts) == sum(exact, D(0)).quantize(D("0.01"))
    assert all(part == part.quantize(D("0.01")) for part in parts)
    assert all(abs(part - value) < D("0.01") for part, value in zip(parts, exact, strict=True))
    # what is a whole number of cents already stays as it is when all of them are
    whole = [value.quantize(D("0.01")) for value in exact]
    assert portfolio.shared_out(whole) == whole


def test_sharing_out_to_a_coarser_step_keeps_the_rounded_sum() -> None:
    # Each rounded on its own, 48.5 and 51.5 would be 49 and 52: 101.
    assert portfolio.shared_out([D("48.5"), D("51.5")], D(1)) == [D(49), D(51)]
    assert portfolio.shared_out([D("99.2433"), D("0.7195"), D("0.0372")], D("0.1")) == [
        D("99.3"),
        D("0.7"),
        D("0.0"),
    ]


# -------------------------------------------------------------------------------- people


def family(conn: sqlite3.Connection) -> None:
    close(
        conn,
        account("migros", "Migros", "CHF", "1000"),
        ibkr(
            bought("WIDG", "10", "-500", day(15)),
            bought("GADG", "3", "-900", day(16), asset_tab="Stocks"),
            bought("SPRK", "20", "-400", day(16), asset_tab="Stocks"),
            bought("BOLT", "20", "-200", day(17)),
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
    for symbol, close_ in (("WIDG", "50"), ("GADG", "301.3"), ("SPRK", "20.07"), ("BOLT", "25")):
        price(conn, symbol, day(30), close_)
    rate(conn, "USD", day(1), "0.8317")
    rate(conn, "EUR", day(1), "0.9473")


def test_positions_are_listed_by_person_kind_and_value(conn: sqlite3.Connection) -> None:
    family(conn)
    held = agrees_with_net_worth(conn, None, "USD")
    assert [(p.owner, p.asset, p.symbol, p.account, p.value) for p in held.positions] == [
        (PARTNER, ETFS, "WIDG", "IBKR USD", D(350)),
        (PARTNER, ETFS, "WIDG", "Revolut USD", D(50)),
        (OWNER, ETFS, "BOLT", "IBKR USD", D(500)),  # worth the same: by symbol
        (OWNER, ETFS, "WIDG", "IBKR USD", D(500)),
        (OWNER, STOCKS, "GADG", "IBKR USD", D("903.90")),
        (OWNER, STOCKS, "SPRK", "IBKR USD", D("401.40")),
        (OWNER, BILLS, "912797ZZ1", "IBKR USD", D("9872.50")),
    ]
    mine = agrees_with_net_worth(conn, OWNER, "USD")
    assert mine.positions == held.positions[2:]
    hers = agrees_with_net_worth(conn, PARTNER, "EUR")
    assert [p.value_native for p in hers.positions] == [D(350), D(50)]
    assert {p.owner for p in hers.positions} == {PARTNER}
    for currency in networth.CURRENCIES[:3]:
        agrees_with_net_worth(conn, None, currency)


def test_several_people_are_shown_on_the_latest_day_each_as_their_statements_leave_them(
    conn: sqlite3.Connection,
) -> None:
    family(conn)
    october = ibkr(bought("WIDG", "5", "-250", day(5, 10)), opening="8150", month=OCTOBER)
    close(conn, account("migros", "Migros", "CHF", "1000", month=OCTOBER), october, month=OCTOBER)
    mine = agrees_with_net_worth(conn, OWNER, "USD")
    assert mine.day == day(31, 10)
    assert {p.symbol: p.quantity for p in mine.positions}["WIDG"] == 15
    everyone = agrees_with_net_worth(conn, None, "USD")
    assert (everyone.day, everyone.through) == (day(31, 10), day(30))
    assert [p.quantity for p in everyone.positions if (p.owner, p.symbol) == (OWNER, "WIDG")] == [
        D(15)
    ]


TICKERS = {"WIDG": "ETFs", "BOLT": "ETFs", "GADG": "Stocks", "SPRK": "Stocks"}
bought_and_priced = st.dictionaries(
    st.sampled_from(sorted(TICKERS)),
    st.tuples(
        st.integers(1, 28),
        st.decimals("0.001", 300, places=3),
        st.decimals("0.001", 900, places=3),
    ),
    max_size=4,
)


@settings(max_examples=30, deadline=None)
@given(
    bought_and_priced,
    bought_and_priced,
    st.booleans(),
    st.sampled_from(["CHF", "EUR", "USD", "GBP"]),
    st.decimals("0.5", "1.5", places=4),
)
def test_holdings_and_net_worth_never_disagree(
    mine: dict[str, tuple[int, Decimal, Decimal]],
    hers: dict[str, tuple[int, Decimal, Decimal]],
    with_bill: bool,
    currency: str,
    dollar: Decimal,
) -> None:
    conn = db.connect(":memory:")
    db.migrate(conn, NOW)
    for owner, picked in ((OWNER, mine), (PARTNER, hers)):
        rows = [
            bought(ticker, str(quantity), "-100", day(n), asset_tab=TICKERS[ticker])
            for ticker, (n, quantity, _) in picked.items()
        ]
        if with_bill:
            rows.append(bill("10000", "-9850", day(15)))
        close(conn, ibkr(*rows, opening="50000"), owner=owner)
    for ticker, (_, _, close_) in (hers | mine).items():
        price(conn, ticker, day(29), str(close_))
    rate(conn, "USD", day(1), str(dollar))
    rate(conn, "EUR", day(1), "0.9473")
    rate(conn, "GBP", day(1), "1.1291")
    for shown in (None, OWNER, PARTNER):
        held = agrees_with_net_worth(conn, shown, currency)
        for position in held.positions:
            assert position.quantity
            if position.price is not None:
                exact = position.quantity * position.price
                assert position.value_native == exact.quantize(D("0.01"))
        assert portfolio.run(conn, owner=shown, currency=currency) == held
    conn.close()
