"""What the investments are compared with: a benchmark's return and its line over a range."""

import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest

from scrooge import benchmark, performance
from scrooge.benchmark import Benchmark, BenchmarkError
from scrooge.networth import MissingRateError
from scrooge.storage import db, store
from tests.test_networth import NOW, close, day, ibkr, price, rate
from tests.test_performance import deposit

D = Decimal


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def fund(conn: sqlite3.Connection, symbol: str = "VT", price_symbol: str | None = "VT") -> int:
    """A benchmark as `scrooge prices` leaves it: an ETF in dollars, priced under its symbol."""
    store.ensure_currency(conn, "USD")
    row = conn.execute(
        "INSERT INTO instruments (symbol, type, currency, price_symbol) "
        "VALUES (?, 'etf', 'USD', ?) RETURNING id",
        (symbol, price_symbol),
    ).fetchone()
    return int(row[0])


def days(first: int, last: int) -> list[date]:
    return [day(n) for n in range(first, last + 1)]


def money(*amounts: int) -> list[Decimal]:
    return [D(amount) for amount in amounts]


def test_the_money_put_in_is_followed_into_the_benchmark_through_a_split(
    conn: sqlite3.Connection,
) -> None:
    vt = fund(conn)
    price(conn, "VT", day(1), "100")
    price(conn, "VT", day(3), "110")
    store.add_split(conn, vt, day(4), 2, 1)  # from the 4th, two shares for each one held
    price(conn, "VT", day(5), "60")
    # 1000 to start with, 100 put in on the 3rd, 500 taken out on the 5th.
    followed = benchmark.follow(
        conn, "VT", days(1, 5), money(1000, 0, 100, 0, -500), currency="USD"
    )
    # 10 shares; one more bought at the close the 3rd starts from (100); 11 at 110; after the
    # split 22 at 60, of which 500 are sold.
    assert followed.values == (D("1000.00"), D("1000.00"), D("1210.00"), D("1210.00"), D("820.00"))
    assert followed.benchmark == Benchmark("VT", D("20.00"))  # 120 / 100


def test_its_return_counts_from_the_day_asked_and_in_the_currency_asked(
    conn: sqlite3.Connection,
) -> None:
    fund(conn)
    price(conn, "VT", day(1), "100")
    price(conn, "VT", day(2), "100")
    price(conn, "VT", day(3), "110")
    rate(conn, "USD", day(1), "0.8")
    rate(conn, "USD", day(3), "0.9")
    followed = benchmark.follow(conn, "VT", days(1, 3), money(0, 0, 0), currency="CHF", start=1)
    assert followed.values == (D(0), D(0), D(0))  # nothing was put in
    # 80 francs on the 2nd, 99 on the 3rd: the dollar's rise is part of it.
    assert followed.benchmark.returned == D("23.75")


def test_shares_moved_in_kind_are_followed_at_the_close_of_their_day(
    conn: sqlite3.Connection,
) -> None:
    fund(conn)
    price(conn, "VT", day(1), "100")
    price(conn, "VT", day(2), "125")
    # Of the 350 that came on the 2nd, 250 were shares valued at its close: those buy 2 there,
    # the 100 of cash 1 at the close of the 1st.
    followed = benchmark.follow(
        conn, "VT", days(1, 2), money(100, 350), late=money(0, 250), currency="USD"
    )
    assert followed.values == (D("100.00"), D("500.00"))
    out = benchmark.follow(
        conn, "VT", days(1, 2), money(200, -125), late=money(0, -125), currency="USD"
    )
    assert out.values == (D("200.00"), D("125.00"))  # one sold at the close of the 2nd


def test_more_taken_out_than_it_would_be_worth_sells_what_there_is(
    conn: sqlite3.Connection,
) -> None:
    fund(conn)
    price(conn, "VT", day(1), "100")
    price(conn, "VT", day(2), "50")
    followed = benchmark.follow(conn, "VT", days(1, 3), money(1000, -800, 100), currency="USD")
    assert followed.values == (D("1000.00"), D("0.00"), D("100.00"))


def test_a_close_typed_by_hand_wins_and_one_priced_under_another_symbol_is_found(
    conn: sqlite3.Connection,
) -> None:
    fund(conn, "XX0000000001", "VT")
    price(conn, "XX0000000001", day(1), "100")
    price(conn, "XX0000000001", day(2), "104", source="fetched:yahoo")
    price(conn, "XX0000000001", day(2), "105", source="manual")
    followed = benchmark.follow(conn, "VT", days(1, 2), money(100, 0), currency="USD")
    assert followed.benchmark.returned == D("5.00")


@pytest.mark.parametrize(
    ("closes", "why"),
    [
        ((), "no close of it is stored; `scrooge prices` fetches it"),
        (
            ((day(2), "100"), (day(3), "101")),
            "no close of it is stored on or before 2026-09-01; `scrooge prices` fetches it",
        ),
        (
            ((day(1), "100"),),
            "no close of it is stored after 2026-09-01; `scrooge prices` fetches it",
        ),
    ],
)
def test_a_benchmark_without_closes_for_the_range_is_refused(
    conn: sqlite3.Connection, closes: tuple[tuple[date, str], ...], why: str
) -> None:
    if closes:
        fund(conn)
    for on, close_ in closes:
        price(conn, "VT", on, close_)
    with pytest.raises(BenchmarkError) as refused:
        benchmark.follow(conn, "VT", days(1, 5), money(1, 0, 0, 0, 0), currency="USD")
    assert str(refused.value) == why


def test_a_benchmark_in_a_currency_without_a_rate_is_refused(conn: sqlite3.Connection) -> None:
    fund(conn)
    price(conn, "VT", day(1), "100")
    price(conn, "VT", day(2), "101")
    with pytest.raises(MissingRateError):
        benchmark.follow(conn, "VT", days(1, 2), money(1, 0), currency="GBP")


def test_the_investments_are_set_beside_each_benchmark_and_one_without_closes_is_said(
    conn: sqlite3.Connection,
) -> None:
    close(conn, ibkr(deposit("5000", day(10))))  # 10000 to open with on 31 August
    fund(conn)
    price(conn, "VT", day(28, 8), "100")
    price(conn, "VT", day(15), "110")
    price(conn, "VT", day(30), "120")
    shown = performance.run(conn, currency="USD", range_="all", benchmarks=("VT", "VOO"))
    assert shown.benchmarks == (Benchmark("VT", D("20.00")),)
    assert shown.unbenchmarked == (
        "VOO is left out: no close of it is stored; `scrooge prices` fetches it",
    )
    # 10000 buys 100 shares at the close of 30 August, the 5000 put in on the 10th 50 more
    # at that of the 9th; 150 at 120 at the end.
    assert [point.benchmarks for point in shown.points[:2]] == [(D(0),), (D("10000.00"),)]
    assert shown.points[-1].benchmarks == (D("18000.00"),)


def test_a_benchmark_s_return_counts_from_where_the_time_weighted_return_does(
    conn: sqlite3.Connection,
) -> None:
    fund(conn)
    price(conn, "VT", day(1), "100")
    price(conn, "VT", day(2), "120")
    price(conn, "VT", day(3), "150")
    totals = performance.Measured(D(0), D(0), D(0), None, None, since=day(3))
    followed, _ = performance._followed(  # where the return is counted from
        conn,
        ("VT",),
        days(1, 3),
        performance.Days(money(0, 0, 0), money(0, 0, 0), money(0, 0, 0)),
        totals=totals,
        currency="USD",
    )
    assert followed[0].benchmark.returned == D("25.00")  # from the end of the 2nd
