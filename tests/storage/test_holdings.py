import sqlite3
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finanze import monthclose
from finanze.models import CashBalance, Holding, SecurityTrade
from finanze.pipeline import Statement
from finanze.storage import holdings, store
from finanze.storage.holdings import Change, Mismatch, Step, timeline
from finanze.validation import HoldingsMismatchError
from tests.storage.conftest import NOW
from tests.storage.helpers import AUGUST, OCTOBER, OWNER, SEPTEMBER, batch, statement, trade

SPLIT, TRADE, PRINTED = 0, 1, 2
D = Decimal


def day(n: int, month: int = 9) -> date:
    return date(2026, month, n)


def test_trades_add_up_from_nothing_when_no_statement_prints_the_holdings() -> None:
    opening, steps, mismatches = timeline(
        [Change(day(20), TRADE, D(-4)), Change(day(3), TRADE, D(10))]
    )
    assert opening == 0
    assert steps == [Step(day(3), D(10)), Step(day(20), D(6))]
    assert mismatches == []


def test_several_changes_on_one_day_make_one_step() -> None:
    _, steps, _ = timeline([Change(day(3), TRADE, D(10)), Change(day(3), TRADE, D(5))])
    assert steps == [Step(day(3), D(15))]


def test_the_quantity_before_the_first_statement_is_worked_out_backward() -> None:
    # 7 were held before the history starts: 7 + 10 - 5 is what the statement prints.
    changes = [
        Change(day(3), TRADE, D(10)),
        Change(day(12), TRADE, D(-5)),
        Change(day(30), PRINTED, D(12)),
    ]
    opening, steps, mismatches = timeline(changes)
    assert opening == 7
    assert [step.quantity for step in steps] == [17, 12, 12]
    assert mismatches == []


def test_a_later_statement_that_disagrees_is_a_mismatch_and_wins() -> None:
    changes = [
        Change(day(31, 8), PRINTED, D(10)),
        Change(day(12), TRADE, D(5)),
        Change(day(30), PRINTED, D(20)),
        Change(day(5, 10), TRADE, D(1)),
    ]
    opening, steps, mismatches = timeline(changes)
    assert opening == 10
    assert mismatches == [Mismatch(day(30), D(20), D(15))]
    assert steps[-1] == Step(day(5, 10), D(21))


def test_a_split_multiplies_what_was_held_overnight_and_not_what_is_bought_that_day() -> None:
    changes = [
        Change(day(3), TRADE, D(10)),
        Change(day(15), TRADE, D(1)),  # already in the new shares
        Change(day(15), SPLIT, D(4)),
        Change(day(30), PRINTED, D(41)),
    ]
    opening, steps, mismatches = timeline(changes)
    assert opening == 0
    assert mismatches == []
    assert steps == [Step(day(3), D(10)), Step(day(15), D(41)), Step(day(30), D(41))]


def test_a_split_before_the_first_statement_is_undone_on_the_way_back() -> None:
    changes = [Change(day(15), SPLIT, D(4)), Change(day(30), PRINTED, D(40))]
    opening, steps, _ = timeline(changes)
    assert opening == 10
    assert steps == [Step(day(15), D(40)), Step(day(30), D(40))]


def test_holding_on_a_day_is_the_last_step_up_to_it() -> None:
    held = holdings.Holding(1, 1, D(7), (Step(day(3), D(17)), Step(day(12), D(12))), ())
    assert [held.on(day(n)) for n in (1, 3, 11, 12, 30)] == [7, 17, 17, 12, 12]


@given(
    st.lists(st.tuples(st.integers(1, 28), st.integers(-50, 50)), max_size=8),
    st.integers(0, 500),
)
def test_a_first_statement_is_always_met_and_trades_after_it_add_up(
    trades: list[tuple[int, int]], printed: int
) -> None:
    changes = [Change(day(n), TRADE, D(q)) for n, q in trades]
    changes.append(Change(day(15), PRINTED, D(printed)))
    opening, steps, mismatches = timeline(changes)
    assert mismatches == []
    held = holdings.Holding(1, 1, opening, tuple(steps), ())
    assert held.on(day(15)) == printed
    assert held.on(day(28)) == printed + sum(q for n, q in trades if n > 15)
    assert opening == printed - sum(q for n, q in trades if n <= 15)


# ---------------------------------------------------------------- with the database


def ibkr(
    month: date, held: dict[str, str] | None, *rows: SecurityTrade, unvested: bool = False
) -> Statement:
    """An IBKR statement that prints `held` (None: prints nothing about holdings)."""
    balance = CashBalance("IBKR USD", "USD", D(0), sum((r.amount for r in rows), D(0)))
    base = statement("ibkr", rows, [balance], filename=f"ibkr {month:%Y-%m}.csv", month=month)
    printed = None
    if held is not None:
        printed = tuple(
            Holding(
                "IBKR USD", ticker, "USD", D(quantity), D(50), D(quantity) * 50, "ETFs", unvested
            )
            for ticker, quantity in held.items()
        )
    return replace(base, extraction=replace(base.extraction, holdings=printed))


def stage(conn: sqlite3.Connection, month: date, *statements: Statement) -> monthclose.Staged:
    return monthclose.stage(conn, batch(*statements, month=month), NOW)


def buy(quantity: str, on: date, ticker: str = "WIDG") -> SecurityTrade:
    return trade("0", ticker, quantity, date=on, source_file=f"ibkr {on:%Y-%m}.csv")


def test_holdings_follow_the_trades_between_two_statements(conn: sqlite3.Connection) -> None:
    stage(conn, AUGUST, ibkr(AUGUST, {"WIDG": "10"}))
    staged = stage(conn, SEPTEMBER, ibkr(SEPTEMBER, {"WIDG": "14"}, buy("4", day(15))))
    assert staged.gaps == ()
    [held] = holdings.held(conn)
    assert held.opening == 10
    assert [held.on(d) for d in (day(31, 8), day(14), day(15), day(30))] == [10, 10, 14, 14]
    assert holdings.check(conn, store.owner_id(conn, OWNER) or 0) == []


def test_a_statement_that_does_not_follow_blocks_the_commit(conn: sqlite3.Connection) -> None:
    stage(conn, AUGUST, ibkr(AUGUST, {"WIDG": "10"}))
    staged = stage(conn, SEPTEMBER, ibkr(SEPTEMBER, {"WIDG": "20"}, buy("4", day(15))))
    assert staged.gaps == (
        "IBKR USD: the statement says 20 WIDG were held on 2026-09-30, but the holdings before "
        "it and what was booked since give 14 (a statement in between is missing, or a split "
        "is not stored)",
    )
    owner = store.owner_id(conn, OWNER) or 0
    [refused] = monthclose.would_refuse(conn, owner)
    assert isinstance(refused, HoldingsMismatchError)
    assert (refused.account, refused.symbol, refused.day) == ("IBKR USD", "WIDG", day(30))
    assert (refused.expected, refused.actual) == (D(20), D(14))
    with pytest.raises(HoldingsMismatchError):
        monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)
    assert store.month_status(conn, owner, SEPTEMBER) == "open"


def test_an_instrument_a_statement_does_not_list_is_not_held(conn: sqlite3.Connection) -> None:
    stage(conn, AUGUST, ibkr(AUGUST, {"WIDG": "10"}))
    # Sold in September: the statement lists nothing, and that agrees.
    assert stage(conn, SEPTEMBER, ibkr(SEPTEMBER, {}, buy("-10", day(15)))).gaps == ()
    # Listing nothing while 10 are still held does not.
    [gap] = stage(conn, SEPTEMBER, ibkr(SEPTEMBER, {})).gaps
    assert "says 0 WIDG were held on 2026-09-30" in gap


def test_a_statement_that_prints_no_holdings_says_nothing(conn: sqlite3.Connection) -> None:
    stage(conn, AUGUST, ibkr(AUGUST, {"WIDG": "10"}))
    assert stage(conn, SEPTEMBER, ibkr(SEPTEMBER, None)).gaps == ()
    [held] = holdings.held(conn)
    assert held.on(day(30)) == 10


def test_a_quantity_with_unvested_shares_is_no_known_point(conn: sqlite3.Connection) -> None:
    stage(conn, AUGUST, ibkr(AUGUST, {"WIDG": "3"}, unvested=True))
    staged = stage(conn, SEPTEMBER, ibkr(SEPTEMBER, {"WIDG": "5"}, buy("5", day(15))))
    assert staged.gaps == ()
    [held] = holdings.held(conn)
    assert (held.opening, held.on(day(31, 8)), held.on(day(30))) == (D(0), D(0), D(5))
    assert conn.execute("SELECT sum(unvested) FROM positions").fetchone()[0] == 1


def test_a_stored_split_is_applied_to_what_is_held(conn: sqlite3.Connection) -> None:
    stage(conn, AUGUST, ibkr(AUGUST, {"WIDG": "10"}))
    assert len(stage(conn, SEPTEMBER, ibkr(SEPTEMBER, {"WIDG": "40"})).gaps) == 1
    instrument = conn.execute("SELECT id FROM instruments").fetchone()[0]
    conn.execute("INSERT INTO splits VALUES (?, '2026-09-10', 4, 1)", (instrument,))
    assert holdings.check(conn, store.owner_id(conn, OWNER) or 0) == []
    [held] = holdings.held(conn)
    assert [held.on(d) for d in (day(9), day(10))] == [10, 40]


def test_only_committed_months_are_read_for_the_dashboards(conn: sqlite3.Connection) -> None:
    stage(conn, AUGUST, ibkr(AUGUST, {"WIDG": "10"}))
    stage(conn, SEPTEMBER, ibkr(SEPTEMBER, {"WIDG": "14"}, buy("4", day(15))))
    assert holdings.held(conn, committed=True) == []
    monthclose.commit(conn, owner=OWNER, month=AUGUST, now=NOW)
    [held] = holdings.held(conn, committed=True)
    assert held.on(day(30)) == 10  # September is not committed: neither its trade nor its statement
    monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)
    [held] = holdings.held(conn, committed=True)
    assert held.on(day(30)) == 14


def test_each_person_has_holdings_of_their_own(conn: sqlite3.Connection) -> None:
    stage(conn, SEPTEMBER, ibkr(SEPTEMBER, {"WIDG": "14"}, buy("4", day(15))))
    other = batch(ibkr(OCTOBER, {"WIDG": "1"}), owner="Anna", month=OCTOBER)
    monthclose.stage(conn, other, NOW)
    assert len(holdings.held(conn)) == 2
    mine = store.owner_id(conn, OWNER)
    [held] = holdings.held(conn, owner_id=mine)
    assert held.on(day(30)) == 14


def test_importing_a_statement_again_replaces_what_it_printed(conn: sqlite3.Connection) -> None:
    stage(conn, SEPTEMBER, ibkr(SEPTEMBER, {"WIDG": "14", "ACME": "2"}))
    again = ibkr(SEPTEMBER, {"WIDG": "15"})
    again = replace(again, sha256="another export")
    stage(conn, SEPTEMBER, again)
    printed = conn.execute(
        "SELECT i.symbol, p.quantity, p.price, p.value_nano, p.date FROM positions p "
        "JOIN instruments i ON i.id = p.instrument_id"
    ).fetchall()
    assert [tuple(row) for row in printed] == [("WIDG", "15", "50", 750_000_000_000, "2026-09-30")]
    assert conn.execute("SELECT count(*) FROM statement_files").fetchone()[0] == 1
