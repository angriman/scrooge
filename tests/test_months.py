import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest

from finanze import monthclose, months
from finanze.models import CashBalance, Kind
from finanze.storage import db
from tests.storage.helpers import (
    AUGUST,
    CLOSED_AT,
    OCTOBER,
    OWNER,
    PARTNER,
    SEPTEMBER,
    batch,
    close,
    migros,
    statement,
    txn,
)

EUR = {"account": "Revolut EUR", "currency": "EUR", "source_file": "revolut.tsv"}
LUKAS = [
    txn("-45.20", date=date(2026, 8, 3), source_ref="1"),
    txn("6000", date=date(2026, 8, 25), source_ref="2", kind=Kind.INCOME, category="Stipendio"),
    txn("-80", date=date(2026, 9, 5), source_ref="3", category=""),
    # Bought on the last day of August, booked in September.
    txn("-15", date=date(2026, 8, 31), booking_date=date(2026, 9, 2), source_ref="4"),
    txn("-70", date=date(2026, 9, 8), source_ref="5", kind=Kind.INTERNAL, category=""),
    txn("-20", date=date(2026, 9, 9), source_ref="6", category="", counterparty="Kiosk"),
]
LUKAS_EUR = [txn("-30", date=date(2026, 8, 5), source_ref="1", category="Ristoranti", **EUR)]


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """Lukas: August committed, September open with two rows to categorize. Anna: August open."""
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    span = (AUGUST, date(2026, 9, 30))
    close(connection, OWNER, span, AUGUST, ("migros", LUKAS), ("revolut", LUKAS_EUR))
    monthclose.stage(connection, batch(migros("0", "-60", month=AUGUST), owner=PARTNER), CLOSED_AT)
    yield connection
    connection.close()


def test_months_are_listed_newest_first_with_what_they_hold(conn: sqlite3.Connection) -> None:
    assert months.listing(conn) == (
        months.MonthLine(OWNER, SEPTEMBER, False, 2, 4, 2),
        months.MonthLine(PARTNER, AUGUST, False, 1, 1, 0),
        months.MonthLine(OWNER, AUGUST, True, 2, 3, 0),
    )
    assert [line.month for line in months.listing(conn, PARTNER)] == [AUGUST]


def test_an_open_month_lists_its_statements_and_what_needs_a_category(
    conn: sqlite3.Connection,
) -> None:
    september = months.checklist(conn, OWNER, SEPTEMBER)
    assert (september.committed, september.committed_at) == (False, None)
    assert september.files == (
        months.File("migros", "migros.csv", AUGUST, date(2026, 9, 30), True, 4),
        months.File("revolut", "revolut.tsv", AUGUST, date(2026, 9, 30), True, 0),
    )
    first, second = september.uncategorized
    assert first == months.Pending(
        first.id, date(2026, 9, 5), "Migros", Decimal(-80), "CHF", "", "KONSUM"
    )
    assert (second.date, second.amount, second.counterparty) == (
        date(2026, 9, 9),
        Decimal(-20),
        "Kiosk",
    )
    assert first.id != second.id
    assert september.totals == (months.CategoryTotal("uscite", "Spesa", "CHF", 1, Decimal(-15)),)
    assert september.blocked_by == ("2 transactions still need a category",)


def test_a_committed_month_says_when_and_adds_up_by_booking_date(conn: sqlite3.Connection) -> None:
    august = months.checklist(conn, OWNER, AUGUST)
    assert (august.committed, august.committed_at) == (True, "2026-10-08T12:00:00+00:00")
    assert august.totals == (
        months.CategoryTotal("uscite", "Spesa", "CHF", 1, Decimal("-45.2")),
        months.CategoryTotal("uscite", "Ristoranti", "EUR", 1, Decimal(-30)),
        months.CategoryTotal("entrate", "Stipendio", "CHF", 1, Decimal(6000)),
    )
    assert august.uncategorized == ()
    assert august.blocked_by == ()
    assert august.missing == ()


def test_a_source_seen_the_month_before_and_absent_now_is_missing(
    conn: sqlite3.Connection,
) -> None:
    card = statement(
        "swisscard",
        [txn("-10", date=date(2026, 10, 3), account="Swisscard", source_file="card.pdf")],
        filename="card.pdf",
        month=OCTOBER,
    )
    monthclose.stage(conn, batch(card, month=OCTOBER), CLOSED_AT)
    october = months.checklist(conn, OWNER, OCTOBER)
    assert [file.source for file in october.files] == ["swisscard"]
    assert october.files[0].checked is False
    assert october.missing == ("migros", "revolut")
    assert months.checklist(conn, OWNER, SEPTEMBER).missing == ()


def test_one_row_to_categorize_is_said_in_the_singular(conn: sqlite3.Connection) -> None:
    row = txn("-5", date=date(2026, 9, 3), category="")
    lone = statement("migros", [row], [CashBalance("Migros", "CHF", Decimal(-60), Decimal(-65))])
    monthclose.stage(conn, batch(lone, owner=PARTNER), CLOSED_AT)
    september = months.checklist(conn, PARTNER, SEPTEMBER)
    assert september.blocked_by == ("1 transaction still needs a category",)


def test_a_missing_statement_blocks_and_is_said(conn: sqlite3.Connection) -> None:
    # Anna's bank closed August at -60; a statement for October that starts from 100.
    later = statement(
        "migros",
        [txn("-1", date=date(2026, 10, 3))],
        [CashBalance("Migros", "CHF", Decimal(100), Decimal(99))],
        month=OCTOBER,
    )
    monthclose.stage(conn, batch(later, owner=PARTNER, month=OCTOBER), CLOSED_AT)
    (gap,) = months.checklist(conn, PARTNER, OCTOBER).gaps
    assert gap.startswith("Migros (CHF): no statement covers the days after 2026-08-31")
    assert months.checklist(conn, PARTNER, AUGUST).blocked_by == (gap,)


def test_statements_that_disagree_are_said_too(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE balances SET amount_nano = amount_nano + 1 WHERE date = '2026-09-30'")
    (gap,) = months.checklist(conn, OWNER, SEPTEMBER).gaps
    assert "Migros" in gap or "Revolut" in gap


@pytest.mark.parametrize(("owner", "month"), [("Zoe", AUGUST), (PARTNER, date(2020, 1, 1))])
def test_a_month_nobody_staged_is_refused(
    conn: sqlite3.Connection, owner: str, month: date
) -> None:
    with pytest.raises(monthclose.NothingStagedError, match=f"nothing is staged for {owner}"):
        months.checklist(conn, owner, month)


def test_waiting_rows_are_grouped_by_counterparty_and_direction(conn: sqlite3.Connection) -> None:
    card_row = {"category": "", "account": "Swisscard", "source_file": "card.pdf"}
    more = [
        txn("-80", date=date(2026, 10, 5), source_ref="1", **card_row),
        txn("-7", date=date(2026, 10, 6), source_ref="2", counterparty=" Kiosk ", **card_row),
        txn("3", date=date(2026, 10, 7), source_ref="3", kind=Kind.INCOME, **card_row),
        txn("-9", date=date(2026, 10, 8), source_ref="4", counterparty="Kiosk", **card_row),
    ]
    card = statement("swisscard", more, filename="card.pdf", month=OCTOBER)
    monthclose.stage(conn, batch(card, month=OCTOBER), CLOSED_AT)
    groups = months.checklist(conn, OWNER, OCTOBER).groups
    found = [(g.name, g.incoming, [str(row.amount) for row in g.rows]) for g in groups]
    assert found == [
        ("KONSUM", False, ["-80"]),
        ("Kiosk", False, ["-7", "-9"]),
        ("KONSUM", True, ["3"]),
    ]
