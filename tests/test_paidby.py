import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest

from scrooge import paidby, report, spending
from scrooge.models import Kind, Transaction
from scrooge.paidby import Payer, Split
from scrooge.storage import db, store
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close, txn

START, END = date(2026, 1, 1), date(2026, 6, 30)
SHOP = "KONSUM ZUERICH"
EUR = {"account": "Revolut EUR", "currency": "EUR", "source_file": "revolut.tsv"}


def monthly(amount: str, day: int, ref: str, **fields: object) -> list[Transaction]:
    return [
        txn(amount, date=date(2026, n + 1, day), source_ref=f"{ref}{n}", **fields) for n in range(6)
    ]


LUKAS = [
    *monthly("-1500", 1, "rent", category="Affitto", counterparty="Hans Muster"),
    *monthly("-100", 5, "food", counterparty=SHOP),
    txn("20", date=date(2026, 5, 20), source_ref="1", kind=Kind.INCOME, counterparty=SHOP),
    # Paid to the other person: it stays in the household.
    txn(
        "-300", date=date(2026, 2, 10), source_ref="2", category="Regali", counterparty="Anna Meier"
    ),
    # Securities are not spending.
    txn(
        "-5000",
        date=date(2026, 3, 3),
        source_ref="3",
        kind=Kind.SECURITY_TRADE,
        category="Investimenti",
    ),
]
ANNA = [
    *monthly("-300", 6, "food", counterparty=SHOP),
    txn("-90", date=date(2026, 3, 14), source_ref="1", category="Ristoranti e bar"),
    txn(
        "500",
        date=date(2026, 4, 2),
        source_ref="2",
        kind=Kind.INCOME,
        category="Rimborsi",
        counterparty="LUKAS MEIER",
    ),
]
ANNA_EUR = [txn("-100", date=date(2026, 4, 18), source_ref="1", category="Ristoranti e bar", **EUR)]


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    close(connection, OWNER, (START, END), END, ("migros", LUKAS))
    close(connection, PARTNER, (START, END), END, ("migros", ANNA), ("revolut", ANNA_EUR))
    store.set_rate(connection, START, "EUR", Decimal("0.95"), source="fetched:yahoo")
    yield connection
    connection.close()


def D(value: str | int) -> Decimal:  # reads like the type it makes
    return Decimal(value)


def test_each_person_paid_a_share_of_what_the_household_spent(conn: sqlite3.Connection) -> None:
    shown = paidby.run(conn, first=START, last=END, currency="CHF")
    assert shown is not None
    assert (shown.currency, shown.spent) == ("CHF", D(11565))
    # By name. Hers: food, a restaurant and 100 EUR at 0.95; his: rent and food, less a refund.
    assert shown.people == (
        Payer(PARTNER, D(1985), D("0.172")),
        Payer(OWNER, D(9580), D("0.828")),
    )
    assert shown.categories == (
        Split("Casa", D(9000), {PARTNER: D(0), OWNER: D(9000)}),
        Split("Alimentari", D(2380), {PARTNER: D(1800), OWNER: D(580)}),
        Split("Ristoranti e bar", D(185), {PARTNER: D(185), OWNER: D(0)}),
    )


def test_it_adds_up_to_what_the_household_spent(conn: sqlite3.Connection) -> None:
    for first, last in ((START, END), (date(2026, 3, 1), date(2026, 3, 31)), (None, None)):
        shown = paidby.run(conn, first=first, last=last, currency="CHF")
        assert shown is not None
        spent = spending.run(conn, first=first, last=last, currency="CHF")
        assert shown.spent == spent.spent == sum(payer.spent for payer in shown.people)
        assert [split.name for split in shown.categories] == [c.name for c in spent.categories]


def test_only_the_household_sees_who_paid(conn: sqlite3.Connection) -> None:
    everyone = report.run(conn, first=date(2026, 3, 1), last=date(2026, 3, 31), currency="CHF")
    assert everyone.paid_by is not None
    assert [payer.spent for payer in everyone.paid_by.people] == [D(390), D(1600)]
    assert report.run(conn, owner=OWNER, currency="CHF").paid_by is None


def test_a_range_with_nothing_in_it_has_no_one_who_paid(conn: sqlite3.Connection) -> None:
    assert paidby.run(conn, first=END, last=START, currency="CHF") is None


def test_one_person_is_no_household() -> None:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    close(connection, OWNER, (START, END), END, ("migros", LUKAS))
    assert paidby.run(connection, currency="CHF") is None
    connection.close()
