"""What the investments returned over a range, and how much of the change was money put in."""

import sqlite3
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest

from scrooge import performance
from scrooge.models import Kind, Transaction
from scrooge.performance import Point, base_day, measure, money_weighted, sampled, time_weighted
from scrooge.storage import db
from tests.storage.helpers import AUGUST, OWNER, PARTNER
from tests.test_networth import NOW, account, bought, close, day, ibkr, price, rate, spent

D = Decimal


def values(*amounts: int) -> list[Decimal]:
    return [D(amount) for amount in amounts]


# ---------------------------------------------------------------------- rates of return


def test_without_flows_the_time_weighted_return_is_the_simple_return() -> None:
    worth = values(1000, 1100, 1045, 1210)
    assert time_weighted(worth, values(0, 0, 0, 0)) == D("0.21")
    measured = measure(worth, values(0, 0, 0, 0))
    assert (measured.net_flows, measured.gain, measured.twr) == (D(0), D(210), D("21.00"))


def test_a_deposit_comes_at_the_start_of_its_day_and_earns_from_then() -> None:
    # 10% on the first day; 1000 put in, 2200 out of 2100 on the second; 5% on the third.
    worth, flows = values(1000, 1100, 2200, 2310), values(0, 0, 1000, 0)
    measured = measure(worth, flows)
    assert (measured.start, measured.end, measured.net_flows) == (D(1000), D(2310), D(1000))
    assert measured.gain == D(310)
    assert measured.twr == D("21.00")  # 1.1 x 22/21 x 1.05
    # 310 on 1000 for three days and 1000 for two: as a rate a year, past any to give.
    assert measured.mwr is None


def test_a_withdrawal_takes_money_out_without_being_a_loss() -> None:
    # 20% on the first day; 600 taken out, nothing moves on the second; 10% on the third.
    measured = measure(values(1000, 1200, 600, 660), values(0, 0, -600, 0))
    assert (measured.net_flows, measured.gain, measured.twr) == (D(-600), D(260), D("32.00"))


def test_a_range_that_starts_with_nothing_counts_from_the_first_money_put_in() -> None:
    measured = measure(values(0, 0, 1000, 1100), values(0, 0, 1000, 0))
    assert (measured.start, measured.net_flows, measured.gain) == (D(0), D(1000), D(100))
    assert measured.twr == D("10.00")
    assert time_weighted(values(0, 0, 0), values(0, 0, 0)) is None
    assert measure(values(0, 0), values(0, 0)) == performance.Measured(D(0), D(0), D(0), None, None)


def test_a_day_that_starts_with_less_than_nothing_is_no_day_of_return() -> None:
    # 100 owed, 150 put in: the 50 left grows by 10%.
    assert time_weighted(values(-100, 50, 55), values(0, 150, 0)) == D("0.1")


def test_what_is_made_before_any_money_is_put_in_is_no_return() -> None:
    # A bill bought before the deposit that pays for it: a few cents a day on nothing, then
    # 1000 put in that earns 5%.
    worth = [D(0), D(2), D(4), D(1002), D("1052.10")]
    flows = values(0, 0, 0, 998, 0)
    assert time_weighted(worth, flows) == D("0.05")
    assert measure(worth, flows).gain == D("54.10")


def test_a_day_on_next_to_nothing_or_ending_below_nothing_is_no_day_of_return() -> None:
    # 5 left after 1095 taken out, then 9: each under a hundredth of the most invested.
    assert time_weighted(values(1000, 1100, 9, 18), values(0, 0, -1095, 0)) == D("0.1")
    # Losing more than there was: money put in is missing, not a return of -300%.
    assert time_weighted(values(1000, 1100, -2200), values(0, 0, 0)) == D("0.1")


@pytest.mark.parametrize(
    ("dated", "rate"),
    [
        ([(0, -1000), (365, 1100)], "0.1"),
        ([(0, -1000), (365, 500)], "-0.5"),
        # 1000 for two years and 1000 for one, both at 10%: 1210 + 1100.
        ([(0, -1000), (365, -1000), (730, 2310)], "0.1"),
        # Money taken out halfway: 1000 grows to 1100, 550 leaves, 550 grows to 605.
        ([(0, -1000), (365, 550), (730, 605)], "0.1"),
    ],
)
def test_the_money_weighted_return_converges_on_the_rate_a_year(
    dated: list[tuple[int, int]], rate: str
) -> None:
    found = money_weighted([(days, D(amount)) for days, amount in dated])
    assert found is not None
    assert abs(found - D(rate)) < D("1e-9")


@pytest.mark.parametrize(
    "dated",
    [
        [],
        [(0, 0), (365, 0)],
        [(0, 1000), (365, 100)],  # only money out: no rate makes it nothing
        [(0, -1000), (0, 1000)],  # no time passes
        [(0, -1000), (1, 2000)],  # doubled in a day: more than a billion times a year
    ],
)
def test_without_a_rate_to_find_the_money_weighted_return_is_none(
    dated: list[tuple[int, int]],
) -> None:
    assert money_weighted([(days, D(amount)) for days, amount in dated]) is None


def test_a_range_starts_so_many_days_back_and_never_before_the_history() -> None:
    first, last = date(2025, 3, 1), date(2026, 9, 30)
    assert base_day("1m", first, last) == date(2026, 8, 31)
    assert base_day("1y", first, last) == date(2025, 9, 30)
    assert base_day("ytd", first, last) == date(2025, 12, 31)
    assert base_day("3y", first, last) == date(2025, 2, 28)
    assert base_day("all", first, last) == date(2025, 2, 28)
    assert base_day("ytd", date(2026, 5, 1), last) == date(2026, 4, 30)


def test_a_long_range_is_drawn_one_point_a_week_up_to_its_last_day() -> None:
    first = date(2025, 1, 1)
    points = [Point(first + timedelta(days=n), D(n), D(0)) for n in range(500)]
    weekly = sampled(points)
    assert weekly[0] == points[0]
    assert weekly[-1] == points[-1]
    assert {(b.day - a.day).days for a, b in pairwise(weekly[1:])} == {7}
    assert sampled(points[:30]) == tuple(points[:30])


# ---------------------------------------------------------------------------- accounts


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def deposit(amount: str, on: date, **fields: object) -> Transaction:
    fields.setdefault("account", "IBKR USD")
    fields.setdefault("currency", "USD")
    return spent(amount, on, kind=Kind.TRANSFER, category="Giroconto", **fields)


def test_money_put_in_is_set_apart_from_what_the_investments_made(
    conn: sqlite3.Connection,
) -> None:
    rows = (
        deposit("5000", day(10)),
        bought("WIDG", "10", "-500", day(15)),
        spent(
            "20",
            day(20),
            account="IBKR USD",
            currency="USD",
            kind=Kind.DIVIDEND,
            category="Dividendi",
        ),
    )
    close(
        conn,
        ibkr(*rows),
        account("migros", "Migros", "CHF", "1000", spent("-200", day(12))),
        account("finpension", "Pillar 3a", "CHF", "3000"),
    )
    price(conn, "WIDG", day(15), "50")
    price(conn, "WIDG", day(25), "55")
    shown = performance.run(conn, currency="USD", range_="all")
    assert (shown.first, shown.last) == (day(31, 8), day(30))
    totals = shown.totals
    assert totals is not None
    # The 10000 the account opens with on the eve of its statement, and the 5000 deposited.
    assert (totals.start, totals.net_flows, totals.end) == (D(0), D(15000), D("15070.00"))
    assert totals.gain == D("70.00")  # the dividend and the rise of the shares
    assert totals.twr == D("0.47")  # 15070 / 15000
    assert totals.mwr is not None
    assert totals.mwr > 0
    # Neither the bank account nor the pension fund is an investment account.
    assert [(row.owner, row.name, row.accounts) for row in shown.holders] == [
        (OWNER, "IBKR", ("IBKR USD",))
    ]
    assert shown.points[0] == Point(day(30, 8), D(0), D(0))
    assert shown.points[1] == Point(day(31, 8), D("10000.00"), D("10000.00"))
    assert shown.points[-1] == Point(day(30), D("15070.00"), D("15000.00"))
    assert (shown.missing, shown.left_out) == ((), ())


def test_a_range_starts_from_what_was_held_on_the_day_before_it(
    conn: sqlite3.Connection,
) -> None:
    close(conn, ibkr(deposit("5000", day(10)), bought("WIDG", "100", "-5000", day(10))))
    price(conn, "WIDG", day(10), "50")
    price(conn, "WIDG", day(30), "60")
    price(conn, "WIDG", day(31, 10), "66")
    shown = performance.run(conn, currency="USD", range_="1m")
    assert (shown.first, shown.last) == (day(2, 10), day(31, 10))  # 30 days back
    totals = shown.totals
    assert totals is not None
    assert (totals.start, totals.end, totals.net_flows) == (D(16000), D(16600), D(0))
    assert totals.twr == D("3.75")


def test_shares_vested_as_salary_are_money_put_in_and_a_bill_repaid_is_not(
    conn: sqlite3.Connection,
) -> None:
    plan = {"account": "IBKR USD", "currency": "USD"}
    rows = (
        spent("1000", day(5), kind=Kind.INCOME, category="Stipendio", **plan),
        bought("ACME", "10", "-1000", day(5), asset_tab="Stocks"),
        spent("990", day(8), kind=Kind.INCOME, category="Investimenti", **plan),
        spent("-30", day(9), kind=Kind.COMMISSION, category="Commissioni", **plan),
    )
    close(conn, ibkr(*rows, opening="0"))
    price(conn, "ACME", day(5), "100")
    shown = performance.run(conn, currency="USD", range_="all")
    assert shown.totals is not None
    assert shown.totals.net_flows == D(1000)
    assert shown.totals.gain == D(960)  # what came back from the bill, less the commission


def test_shares_moved_in_kind_are_money_put_in_at_the_close_of_their_day(
    conn: sqlite3.Connection,
) -> None:
    moved = bought("WIDG", "10", "0", day(15), kind=Kind.TRANSFER, category="Giroconto")
    close(conn, ibkr(moved, opening="0"))
    price(conn, "WIDG", day(15), "52")
    price(conn, "WIDG", day(20), "57.2")
    shown = performance.run(conn, currency="USD", range_="all")
    assert shown.totals is not None
    assert (shown.totals.net_flows, shown.totals.gain) == (D(520), D(52))
    assert shown.totals.twr == D("10.00")


def test_a_broker_s_accounts_in_each_currency_are_one_and_an_exchange_moves_nothing(
    conn: sqlite3.Connection,
) -> None:
    fx = {"kind": Kind.FX, "category": "Giroconto"}
    close(
        conn,
        ibkr(spent("-1000", day(10), account="IBKR USD", currency="USD", **fx)),
        account(
            "ibkr",
            "IBKR CHF",
            "CHF",
            "0",
            spent("900", day(10), account="IBKR CHF", currency="CHF", **fx),
        ),
    )
    rate(conn, "USD", day(1), "0.9")
    shown = performance.run(conn, currency="CHF", range_="all")
    assert [(row.name, row.accounts) for row in shown.holders] == [
        ("IBKR", ("IBKR CHF", "IBKR USD"))
    ]
    assert shown.totals is not None
    assert (shown.totals.net_flows, shown.totals.gain) == (D(9000), D(0))


def test_a_currency_without_a_rate_leaves_its_accounts_out_and_not_the_others(
    conn: sqlite3.Connection,
) -> None:
    close(
        conn,
        ibkr(),
        account("tastytrade", "Tastytrade", "CHF", "2000"),
    )
    shown = performance.run(conn, currency="CHF", range_="all")
    assert shown.missing == ("no exchange rate is stored for USD: run `scrooge prices`",)
    assert shown.left_out == (f"IBKR ({OWNER})",)
    assert [row.name for row in shown.holders] == ["Tastytrade"]
    nothing = performance.run(conn, currency="EUR", range_="all")
    assert (nothing.totals, nothing.holders, nothing.points) == (None, (), ())
    assert len(nothing.left_out) == 2


def test_each_person_and_both_together(conn: sqlite3.Connection) -> None:
    close(conn, ibkr())
    close(conn, ibkr(opening="500", month=AUGUST), owner=PARTNER, month=AUGUST)
    close(conn, ibkr(opening="500"), owner=PARTNER)
    both = performance.run(conn, currency="USD", range_="all")
    assert [(row.owner, row.measured.end) for row in both.holders] == [
        (PARTNER, D("500.00")),
        (OWNER, D("10000.00")),
    ]
    assert both.totals is not None
    assert both.totals.end == D("10500.00")
    alone = performance.run(conn, owner=PARTNER, currency="USD", range_="all")
    assert [row.owner for row in alone.holders] == [PARTNER]


def test_without_investment_accounts_there_is_nothing_to_measure(
    conn: sqlite3.Connection,
) -> None:
    assert performance.run(conn) == performance.Performance("EUR", "ytd", None, None, None, (), ())
    close(conn, account("migros", "Migros", "CHF", "1000"))
    assert performance.run(conn, currency="CHF").holders == ()
