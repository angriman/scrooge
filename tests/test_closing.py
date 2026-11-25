"""Closing an account: refused unless it held nothing, and nothing after the day is let in."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from scrooge import closing, months, staging
from scrooge.models import CashBalance, TypedHolding
from scrooge.storage import db, store
from tests.storage.helpers import (
    OCTOBER,
    OWNER,
    PARTNER,
    batch,
    certificate,
    certified,
    migros,
    rows,
    statement,
    txn,
)

NOW = datetime(2026, 12, 20, 12, 0, tzinfo=UTC)
END = date(2026, 9, 30)  # the last day of the September statements


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def _emptied(conn: sqlite3.Connection, *amounts: str) -> None:
    """Migros, committed for September: it opens with 100 and books `amounts` on the 10th."""
    staging.stage(conn, batch(migros("100", *amounts)), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)


def _closed(conn: sqlite3.Connection, owner: str = OWNER) -> list[tuple[str, date | None]]:
    return [(line.account, line.closed) for line in months.coverage(conn, owner)]


def test_an_account_that_holds_nothing_is_closed_and_keeps_its_history(
    conn: sqlite3.Connection,
) -> None:
    _emptied(conn, "-100")
    before = rows(conn, "SELECT id, amount_nano FROM transactions ORDER BY id")
    closing.close(conn, owner=OWNER, account="Migros", day=END)
    assert _closed(conn) == [("Migros", END)]
    assert rows(conn, "SELECT id, amount_nano FROM transactions ORDER BY id") == before
    # closing it again moves the day: any day it held nothing at the end of
    closing.close(conn, owner=OWNER, account="Migros", day=date(2026, 9, 20))
    assert _closed(conn) == [("Migros", date(2026, 9, 20))]
    assert closing.reopen(conn, owner=OWNER, account="Migros") == date(2026, 9, 20)
    assert _closed(conn) == [("Migros", None)]


def test_an_account_that_held_money_on_the_day_is_not_closed(conn: sqlite3.Connection) -> None:
    _emptied(conn, "-1")
    with pytest.raises(closing.ClosingError, match=r"held 99\.00 CHF at the end of 2026-09-30"):
        closing.close(conn, owner=OWNER, account="Migros", day=END)
    assert _closed(conn) == [("Migros", None)]  # nothing was kept of it


def test_an_account_before_its_first_day_holds_nothing(conn: sqlite3.Connection) -> None:
    _emptied(conn, "-100")
    with pytest.raises(
        staging.ClosedAccountError,
        match="after it rows are booked up to 2026-09-10: close it on a later day",
    ):
        closing.close(conn, owner=OWNER, account="Migros", day=date(2026, 8, 1))


def test_only_an_account_of_the_person_with_nothing_staged_is_closed(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(closing.ClosingError, match="Lukas has no account 'Migros'"):
        closing.close(conn, owner=OWNER, account="Migros", day=END)
    staging.stage(conn, batch(migros("100", "-100")), NOW)
    with pytest.raises(closing.ClosingError, match="Anna has no account 'Migros'"):
        closing.close(conn, owner=PARTNER, account="Migros", day=END)
    with pytest.raises(closing.ClosingError, match="has statements staged: commit or discard"):
        closing.close(conn, owner=OWNER, account="Migros", day=END)
    with pytest.raises(closing.ClosingError, match="Migros of Lukas is not closed"):
        closing.reopen(conn, owner=OWNER, account="Migros")


def test_a_statement_with_rows_after_the_day_is_refused_until_it_is_open_again(
    conn: sqlite3.Connection,
) -> None:
    _emptied(conn, "-100")
    closing.close(conn, owner=OWNER, account="Migros", day=END)
    later = batch(migros("0", "5", month=OCTOBER), month=OCTOBER)
    with pytest.raises(
        staging.ClosedAccountError,
        match=r"Migros was closed on 2026-09-30, and after it rows are booked up to 2026-10-10: "
        r"open it again \('scrooge close --reopen'\)",
    ):
        staging.stage(conn, later, NOW)
    assert rows(conn, "SELECT count(*) FROM statement_files") == [(1,)]  # nothing was staged
    closing.reopen(conn, owner=OWNER, account="Migros")
    staging.stage(conn, later, NOW)
    assert rows(conn, "SELECT count(*) FROM statement_files") == [(2,)]


def test_a_later_statement_that_says_it_still_holds_nothing_is_let_in(
    conn: sqlite3.Connection,
) -> None:
    _emptied(conn, "-100")
    closing.close(conn, owner=OWNER, account="Migros", day=END)
    empty = CashBalance("Migros", "CHF", Decimal(0), Decimal(0))
    nothing = statement("migros", [], [empty], filename="october.csv", month=OCTOBER)
    staging.stage(conn, batch(nothing, month=OCTOBER), NOW)
    assert rows(conn, "SELECT count(*) FROM statement_files") == [(2,)]


def test_a_pension_fund_left_takes_a_typed_zero_first(conn: sqlite3.Connection) -> None:
    staging.stage(conn, certified(certificate(date(2026, 1, 1), "30000")), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    left = date(2026, 6, 30)
    with pytest.raises(
        closing.ClosingError,
        match=r"held 30,000\.00 CHF at the end of 2026-06-30: close it on a day it held "
        r"nothing, or type the zero its fund shows \('scrooge balance'\)",
    ):
        closing.close(conn, owner=OWNER, account="Pillar 2 AXA", day=left)
    _type(conn, left, "0")
    closing.close(conn, owner=OWNER, account="Pillar 2 AXA", day=left)
    _type(conn, date(2026, 7, 31), "0")  # nothing is still nothing
    with pytest.raises(staging.ClosedAccountError, match=r"it held 5\.00 on 2026-08-31"):
        _type(conn, date(2026, 8, 31), "5")
    with pytest.raises(staging.ClosedAccountError, match=r"it held 6\.00 on 2026-09-01"):
        staging.stage(conn, certified(certificate(date(2026, 9, 1), "6")), NOW)
    stored = rows(conn, "SELECT date, amount_nano FROM balances ORDER BY date")
    assert stored == [("2026-01-01", 30_000_000_000_000), (str(left), 0), ("2026-07-31", 0)]


def test_shares_held_keep_an_account_open_and_none_may_be_typed_after(
    conn: sqlite3.Connection,
) -> None:
    held = TypedHolding("ABCD", Decimal(2), "USD")
    day = date(2026, 3, 1)
    staging.type_holding(conn, owner=OWNER, account="Broker", day=day, holding=held)
    with pytest.raises(closing.ClosingError, match=r"held 2 ABCD at the end of 2026-03-31"):
        closing.close(conn, owner=OWNER, account="Broker", day=date(2026, 3, 31))
    sold = TypedHolding("ABCD", Decimal(0), "USD")
    staging.type_holding(conn, owner=OWNER, account="Broker", day=date(2026, 4, 1), holding=sold)
    closing.close(conn, owner=OWNER, account="Broker", day=date(2026, 4, 1))
    again = TypedHolding("ABCD", Decimal("1.5"), "USD")
    with pytest.raises(staging.ClosedAccountError, match=r"it held 1\.5 ABCD on 2026-05-01"):
        staging.type_holding(
            conn, owner=OWNER, account="Broker", day=date(2026, 5, 1), holding=again
        )
    new = TypedHolding("WXYZ", Decimal(1), "USD")
    with pytest.raises(staging.ClosedAccountError, match="it held 1 WXYZ on 2026-05-01"):
        staging.type_holding(conn, owner=OWNER, account="Broker", day=date(2026, 5, 1), holding=new)
    closed = store.account_of(conn, store.owner_id(conn, OWNER) or 0, "Broker")
    assert closed is not None
    assert closed.closed == date(2026, 4, 1)


def test_the_other_accounts_of_the_person_are_not_held_back(conn: sqlite3.Connection) -> None:
    _emptied(conn, "-100")
    closing.close(conn, owner=OWNER, account="Migros", day=END)
    cash = txn("7", account="Revolut EUR", currency="EUR", date=date(2026, 10, 3))
    revolut = statement(
        "revolut",
        [cash],
        [CashBalance("Revolut EUR", "EUR", Decimal(0), Decimal(7))],
        month=OCTOBER,
    )
    staging.stage(conn, batch(revolut, month=OCTOBER), NOW)
    assert ("Revolut EUR", None) in _closed(conn)


def _type(conn: sqlite3.Connection, day: date, amount: str) -> None:
    staging.type_balance(
        conn, owner=OWNER, account="Pillar 2 AXA", day=day, amount=Decimal(amount), currency="CHF"
    )
