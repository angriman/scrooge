"""What needs a look besides the statements: rows to categorise, and recurring payments that
went up or started lately."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from scrooge import attention, categories, query
from scrooge.models import Kind, Transaction
from scrooge.storage import db, store
from tests.storage.helpers import OWNER, PARTNER, txn
from tests.test_recurring import BUILT, SPAN, _monthly, _rows
from tests.web.builders import close

D = Decimal
UNSORTED = categories.UNSORTED


def _extra() -> list[Transaction]:
    """A gym first paid in August, two rows of Lukas no rule matched, and one of Anna."""
    gym = _monthly("Gymly", [(2026, m, "-50") for m in (8, 9, 10)], "n")
    waiting = [
        txn("-20", date=date(2026, 10, d), category=UNSORTED, source_ref=f"u{d}") for d in (5, 6)
    ]
    return [*gym, *waiting]


def build(path: Path, *, rate: bool = True) -> Path:
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    close(conn, OWNER, SPAN, ("migros", [*_rows(), *_extra()]), now=BUILT)
    hers = txn("-5", date=date(2026, 10, 7), category=UNSORTED, source_ref="a1")
    close(conn, PARTNER, SPAN, ("migros", [hers]), now=BUILT)
    if rate:
        store.set_rate(conn, date(2026, 10, 1), "EUR", D("0.80"), source="fetched:yahoo")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return build(tmp_path / "scrooge.db")


def test_rows_still_unmatched_are_counted_per_person(database: Path) -> None:
    with db.reading(database) as conn:
        assert attention.uncategorised(conn, None, UNSORTED) == (
            attention.Uncategorised(PARTNER, 1),
            attention.Uncategorised(OWNER, 2),
        )
        assert attention.uncategorised(conn, PARTNER, UNSORTED) == (
            attention.Uncategorised(PARTNER, 1),
        )
        # Without `unmatched` every row was given a category before it was committed.
        assert attention.uncategorised(conn, None, "") == ()
        assert attention.uncategorised(conn, None, "Nowhere") == ()


def test_a_rise_lately_and_a_payment_that_started_lately_are_changes(database: Path) -> None:
    with db.reading(database) as conn:
        found = attention.changes(conn, owner=OWNER, currency="CHF")
    listed = [(c.found.name, c.kind, c.change, c.since) for c in found.changes]
    # The gym started in August; Streamio went from 10 to 12 in September. The rent never
    # changed, the yearly insurance is not new, and the old gym stopped.
    assert listed == [
        ("Streamio", "up", D("2.00"), date(2026, 9, 3)),
        ("Gymly", "new", D("50.00"), date(2026, 8, 3)),
    ]
    assert (found.currency, found.errors) == ("CHF", ())


def test_what_changed_is_converted_at_the_rate_of_the_last_day(database: Path) -> None:
    with db.reading(database) as conn:
        found = attention.changes(conn, owner=None, currency="EUR")
    assert [c.change for c in found.changes] == [D("2.50"), D("62.50")]


def test_a_missing_rate_leaves_what_changed_unknown(tmp_path: Path) -> None:
    with db.reading(build(tmp_path / "scrooge.db", rate=False)) as conn:
        found = attention.changes(conn, owner=OWNER, currency="EUR")
    assert [c.change for c in found.changes] == [None, None]
    assert len(found.errors) == 1


def _recurrence(amounts: list[str], *, through: date) -> query.Recurrence:
    rows = [
        query.Row(
            id=str(n),
            owner=OWNER,
            account="Migros",
            date=date(2026, m, 3),
            booking_date=date(2026, m, 3),
            amount=D(amount),
            currency="CHF",
            description="",
            counterparty="Streamio",
            kind=Kind.EXPENSE,
            category="SIM",
            category_parent="Telefonia",
            counterparty_owner=None,
        )
        for n, (m, amount) in enumerate(zip(range(4, 4 + len(amounts)), amounts, strict=True))
    ]
    return query.Recurrence("Streamio", OWNER, "Migros", "monthly", tuple(rows), through)


@pytest.mark.parametrize(
    ("amounts", "through", "expected"),
    [
        (["-10", "-10", "-10", "-10"], date(2026, 7, 10), None),  # never changed
        (["-10", "-12", "-12", "-12"], date(2026, 7, 10), None),  # a rise of April
        (["-12", "-12", "-12", "-10"], date(2026, 7, 10), None),  # cheaper
        (["-10", "-10", "-12", "-12"], date(2026, 7, 10), ("up", D(2), date(2026, 6, 3))),
        (["-10", "-11", "-12", "-12"], date(2026, 7, 10), ("up", D(1), date(2026, 6, 3))),
        (["-10", "-10", "-10"], date(2026, 6, 10), ("new", D(10), date(2026, 4, 3))),
    ],
)
def test_a_change_is_a_rise_in_the_last_two_months_or_a_start_in_the_last_three(
    amounts: list[str], through: date, expected: tuple[str, Decimal, date] | None
) -> None:
    assert attention._changed(_recurrence(amounts, through=through)) == expected


def test_months_before_keeps_the_day_or_takes_the_last_of_a_shorter_month() -> None:
    assert attention.months_before(date(2026, 10, 25), 2) == date(2026, 8, 25)
    assert attention.months_before(date(2026, 4, 30), 2) == date(2026, 2, 28)
    assert attention.months_before(date(2026, 1, 15), 3) == date(2025, 10, 15)
