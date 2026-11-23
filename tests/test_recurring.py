"""The recurring payments of the cash-flow page and what they cost a month."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from scrooge import recurring
from scrooge.models import Kind, Transaction
from scrooge.storage import db, store
from tests.storage.helpers import OWNER, txn
from tests.web.builders import close

D = Decimal
BUILT = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
SPAN = (date(2025, 10, 1), date(2026, 10, 31))


def _monthly(name: str, amounts: list[tuple[int, int, str]], ref: str) -> list[Transaction]:
    return [
        txn(amount, date=date(year, month, 3), counterparty=name, source_ref=f"{ref}{n}")
        for n, (year, month, amount) in enumerate(amounts)
    ]


def _rows() -> list[Transaction]:
    months = [(2026, m) for m in range(5, 11)]
    rent = _monthly("Landlord AG, Bern", [(y, m, "-1500") for y, m in months], "r")
    stream = _monthly("Streamio 123", [(y, m, "-10" if m < 9 else "-12") for y, m in months], "s")
    gym = _monthly("Gym", [(2026, m, "-80") for m in (3, 4, 5, 6)], "g")
    salary = [
        txn("5000", date=date(y, m, 25), counterparty="ACME", kind=Kind.INCOME, source_ref=f"p{m}")
        for y, m in months
    ]
    insurance = [
        txn("-600", date=date(y, 10, 1), counterparty="Insurer", source_ref=f"i{y}")
        for y in (2025, 2026)
    ]
    return [*rent, *stream, *gym, *salary, *insurance]


def build(path: Path, *, rate: bool) -> Path:
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    close(conn, OWNER, SPAN, ("migros", _rows()), now=BUILT)
    if rate:
        store.set_rate(conn, date(2026, 10, 1), "EUR", D("0.80"), source="fetched:yahoo")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return build(tmp_path / "scrooge.db", rate=True)


def test_active_payments_come_most_a_month_first_and_ended_ones_apart(database: Path) -> None:
    with db.reading(database) as conn:
        found = recurring.run(conn, owner=None, currency="CHF")
    assert [(p.found.name, p.monthly) for p in found.active] == [
        ("Landlord AG", D(1500)),
        ("Insurer", D(50)),
        ("Streamio", D(12)),
    ]
    assert [p.found.name for p in found.ended] == ["Gym"]
    assert found.monthly == D(1562)
    assert found.errors == ()
    stream = found.active[2].found
    assert (stream.previous, stream.next, len(stream.rows)) == (D(-10), date(2026, 11, 3), 6)
    insurance = found.active[1].found
    assert (insurance.every, insurance.previous, insurance.next) == (
        "yearly",
        None,
        date(2027, 10, 1),
    )


def test_transfers_and_what_the_two_pay_each_other_are_not_payments(tmp_path: Path) -> None:
    months = [(2026, m) for m in range(5, 11)]
    broker = [
        txn(
            "-1000",
            date=date(y, m, 5),
            counterparty="Broker",
            kind=Kind.TRANSFER,
            category="Topup IBKR",
            source_ref=f"b{m}",
        )
        for y, m in months
    ]
    rows = [
        *broker,
        *_monthly("Anna Meier", [(y, m, "-300") for y, m in months], "a"),
        *_monthly("Landlord AG, Bern", [(y, m, "-1500") for y, m in months], "r"),
    ]
    conn = db.connect(tmp_path / "scrooge.db")
    db.migrate(conn, BUILT)
    close(conn, OWNER, SPAN, ("migros", rows), now=BUILT)
    conn.commit()
    conn.close()
    with db.reading(tmp_path / "scrooge.db") as conn:
        everyone = recurring.run(conn, owner=None, currency="CHF")
        lukas = recurring.run(conn, owner=OWNER, currency="CHF")
    assert [p.found.name for p in everyone.active] == ["Landlord AG"]
    assert everyone.monthly == D(1500)
    # In one person's view, what they pay the other is money spent, as cash flow counts it.
    assert [p.found.name for p in lukas.active] == ["Landlord AG", "Anna Meier"]


def test_a_currency_without_a_rate_leaves_its_cost_and_the_total_unknown(tmp_path: Path) -> None:
    with db.reading(build(tmp_path / "scrooge.db", rate=False)) as conn:
        found = recurring.run(conn, owner=OWNER, currency="EUR")
    assert [p.monthly for p in found.active] == [None, None, None]
    assert found.monthly is None
    assert len(found.errors) == 1


def test_costs_are_converted_at_the_rate_of_the_last_day(database: Path) -> None:
    with db.reading(database) as conn:
        found = recurring.run(conn, owner=OWNER, currency="EUR")
    assert found.monthly == D(1562) / D("0.80")


def test_nobody_s_payments_are_empty(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    conn.close()
    with db.reading(path) as conn:
        found = recurring.run(conn, owner=None, currency="CHF")
    assert (found.active, found.ended, found.monthly) == ((), (), D(0))
