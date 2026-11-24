"""What needs a look besides the statements: rows to categorise, and recurring payments that
went up or started lately."""

from dataclasses import replace
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
    """A gym first paid in August, an electricity bill that varies, a cloud plan 1.20 dearer
    since September, a newspaper 50 cents dearer since then, and two rows of Lukas no rule
    matched."""
    gym = _monthly("Gymly", [(2026, m, "-50") for m in (8, 9, 10)], "n")
    bills = ["-61.20", "-58.90", "-63.40", "-60.10", "-64.80", "-64.85"]
    power = _monthly(
        "Powerco", [(2026, m, a) for m, a in zip(range(5, 11), bills, strict=True)], "e"
    )
    cloud = _monthly("Cloudy", [(2026, m, "-6" if m < 9 else "-7.20") for m in range(5, 11)], "c")
    news = _monthly("Newsly", [(2026, m, "-8" if m < 9 else "-8.50") for m in range(5, 11)], "w")
    waiting = [
        txn("-20", date=date(2026, 10, d), category=UNSORTED, source_ref=f"u{d}") for d in (5, 6)
    ]
    return [*gym, *power, *cloud, *news, *waiting]


# Anna's statements end in July: her phone, dearer since June, is news to her still.
HERS = (date(2025, 10, 1), date(2026, 7, 31))


def _hers() -> list[Transaction]:
    phone = _monthly("Phonio", [(2026, m, "-20" if m < 6 else "-23") for m in range(2, 8)], "p")
    waiting = txn("-5", date=date(2026, 7, 7), category=UNSORTED, source_ref="a1")
    return [*phone, waiting]


def build(path: Path, *, rate: bool = True) -> Path:
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    close(conn, OWNER, SPAN, ("migros", [*_rows(), *_extra()]), now=BUILT)
    close(conn, PARTNER, HERS, ("migros", _hers()), now=BUILT)
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
    # The gym started in August; the cloud plan went from 6 to 7.20 in September. The rent
    # never changed, the yearly insurance is not new, and the old gym stopped. Streamio was
    # paid once at its new price, the electricity bill varies, and the newspaper's rise is
    # less than a franc a month: none of them is news.
    assert listed == [
        ("Cloudy", "up", D("1.20"), date(2026, 9, 3)),
        ("Gymly", "new", D("50.00"), date(2026, 8, 3)),
    ]
    assert (found.currency, found.errors) == ("CHF", ())


def test_each_person_s_changes_are_judged_by_their_own_last_day(database: Path) -> None:
    with db.reading(database) as conn:
        found = attention.changes(conn, owner=None, currency="CHF")
    assert [(c.found.owner, c.found.name) for c in found.changes] == [
        (OWNER, "Cloudy"),
        (OWNER, "Gymly"),
        (PARTNER, "Phonio"),
    ]


def test_what_changed_is_converted_at_the_rate_of_the_last_day(database: Path) -> None:
    with db.reading(database) as conn:
        found = attention.changes(conn, owner=None, currency="EUR")
    assert [c.change for c in found.changes] == [D("1.50"), D("62.50"), D("3.75")]


def test_a_missing_rate_leaves_what_changed_unknown(tmp_path: Path) -> None:
    with db.reading(build(tmp_path / "scrooge.db", rate=False)) as conn:
        found = attention.changes(conn, owner=OWNER, currency="EUR")
    # Without a rate the size of a rise cannot be judged: its share of the price is enough.
    assert [(c.found.name, c.change) for c in found.changes] == [
        ("Newsly", None),
        ("Cloudy", None),
        ("Gymly", None),
    ]
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


JULY = date(2026, 7, 10)


@pytest.mark.parametrize(
    ("amounts", "expected"),
    [
        (["-10", "-10", "-10", "-10"], None),  # never changed
        (["-10", "-12", "-12", "-12"], None),  # a rise of April
        (["-12", "-12", "-12", "-10"], None),  # cheaper
        (["-10", "-10", "-12", "-12"], ("up", D(2), date(2026, 6, 3))),
        (["-10", "-11", "-12", "-12"], ("up", D(1), date(2026, 6, 3))),
        (["-10", "-10", "-10", "-12"], None),  # paid once at the new price: a bill may vary
        (["-61.20", "-63.40", "-60.10", "-64.85"], None),  # a bill that varies
        (["-100", "-100", "-102", "-102"], None),  # less than 5%
        (["-10", "-10", "-10"], ("new", D(10), date(2026, 4, 3))),
    ],
)
def test_a_change_is_a_new_price_of_the_last_two_months_or_a_start_in_the_last_three(
    amounts: list[str], expected: tuple[str, Decimal, date] | None
) -> None:
    through = JULY if len(amounts) > 3 else date(2026, 6, 10)
    assert attention._changed(_recurrence(amounts, through=through), through) == expected


def test_a_yearly_rise_counts_from_its_first_payment() -> None:
    rows = ["-100", "-100", "-100", "-110"]
    yearly = replace(_recurrence(rows, through=JULY), every="yearly")
    assert attention._changed(yearly, JULY) == ("up", D(10), date(2026, 7, 3))


def test_months_before_keeps_the_day_or_takes_the_last_of_a_shorter_month() -> None:
    assert attention.months_before(date(2026, 10, 25), 2) == date(2026, 8, 25)
    assert attention.months_before(date(2026, 4, 30), 2) == date(2026, 2, 28)
    assert attention.months_before(date(2026, 1, 15), 3) == date(2025, 10, 15)
