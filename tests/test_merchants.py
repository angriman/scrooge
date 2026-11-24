import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest

from scrooge import merchants, query
from scrooge.merchants import Merchant
from scrooge.models import Kind
from scrooge.networth import MissingRateError
from scrooge.storage import db, store
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close, txn

START, END = date(2025, 1, 1), date(2026, 8, 31)  # twenty months, the last one over
SHOP = "KONSUM-1234 ZUERICH"

LUKAS = [
    # Every month: the twelve up to August 2026 add up to 1200.
    *(
        txn("-100", date=date(2025 + n // 12, n % 12 + 1, 5), source_ref=f"f{n}", counterparty=SHOP)
        for n in range(20)
    ),
    # Written another way since: the same merchant, named as last written.
    txn("-50", date=date(2026, 8, 9), source_ref="1", counterparty="konsum zuerich"),
    # A refund lowers what was spent there.
    txn("20", date=date(2026, 8, 10), source_ref="2", kind=Kind.INCOME, counterparty=SHOP),
    # Only before the twelve months: listed after the others, with nothing spent.
    txn("-500", date=date(2025, 3, 3), source_ref="3", counterparty="Coop-3 Bern"),
    txn("-80", date=date(2025, 6, 3), source_ref="4", counterparty="Jelmoli"),
    # Within them, less than the shop.
    txn("-300", date=date(2025, 9, 1), source_ref="5", counterparty="Hotel Adler, Bern"),
    # Neither spent nor a merchant: the salary, a move between his accounts, securities.
    txn("6000", date=date(2026, 8, 25), source_ref="6", kind=Kind.INCOME, category="Salary"),
    txn(
        "-900", date=date(2026, 8, 26), source_ref="7", kind=Kind.TRANSFER, category="Own transfer"
    ),
    txn(
        "-2000",
        date=date(2026, 8, 3),
        source_ref="8",
        kind=Kind.SECURITY_TRADE,
        category="Investments",
    ),
]
LUKAS_USD = [
    txn(
        "-10",
        date=date(2026, 2, 1),
        source_ref="1",
        kind=Kind.COMMISSION,
        category="Fees",
        counterparty="/ESZ6",
        account="IBKR USD",
        currency="USD",
        source_file="ibkr.csv",
    )
]
ANNA = [txn("-40", date=date(2026, 8, 12), source_ref="1", counterparty="Hotel Adler")]


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    close(connection, OWNER, (START, END), END, ("migros", LUKAS), ("ibkr", LUKAS_USD))
    close(connection, PARTNER, (date(2026, 8, 1), END), END, ("migros", ANNA))
    store.set_rate(connection, START, "USD", Decimal("0.90"), source="fetched:yahoo")
    yield connection
    connection.close()


def test_every_merchant_spent_at_the_largest_first(conn: sqlite3.Connection) -> None:
    found = merchants.run(conn, owner=OWNER, currency="CHF")
    assert (found.currency, found.since, found.through) == ("CHF", date(2025, 9, 1), END)
    assert found.merchants == (
        Merchant("KONSUM ZUERICH", Decimal(1230), 14, date(2026, 8, 10)),
        Merchant("Hotel Adler", Decimal(300), 1, date(2025, 9, 1)),
        # A fee is its account's, converted on its day.
        Merchant("IBKR USD", Decimal(9), 1, date(2026, 2, 1)),
        # The ones not spent at in the twelve months, the latest first.
        Merchant("Jelmoli", Decimal(0), 0, date(2025, 6, 3)),
        Merchant("Coop Bern", Decimal(0), 0, date(2025, 3, 3)),
    )


def test_only_the_first_come_with_their_figures_the_rest_by_name(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(merchants, "SHOWN", 2)
    found = merchants.run(conn, owner=OWNER, currency="CHF")
    assert [each.name for each in found.merchants] == ["KONSUM ZUERICH", "Hotel Adler"]
    assert found.others == ("IBKR USD", "Jelmoli", "Coop Bern")


def test_each_name_lists_the_rows_the_merchant_filter_finds(conn: sqlite3.Connection) -> None:
    for merchant in merchants.run(conn, owner=OWNER, currency="CHF").merchants:
        listed = query.run(conn, query.Filter(owner=OWNER, merchant=merchant.name))
        assert listed.rows
        assert listed.rows[-1].date == merchant.last


def test_both_people_together(conn: sqlite3.Connection) -> None:
    found = merchants.run(conn, currency="CHF")
    hotel = next(each for each in found.merchants if each.name == "Hotel Adler")
    assert (hotel.spent, hotel.count, hotel.last) == (Decimal(340), 2, date(2026, 8, 12))
    alone = merchants.run(conn, owner=PARTNER, currency="CHF")
    assert alone.merchants == (Merchant("Hotel Adler", Decimal(40), 1, date(2026, 8, 12)),)


def test_what_cannot_be_read(conn: sqlite3.Connection) -> None:
    with pytest.raises(query.QueryError, match="no owner named 'Nobody'"):
        merchants.run(conn, owner="Nobody")
    with pytest.raises(MissingRateError, match="GBP"):
        merchants.run(conn, owner=OWNER, currency="GBP")


def test_only_the_twelve_months_are_converted() -> None:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    yen = {"account": "Card JPY", "currency": "JPY", "source_file": "jpy.csv"}
    rows = [txn("-9000", date=date(2024, 5, 3), source_ref="1", counterparty="Ryokan", **yen)]
    close(connection, OWNER, (date(2024, 5, 1), END), END, ("jpy", rows), ("migros", LUKAS[-4:]))
    found = merchants.run(connection, owner=OWNER, currency="CHF")
    # No rate is stored for yen: the ryokan is only named, nothing of it is converted.
    assert found.merchants[-1] == Merchant("Ryokan", Decimal(0), 0, date(2024, 5, 3))
    connection.close()


def test_nothing_committed_lists_nobody() -> None:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    assert merchants.run(connection) == merchants.Merchants("EUR", None, None, (), ())
