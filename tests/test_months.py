import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest

from scrooge import months, staging
from scrooge.models import CashBalance, Kind
from scrooge.router import LABELS
from scrooge.storage import db
from tests.storage.helpers import (
    AUGUST,
    CLOSED_AT,
    OCTOBER,
    OWNER,
    PARTNER,
    SEPTEMBER,
    batch,
    bill,
    certificate,
    certified,
    migros,
    statement,
    tastytrade,
    txn,
)

EUR = {"account": "Revolut EUR", "currency": "EUR", "source_file": "revolut.tsv"}
SEP = {"source_file": "migros sep.csv"}
AUGUST_ROWS = [
    txn("-45.20", date=date(2026, 8, 3), source_ref="1"),
    txn("6000", date=date(2026, 8, 25), source_ref="2", kind=Kind.INCOME, category="Stipendio"),
]
SEPTEMBER_ROWS = [
    txn("-80", date=date(2026, 9, 5), source_ref="3", category="", **SEP),
    # Bought on the last day of August, booked in September.
    txn("-15", date=date(2026, 8, 31), booking_date=date(2026, 9, 2), source_ref="4", **SEP),
    txn("-70", date=date(2026, 9, 8), source_ref="5", kind=Kind.INTERNAL, category="", **SEP),
    txn("-20", date=date(2026, 9, 9), source_ref="6", category="", counterparty="Kiosk", **SEP),
]
LUKAS_EUR = [txn("-30", date=date(2026, 8, 5), source_ref="1", category="Ristoranti e bar", **EUR)]
SEPTEMBER_FILE = 3


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """Lukas: August committed, September staged with two rows to categorize.
    Anna: August staged."""
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    august = statement(
        "migros",
        AUGUST_ROWS,
        [CashBalance("Migros", "CHF", Decimal(0), Decimal("5954.80"))],
        month=AUGUST,
    )
    revolut = statement(
        "revolut",
        LUKAS_EUR,
        [CashBalance("Revolut EUR", "EUR", Decimal(0), Decimal(-30))],
        month=AUGUST,
    )
    staging.stage(connection, batch(august, revolut, month=AUGUST), CLOSED_AT)
    staging.commit(connection, owner=OWNER, now=CLOSED_AT)
    balance = CashBalance("Migros", "CHF", Decimal("5954.80"), Decimal("5769.80"))
    september = statement("migros", SEPTEMBER_ROWS, [balance], month=SEPTEMBER)
    staging.stage(connection, batch(september), CLOSED_AT)
    staging.stage(connection, batch(migros("0", "-60", month=AUGUST), owner=PARTNER), CLOSED_AT)
    yield connection
    connection.close()


def test_months_that_committed_statements_cover_are_listed_newest_first(
    conn: sqlite3.Connection,
) -> None:
    assert months.listing(conn) == (months.MonthLine(OWNER, AUGUST, 2, 3),)
    assert months.listing(conn, PARTNER) == ()
    staging.commit(conn, owner=PARTNER, now=CLOSED_AT)
    assert [line.month for line in months.listing(conn, PARTNER)] == [AUGUST]
    # The rest of September is committed once the person categorizes it.
    conn.execute("UPDATE transactions SET category_id = 1, category_source = 'manual'")
    staging.commit(conn, owner=OWNER, now=CLOSED_AT)
    assert months.listing(conn) == (
        months.MonthLine(OWNER, SEPTEMBER, 1, 4),
        months.MonthLine(PARTNER, AUGUST, 1, 1),
        months.MonthLine(OWNER, AUGUST, 2, 3),
    )


def test_a_draft_lists_the_staged_statements_and_what_needs_a_category(
    conn: sqlite3.Connection,
) -> None:
    draft = months.draft(conn, OWNER)
    assert draft.files == (
        months.File(
            SEPTEMBER_FILE,
            "migros",
            "migros sep.csv",
            SEPTEMBER,
            date(2026, 9, 30),
            True,
            4,
            committed=False,
        ),
    )
    first, second = draft.uncategorized
    assert first == months.Pending(
        first.id, date(2026, 9, 5), "Migros", Decimal(-80), "CHF", "", "KONSUM", statement=3
    )
    assert (second.date, second.amount, second.counterparty) == (
        date(2026, 9, 9),
        Decimal(-20),
        "Kiosk",
    )
    assert first.waiting
    assert first.id != second.id
    assert len(draft.rows) == 3  # not the leg of the card bill, which no category is asked for
    assert draft.totals == (
        months.CategoryTotal("uscite", "Spesa", "CHF", 1, Decimal(-15)),
        months.CategoryTotal("", "", "CHF", 2, Decimal(-100)),  # the waiting rows last
    )
    assert draft.gaps == ()
    assert draft.blocked_by == ("2 transactions still need a category",)


def test_a_draft_of_nobody_or_of_nothing_staged_is_empty(conn: sqlite3.Connection) -> None:
    staging.commit(conn, owner=PARTNER, now=CLOSED_AT)
    for owner in (PARTNER, "Zoe"):
        draft = months.draft(conn, owner)
        assert (draft.files, draft.rows, draft.totals, draft.gaps) == ((), (), (), ())
        assert draft.blocked_by == ()


def test_one_row_to_categorize_is_said_in_the_singular(conn: sqlite3.Connection) -> None:
    row = txn("-5", date=date(2026, 9, 3), category="")
    lone = statement("migros", [row], [CashBalance("Migros", "CHF", Decimal(-60), Decimal(-65))])
    staging.stage(conn, batch(lone, owner=PARTNER), CLOSED_AT)
    assert months.draft(conn, PARTNER).blocked_by == ("1 transaction still needs a category",)


def test_coverage_says_how_far_each_account_is_committed_and_staged(
    conn: sqlite3.Connection,
) -> None:
    covered = months.coverage(conn)
    assert covered == (
        months.Coverage(PARTNER, "Migros", "migros", None, date(2026, 8, 31)),
        months.Coverage(OWNER, "Migros", "migros", date(2026, 8, 31), date(2026, 9, 30)),
        months.Coverage(OWNER, "Revolut EUR", "revolut", date(2026, 8, 31), None),
    )
    assert months.coverage(conn, OWNER) == covered[1:]
    assert [line.label for line in covered[1:]] == [LABELS["migros"], LABELS["revolut"]]
    staging.commit(conn, owner=PARTNER, now=CLOSED_AT)
    assert months.coverage(conn, PARTNER) == (
        months.Coverage(PARTNER, "Migros", "migros", date(2026, 8, 31), None),
    )


def test_the_timeline_has_the_days_each_account_has_statements_for(
    conn: sqlite3.Connection,
) -> None:
    august = (date(2026, 8, 1), date(2026, 8, 31))
    tracks = months.timeline(conn)
    assert [(t.owner, t.account, t.label) for t in tracks] == [
        (PARTNER, "Migros", LABELS["migros"]),
        (OWNER, "Migros", LABELS["migros"]),
        (OWNER, "Revolut EUR", LABELS["revolut"]),
    ]
    assert tracks[0].spans == (months.Span(*august, False),)
    assert tracks[1].spans == (
        months.Span(date(2026, 9, 1), date(2026, 9, 30), False),
        months.Span(*august, True),
    )
    assert tracks[2].spans == (months.Span(*august, True),)
    assert months.timeline(conn, OWNER) == tracks[1:]


def test_statements_that_overlap_or_follow_on_are_one_run_and_a_gap_is_not() -> None:
    d = date
    joined = months._joined(
        [(d(2026, 3, 1), d(2026, 3, 31)), (d(2026, 1, 1), d(2026, 1, 31)),
         (d(2026, 2, 1), d(2026, 2, 10)), (d(2026, 2, 5), d(2026, 2, 28)),
         (d(2026, 5, 1), d(2026, 5, 31))],
        True,
    )  # fmt: skip
    assert joined == [
        months.Span(d(2026, 1, 1), d(2026, 3, 31), True),
        months.Span(d(2026, 5, 1), d(2026, 5, 31), True),
    ]


def test_a_month_adds_up_what_is_committed_by_booking_date(conn: sqlite3.Connection) -> None:
    august = months.checklist(conn, OWNER, AUGUST)
    assert august.files == (
        months.File(1, "migros", "migros.csv", AUGUST, date(2026, 8, 31), True, 2),
        months.File(2, "revolut", "revolut.tsv", AUGUST, date(2026, 8, 31), True, 1),
    )
    assert august.totals == (
        months.CategoryTotal("uscite", "Spesa", "CHF", 1, Decimal("-45.2")),
        months.CategoryTotal("uscite", "Ristoranti e bar", "EUR", 1, Decimal(-30)),
        months.CategoryTotal("entrate", "Stipendio", "CHF", 1, Decimal(6000)),
    )
    assert [row.category for row in august.categorized] == [
        "Spesa",
        "Ristoranti e bar",
        "Stipendio",
    ]
    assert all(row.changeable and not row.waiting for row in august.categorized)
    # September is staged, not committed: the month shows nothing of it yet.
    september = months.checklist(conn, OWNER, SEPTEMBER)
    assert (september.files, september.totals, september.categorized) == ((), (), ())


def test_the_rows_of_a_month_are_grouped_per_counterparty_direction_and_category(
    conn: sqlite3.Connection,
) -> None:
    groups = months.checklist(conn, OWNER, AUGUST).changeable
    found = [(g.name, g.incoming, g.category, [str(row.amount) for row in g.rows]) for g in groups]
    assert found == [
        ("KONSUM", False, "Spesa", ["-45.2"]),
        ("KONSUM", False, "Ristoranti e bar", ["-30"]),
        ("KONSUM", True, "Stipendio", ["6000"]),
    ]


def test_a_month_of_nobody_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(staging.NothingStagedError, match="nobody called Zoe"):
        months.checklist(conn, "Zoe", AUGUST)
    quiet = months.checklist(conn, PARTNER, date(2020, 1, 1))
    assert (quiet.files, quiet.totals) == ((), ())


def test_a_missing_statement_is_said_and_blocks(conn: sqlite3.Connection) -> None:
    # Anna's bank closed August at -60; a statement for October that starts from 100.
    later = statement(
        "migros",
        [txn("-1", date=date(2026, 10, 3))],
        [CashBalance("Migros", "CHF", Decimal(100), Decimal(99))],
        month=OCTOBER,
    )
    staging.stage(conn, batch(later, owner=PARTNER, month=OCTOBER), CLOSED_AT)
    (gap,) = months.draft(conn, PARTNER).gaps
    assert gap.startswith("Migros (CHF): no statement covers the days after 2026-08-31")
    assert months.draft(conn, PARTNER).blocked_by == (gap,)


def test_statements_that_disagree_are_said_too(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE balances SET amount_nano = amount_nano + 1 WHERE date = '2026-09-30'")
    (gap,) = months.draft(conn, OWNER).gaps
    assert "Migros" in gap


def test_waiting_rows_are_grouped_by_counterparty_and_direction(conn: sqlite3.Connection) -> None:
    card_row = {"category": "", "account": "Swisscard", "source_file": "card.pdf"}
    more = [
        txn("-80", date=date(2026, 10, 5), source_ref="1", **card_row),
        txn("-7", date=date(2026, 10, 6), source_ref="2", counterparty=" Kiosk ", **card_row),
        txn("3", date=date(2026, 10, 7), source_ref="3", kind=Kind.INCOME, **card_row),
        txn("-9", date=date(2026, 10, 8), source_ref="4", counterparty="Kiosk", **card_row),
    ]
    card = statement("swisscard", more, filename="card.pdf", month=OCTOBER)
    staging.stage(conn, batch(card, month=OCTOBER), CLOSED_AT)
    found = [
        (g.name, g.incoming, [str(row.amount) for row in g.rows])
        for g in months.draft(conn, OWNER).groups
    ]
    assert found == [
        ("KONSUM", False, ["-80", "-80"]),
        ("Kiosk", False, ["-20", "-7", "-9"]),
        ("KONSUM", True, ["3"]),
    ]


def test_a_bill_without_its_purchase_blocks_and_is_said(conn: sqlite3.Connection) -> None:
    sold = bill("10000", "-10000", date(2026, 9, 16), kind=Kind.INCOME)
    staging.stage(conn, tastytrade(sold), CLOSED_AT)
    draft = months.draft(conn, OWNER)
    (reason,) = [gap for gap in draft.gaps if "912797YY2" in gap]
    assert reason.startswith("Tastytrade: 912797YY2 matured or was sold on 2026-09-16")
    assert reason in draft.blocked_by


def test_a_pension_certificate_is_a_statement_of_its_day(conn: sqlite3.Connection) -> None:
    before = months.draft(conn, OWNER)
    scan = certificate(date(2026, 9, 1), "35802.00", filename="scan.pdf")
    staging.stage(conn, certified(scan), CLOSED_AT)
    draft = months.draft(conn, OWNER)
    assert draft.files == (
        months.File(5, "axa", "scan.pdf", SEPTEMBER, SEPTEMBER, True, 0, committed=False),
        *before.files,
    )
    assert draft.files[0].label == LABELS["axa"]
    assert (draft.gaps, draft.blocked_by) == (before.gaps, before.blocked_by)
    assert months.listing(conn, OWNER) == (months.MonthLine(OWNER, AUGUST, 2, 3),)


def test_a_statement_says_whether_it_is_committed_and_what_holds_it(
    conn: sqlite3.Connection,
) -> None:
    assert months.statement(conn, 99) is None
    staged = months.statement(conn, SEPTEMBER_FILE)
    assert staged is not None
    assert (staged.committed_at, staged.held_by, staged.transactions) == (None, (), 4)
    assert (staged.label, staged.months) == (LABELS["migros"], (SEPTEMBER,))
    committed = months.statement(conn, 1)
    assert committed is not None
    assert committed.committed_at == "2026-12-20T12:00:00+00:00"
    assert committed.held_by == ()  # nothing committed lies after it
