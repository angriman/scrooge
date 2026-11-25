"""The price step of month close, with a source that never connects."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from scrooge import marketdata, staging
from scrooge.marketdata import Refreshed
from scrooge.models import CashBalance, Holding, SecurityTrade
from scrooge.prices import Split
from scrooge.storage import db, store
from tests.fakes import FakeMaturities, FakeSource
from tests.storage.helpers import AUGUST, SEPTEMBER, batch, rows, statement, trade

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
END = date(2026, 9, 30)


def day(n: int, month: int = 9) -> date:
    return date(2026, month, n)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def hold(conn: sqlite3.Connection, *trades: SecurityTrade) -> None:
    """Stage an IBKR statement with these trades (their cash is not the point here)."""
    total = sum((row.amount for row in trades), start=trades[0].amount * 0)
    balance = CashBalance("IBKR USD", "USD", total * 0, total)
    staging.stage(conn, batch(statement("ibkr", trades, [balance])), NOW)


def bought(ticker: str, quantity: str, on: date, **fields: object) -> SecurityTrade:
    return trade("0", ticker, quantity, date=on, source_ref=f"{ticker}{on}{quantity}", **fields)


def closes(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return rows(
        conn,
        "SELECT i.symbol, p.date, p.close, p.source, p.fetched_at FROM prices p "
        "JOIN instruments i ON i.id = p.instrument_id ORDER BY i.symbol, p.date",
    )


def shares(source: FakeSource) -> list[str]:
    """The symbols asked for that are not exchange rates, in the order asked."""
    return [symbol for symbol, _, _ in source.asked if not symbol.endswith("=X")]


WEEK = [(day(14), "50.5"), (day(15), "51"), (day(16), "52.25"), (day(30), "55")]
USD = [(day(1), "0.8"), (day(30), "0.81")]


def test_closes_of_what_is_held_and_rates_of_every_currency_are_stored(
    conn: sqlite3.Connection,
) -> None:
    hold(conn, bought("WIDG", "10", day(15)))
    source = FakeSource({"WIDG": WEEK, "USDCHF=X": USD, "EURCHF=X": [(day(30), "0.95")]})
    done = marketdata.refresh(conn, source, through=END, now=NOW, currencies=["EUR"])
    assert done == Refreshed(closes=4, rates=3, splits=0, problems=())
    stamp = "2026-10-06T12:00:00+00:00"
    assert closes(conn) == [
        ("WIDG", "2026-09-14", "50.5", "fetched:fake", stamp),
        ("WIDG", "2026-09-15", "51", "fetched:fake", stamp),
        ("WIDG", "2026-09-16", "52.25", "fetched:fake", stamp),
        ("WIDG", "2026-09-30", "55", "fetched:fake", stamp),
    ]
    assert rows(conn, "SELECT currency, date, rate_to_chf, source FROM fx_rates ORDER BY 1, 2") == [
        ("EUR", "2026-09-30", "0.95", "fetched:fake"),
        ("USD", "2026-09-01", "0.8", "fetched:fake"),
        ("USD", "2026-09-30", "0.81", "fetched:fake"),
    ]
    # asked from a few days before the first day held, so that it has a close behind it
    assert ("WIDG", day(15) - timedelta(days=10), END) in source.asked
    assert rows(conn, "SELECT symbol, price_symbol FROM instruments") == [("WIDG", "WIDG")]


def test_what_is_stored_is_never_fetched_again(conn: sqlite3.Connection) -> None:
    hold(conn, bought("WIDG", "10", day(15)))
    source = FakeSource({"WIDG": WEEK[:3], "USDCHF=X": USD[:1]})
    marketdata.refresh(conn, source, through=day(20), now=NOW)
    # The source now gives another close for a day already stored, and newer days.
    again = FakeSource({"WIDG": [(day(16), "99"), *WEEK[3:]], "USDCHF=X": USD})
    done = marketdata.refresh(conn, again, through=END, now=LATER)
    assert done == Refreshed(closes=1, rates=1, splits=0, problems=())
    assert again.asked == [("WIDG", day(17), END), ("USDCHF=X", day(2), END)]
    stored = {(row[1], row[2], row[4]) for row in closes(conn)}
    assert ("2026-09-16", "52.25", "2026-10-06T12:00:00+00:00") in stored
    assert ("2026-09-30", "55", "2026-10-09T08:00:00+00:00") in stored
    # Up to date: nothing is asked for at all.
    idle = FakeSource()
    assert marketdata.refresh(conn, idle, through=END, now=LATER) == Refreshed(0, 0, 0, ())
    assert idle.asked == []


def test_an_instrument_sold_needs_no_close_after_the_last_of_it_went(
    conn: sqlite3.Connection,
) -> None:
    hold(conn, bought("WIDG", "10", day(3)), bought("WIDG", "-10", day(16)))
    source = FakeSource({"WIDG": WEEK, "USDCHF=X": USD})
    marketdata.refresh(conn, source, through=END, now=NOW)
    assert source.asked[0] == ("WIDG", day(3) - timedelta(days=10), day(16))
    assert [row[1] for row in closes(conn)] == ["2026-09-14", "2026-09-15", "2026-09-16"]


def test_bills_and_instruments_never_held_are_not_fetched(conn: sqlite3.Connection) -> None:
    bill = bought("912797ZZ1", "10000", day(9), asset_tab="Bonds", maturity=date(2026, 12, 24))
    hold(conn, bill, bought("WIDG", "10", day(15)), bought("WIDG", "-10", day(15)))
    conn.execute("INSERT INTO instruments (symbol, type, currency) VALUES ('ACME', 'stock', 'USD')")
    source = FakeSource({"USDCHF=X": USD})
    assert marketdata.refresh(conn, source, through=END, now=NOW).problems == ()
    assert [symbol for symbol, _, _ in source.asked] == ["USDCHF=X"]


def test_what_is_bought_after_the_last_day_wanted_is_left_for_later(
    conn: sqlite3.Connection,
) -> None:
    hold(conn, bought("WIDG", "10", day(15)))
    source = FakeSource({"WIDG": WEEK, "USDCHF=X": USD})
    marketdata.refresh(conn, source, through=day(10), now=NOW)
    assert [symbol for symbol, _, _ in source.asked] == ["USDCHF=X"]


def test_a_typed_symbol_wins_over_the_one_found_before(conn: sqlite3.Connection) -> None:
    hold(conn, bought("WIDG", "10", day(15)))
    source = FakeSource({"WIDG.SW": WEEK[:2], "WIDG.DE": WEEK, "USDCHF=X": USD})
    marketdata.refresh(conn, source, through=day(15), now=NOW, symbols={"WIDG": "WIDG.SW"})
    assert rows(conn, "SELECT price_symbol FROM instruments") == [("WIDG.SW",)]
    # Later runs keep using it without being told ...
    marketdata.refresh(conn, source, through=day(16), now=NOW, symbols={"WIDG": ""})
    assert shares(source)[-1] == "WIDG.SW"
    # ... until another one is typed.
    marketdata.refresh(conn, source, through=END, now=NOW, symbols={"WIDG": "WIDG.DE"})
    assert shares(source)[-1] == "WIDG.DE"
    assert rows(conn, "SELECT price_symbol FROM instruments") == [("WIDG.DE",)]


def test_a_fund_named_by_its_isin_is_looked_up(conn: sqlite3.Connection) -> None:
    fund = bought("CH0000000001", "2.5", day(15), asset_tab="Funds", currency="CHF")
    staging.stage(
        conn,
        batch(
            statement(
                "finpension", [fund], [CashBalance("IBKR USD", "CHF", fund.amount, fund.amount)]
            )
        ),
        NOW,
    )
    source = FakeSource(
        {"0P0000WIDG.SW": WEEK},
        currencies={"0P0000WIDG.SW": "CHF"},
        isins={"CH0000000001": "0P0000WIDG.SW"},
    )
    assert marketdata.refresh(conn, source, through=END, now=NOW).closes == 4
    assert rows(conn, "SELECT price_symbol FROM instruments") == [("0P0000WIDG.SW",)]
    assert closes(conn)[0][:3] == ("CH0000000001", "2026-09-14", "50.5")


def test_what_cannot_be_fetched_is_reported_and_the_rest_is_stored(
    conn: sqlite3.Connection,
) -> None:
    fund = bought("CH0000000001", "2.5", day(15), asset_tab="Funds")
    hold(
        conn,
        bought("WIDG", "10", day(15)),
        bought("ACME", "1", day(15)),
        bought("LSEX", "1", day(15)),
        bought("NEWX", "1", day(15)),
        fund,
    )
    source = FakeSource(
        {"WIDG": WEEK, "LSEX": WEEK, "NEWX": [], "USDCHF=X": USD, "GBPCHF=X": [(day(1), "1.1")]},
        currencies={"LSEX": "GBp", "USDCHF=X": "CHF", "GBPCHF=X": "USD"},
    )
    done = marketdata.refresh(conn, source, through=END, now=NOW, currencies=["GBP", "JPY", "CHF"])
    assert done.closes == 4
    assert done.rates == 2
    assert done.problems == (
        "ACME: no prices (ACME: No data found)",
        "LSEX: LSEX is quoted in GBp, not in USD: set its price_symbol in instruments.yaml",
        "NEWX: NEWX has no close since 2026-09-05",
        "CH0000000001: no prices (the price source does not know the ISIN CH0000000001)",
        "GBP: GBPCHF=X is quoted in USD, not in CHF",
        "JPY: no rates (JPYCHF=X: No data found)",
    )
    assert {row[0] for row in closes(conn)} == {"WIDG"}
    assert rows(conn, "SELECT DISTINCT currency FROM fx_rates") == [("USD",)]


def test_splits_are_stored_once(conn: sqlite3.Connection) -> None:
    hold(conn, bought("WIDG", "10", day(3)))
    split = Split(day(10), 4, 1)
    source = FakeSource({"WIDG": WEEK, "USDCHF=X": USD}, splits={"WIDG": [split]})
    assert marketdata.refresh(conn, source, through=day(20), now=NOW).splits == 1
    assert marketdata.refresh(conn, source, through=END, now=LATER).splits == 0
    assert rows(conn, "SELECT date, numerator, denominator FROM splits") == [("2026-09-10", 4, 1)]


def test_an_empty_database_asks_for_nothing(conn: sqlite3.Connection) -> None:
    source = FakeSource()
    done = marketdata.refresh(conn, source, through=END, now=NOW, currencies=["EUR"])
    assert done == Refreshed(0, 0, 0, ())
    assert source.asked == []


def test_shares_held_from_before_the_history_starts_are_priced(conn: sqlite3.Connection) -> None:
    base = statement("ibkr", [], [CashBalance("IBKR USD", "USD", D(0), D(0))], month=SEPTEMBER)
    held = (Holding("IBKR USD", "WIDG", "USD", D(10), D(55), D(550), "ETFs"),)
    staging.stage(
        conn, batch(replace(base, extraction=replace(base.extraction, holdings=held))), NOW
    )
    source = FakeSource({"WIDG": WEEK, "USDCHF=X": USD})
    marketdata.refresh(conn, source, through=date(2026, 10, 5), now=NOW)
    # Held when the statement begins: priced from its first day.
    assert source.asked[0] == ("WIDG", SEPTEMBER - timedelta(days=10), date(2026, 10, 5))


def test_a_statement_that_says_none_was_held_yet_is_not_the_first_day(
    conn: sqlite3.Connection,
) -> None:
    cash = [CashBalance("IBKR USD", "USD", D(0), D(0))]
    before = statement("ibkr", [], cash, filename="august.csv", month=AUGUST)
    nothing = replace(before, extraction=replace(before.extraction, holdings=()))
    staging.stage(conn, batch(nothing, month=AUGUST), NOW)
    after = statement("ibkr", [bought("WIDG", "10", day(15))], cash)
    held = (Holding("IBKR USD", "WIDG", "USD", D(10), D(55), D(550), "ETFs"),)
    staging.stage(
        conn, batch(replace(after, extraction=replace(after.extraction, holdings=held))), NOW
    )
    source = FakeSource({"WIDG": WEEK, "USDCHF=X": USD})
    marketdata.refresh(conn, source, through=END, now=NOW)
    assert source.asked[0] == ("WIDG", day(15) - timedelta(days=10), END)


def test_a_range_the_source_has_nothing_for_is_not_asked_again(conn: sqlite3.Connection) -> None:
    hold(conn, bought("WIDG", "10", day(15)))
    late = [(day(28), "54"), (day(30), "55")]
    rates = [(day(28), "0.8"), (day(30), "0.81")]
    marketdata.refresh(conn, FakeSource({"WIDG": late, "USDCHF=X": rates}), through=END, now=NOW)
    # Nothing new was imported: the days before the first close are not wanted again.
    idle = FakeSource()
    done = marketdata.refresh(conn, idle, through=END, now=LATER)
    assert idle.asked == []
    assert done == Refreshed(0, 0, 0, ())


def test_days_before_the_first_stored_are_fetched_when_older_history_is_imported(
    conn: sqlite3.Connection,
) -> None:
    hold(conn, bought("WIDG", "10", day(15)))
    marketdata.refresh(conn, FakeSource({"WIDG": WEEK, "USDCHF=X": USD}), through=END, now=NOW)
    # A statement of July now says the shares were bought then.
    cash = [CashBalance("IBKR USD", "USD", D(0), D(0))]
    july = date(2026, 7, 1)
    older = statement(
        "ibkr",
        [bought("WIDG", "5", day(20, 7), source_file="j.csv")],
        cash,
        filename="j.csv",
        month=july,
    )
    staging.stage(conn, batch(older, month=july), NOW)
    early = [(day(20, 7), "40"), (day(21, 7), "41")]
    split = [Split(day(10, 8), 2, 1)]
    source = FakeSource(
        {
            "WIDG": [*early, (day(14), "99")],
            "USDCHF=X": [(day(1, 7), "0.78"), (day(20, 7), "0.79"), *USD],
        },
        splits={"WIDG": split},
    )
    done = marketdata.refresh(conn, source, through=date(2026, 10, 5), now=LATER)
    # Only the days not stored are asked for: those before, and those since.
    assert source.asked == [
        ("WIDG", day(10, 7), day(4)),
        ("WIDG", date(2026, 10, 1), date(2026, 10, 5)),
        ("USDCHF=X", date(2026, 6, 20), day(20, 8)),  # the July statement opens on 30 June
        ("USDCHF=X", date(2026, 10, 1), date(2026, 10, 5)),
    ]
    assert done == Refreshed(closes=2, rates=2, splits=1, problems=())  # the split of both, once
    assert [row[1] for row in closes(conn)][:3] == ["2026-07-20", "2026-07-21", "2026-09-14"]
    idle = FakeSource()
    marketdata.refresh(conn, idle, through=END, now=LATER)
    assert idle.asked == []  # the days before are not asked for twice


def test_benchmarks_are_fetched_from_the_first_day_and_kept_as_instruments(
    conn: sqlite3.Connection,
) -> None:
    hold(conn, bought("WIDG", "10", day(15)))
    first = store.first_day(conn)
    assert first is not None
    source = FakeSource({"WIDG": WEEK, "VT": WEEK, "USDCHF=X": USD})
    done = marketdata.refresh(conn, source, through=day(20), now=NOW, benchmarks=["VT", "VT"])
    assert done == Refreshed(closes=6, rates=1, splits=0, problems=())
    assert shares(source) == ["WIDG", "VT"]  # once, however often it is named
    assert ("VT", first - timedelta(days=10), day(20)) in source.asked
    assert rows(conn, "SELECT symbol, type, currency, price_symbol FROM instruments") == [
        ("WIDG", "etf", "USD", "WIDG"),
        ("VT", "etf", "USD", "VT"),
    ]
    # Next time it is an instrument like any other: only the days since are asked for.
    again = FakeSource({"VT": WEEK, "WIDG": WEEK, "USDCHF=X": USD})
    marketdata.refresh(conn, again, through=END, now=LATER, benchmarks=["VT"])
    assert ("VT", day(17), END) in again.asked  # after the last close stored
    assert [row[1] for row in closes(conn) if row[0] == "VT"][-1] == "2026-09-30"


def test_a_benchmark_held_is_fetched_once_over_the_days_of_both(
    conn: sqlite3.Connection,
) -> None:
    hold(conn, bought("WIDG", "10", day(15)))
    first = store.first_day(conn)
    assert first is not None
    source = FakeSource({"WIDG": WEEK, "USDCHF=X": USD})
    marketdata.refresh(conn, source, through=END, now=NOW, benchmarks=["WIDG"])
    assert shares(source) == ["WIDG"]
    assert source.asked[0] == ("WIDG", first - timedelta(days=10), END)
    assert rows(conn, "SELECT count(*) FROM instruments") == [(1,)]


def test_a_benchmark_the_source_has_nothing_for_is_reported_and_not_kept(
    conn: sqlite3.Connection,
) -> None:
    hold(conn, bought("WIDG", "10", day(15)))
    source = FakeSource({"WIDG": WEEK, "EMPTY": [], "USDCHF=X": USD})
    done = marketdata.refresh(conn, source, through=END, now=NOW, benchmarks=["NOPE", "EMPTY"])
    first = store.first_day(conn)
    assert first is not None
    assert done.problems == (
        "NOPE: no prices (NOPE: No data found)",
        f"EMPTY: EMPTY has no close since {first - timedelta(days=10)}",
    )
    assert rows(conn, "SELECT symbol FROM instruments") == [("WIDG",)]


def test_benchmarks_are_not_fetched_before_anything_is_known(conn: sqlite3.Connection) -> None:
    source = FakeSource({"VT": WEEK})
    assert marketdata.refresh(conn, source, through=END, now=NOW, benchmarks=["VT"]).closes == 0
    hold(conn, bought("WIDG", "10", day(15)))
    marketdata.refresh(conn, source, through=date(2026, 8, 1), now=NOW, benchmarks=["VT"])
    assert source.asked == []


def test_maturities_are_looked_up_and_the_unknown_bills_named() -> None:
    source = FakeMaturities({"912797XX3": date(2026, 11, 27)})
    looked = marketdata.maturities(source, {"912797ZZ1", "912797XX3"})
    assert looked.found == {"912797XX3": date(2026, 11, 27)}
    assert looked.unknown == ("912797ZZ1",)
    assert source.asked == ["912797XX3", "912797ZZ1"]


def test_stale_lists_what_is_held_or_counted_in_without_a_close_or_rate_of_the_last_week(
    conn: sqlite3.Connection,
) -> None:
    assert marketdata.stale(conn, day(30), ["EUR"]) == []  # nothing known: nothing to value
    bill = bought("912797ZZ1", "10000", day(3), asset_tab="Bonds", maturity=date(2026, 12, 24))
    gone = [bought("GONE", "10", day(3)), bought("GONE", "-10", day(4))]
    hold(conn, bought("WIDG", "10", day(3)), bought("ACME", "5", day(3)), *gone, bill)
    source = FakeSource({"WIDG": WEEK, "ACME": WEEK[:3], "USDCHF=X": USD})
    marketdata.refresh(conn, source, through=END, now=NOW)
    # Yesterday is 30 September: ACME's newest close, of the 16th, is more than a week before;
    # GONE was sold, a bill has no close, and EUR has never been fetched.
    assert marketdata.stale(conn, date(2026, 10, 1), ["EUR"]) == [
        "ACME: the newest close is of 2026-09-16",
        "EUR: no rate stored",
    ]
    # A week later nothing is new: everything held is behind, and so is every rate.
    assert marketdata.stale(conn, date(2026, 10, 9)) == [
        "WIDG: the newest close is of 2026-09-30",
        "ACME: the newest close is of 2026-09-16",
        "USD: the newest rate is of 2026-09-30",
    ]
    assert "GONE: no close stored" not in marketdata.stale(conn, date(2026, 10, 9))
