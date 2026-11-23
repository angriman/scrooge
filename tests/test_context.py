import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest

from scrooge import context, query
from scrooge.context import Merchant
from scrooge.models import Kind, Transaction
from scrooge.networth import MissingRateError
from scrooge.storage import db, store
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close, txn

START, END = date(2024, 9, 1), date(2026, 8, 20)  # the last month is not over
SHOP = "KONSUM-1234 ZUERICH"
LANDLORD = "Hans Muster, Beispielweg 8, 5000 Aarau"


def month(n: int, day: int) -> date:
    """The `day` of the `n`th month from START."""
    return date(START.year + (START.month - 1 + n) // 12, (START.month - 1 + n) % 12 + 1, day)


LUKAS = [
    *(
        txn(
            "-1500",
            date=month(n, 1),
            source_ref=f"rent{n}",
            category="Affitto",
            counterparty=LANDLORD,
        )
        for n in range(24)
    ),
    *(txn("-100", date=month(n, 5), source_ref=f"food{n}", counterparty=SHOP) for n in range(24)),
    # Another shop, every other month.
    *(
        txn("-40", date=month(n, 7), source_ref=f"coop{n}", counterparty="Coop-7 Bern")
        for n in range(0, 24, 2)
    ),
    *(
        txn("5000", date=month(n, 25), source_ref=f"pay{n}", kind=Kind.INCOME, category="Stipendio")
        for n in range(23)
    ),
    # A refund from the shop lowers what was spent there.
    txn("20", date=date(2026, 7, 9), source_ref="1", kind=Kind.INCOME, counterparty=SHOP),
    # Paid to the other person, and paid back by her: not income.
    txn(
        "-200", date=date(2026, 7, 10), source_ref="2", category="Regali", counterparty="Anna Meier"
    ),
    txn(
        "900",
        date=date(2026, 7, 15),
        source_ref="3",
        kind=Kind.INCOME,
        category="Rimborsi",
        counterparty="ANNA MEIER",
    ),
    # Securities are not spending.
    txn(
        "-9920",
        date=date(2026, 7, 3),
        source_ref="4",
        kind=Kind.SECURITY_TRADE,
        category="Investimenti",
    ),
]
LUKAS_USD: list[Transaction] = [
    txn(
        "-5",
        date=date(2026, 2, 1),
        source_ref="1",
        kind=Kind.COMMISSION,
        category="Commissioni",
        counterparty="/ESZ6",
        account="IBKR USD",
        currency="USD",
        source_file="ibkr.csv",
    )
]
ANNA = [txn("-60", date=date(2026, 7, 12), source_ref="1", counterparty=SHOP)]


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    close(connection, OWNER, (START, END), END, ("migros", LUKAS), ("ibkr", LUKAS_USD))
    close(connection, PARTNER, (date(2026, 7, 1), END), END, ("migros", ANNA))
    store.set_rate(connection, START, "USD", Decimal("0.90"), source="fetched:yahoo")
    yield connection
    connection.close()


def shown(
    conn: sqlite3.Connection, kind: str, name: str, owner: str | None = OWNER
) -> context.Context:
    return context.run(conn, kind, name, owner=owner, currency="CHF")


def amounts(found: context.Context) -> list[Decimal]:
    return [each.amount for each in found.months]


def test_a_category_month_by_month_against_its_usual_month(conn: sqlite3.Connection) -> None:
    found = shown(conn, "category", "Alimentari")
    assert [each.first for each in found.months[:2]] == [date(2024, 9, 1), date(2024, 10, 1)]
    assert found.months[-1].first == date(2026, 8, 1)
    # 140 in the months of both shops, 100 in the others; the refund is taken off July.
    assert amounts(found) == [Decimal(140 if n % 2 == 0 else 100) for n in range(22)] + [
        Decimal(120),
        Decimal(100),
    ]
    assert (found.usual, found.this_month) == (Decimal(110), Decimal(100))
    assert (found.through, found.partial, found.earned) == (END, True, False)
    assert found.merchants == (
        Merchant("KONSUM ZUERICH", Decimal(2380), 25),
        Merchant("Coop Bern", Decimal(480), 12),
    )
    assert amounts(shown(conn, "category", "Spesa")) == amounts(found)  # its one leaf


def test_a_category_of_income_is_money_earned(conn: sqlite3.Connection) -> None:
    found = shown(conn, "category", "Stipendio")
    assert found.earned
    assert amounts(found) == [Decimal(5000)] * 23 + [Decimal(0)]
    assert (found.usual, found.this_month) == (Decimal(5000), Decimal(0))
    assert found.merchants == (Merchant("Migros", Decimal(115000), 23),)  # it names nobody
    # What the other person pays back is not income.
    assert set(amounts(shown(conn, "category", "Rimborsi"))) == {Decimal(0)}


def test_only_the_largest_merchants_of_a_category_are_listed(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(context, "MERCHANTS", 1)
    found = shown(conn, "category", "Alimentari")
    assert found.merchants == (Merchant("KONSUM ZUERICH", Decimal(2380), 25),)


def test_a_merchant_as_the_cash_flow_names_it(conn: sqlite3.Connection) -> None:
    found = shown(conn, "merchant", "konsum  zuerich")
    assert found.name == "KONSUM ZUERICH"  # as last written
    assert amounts(found) == [Decimal(100)] * 22 + [Decimal(80), Decimal(100)]
    assert (found.usual, found.merchants) == (Decimal(100), None)
    nobody = shown(conn, "merchant", "Nowhere")
    assert (nobody.name, set(amounts(nobody)), nobody.usual) == (
        "Nowhere",
        {Decimal(0)},
        Decimal(0),
    )


def test_an_account_is_what_was_spent_from_it_converted_on_its_day(
    conn: sqlite3.Connection,
) -> None:
    found = shown(conn, "account", "IBKR USD")
    assert amounts(found)[17] == Decimal("4.50")  # February 2026: 5 dollars at 0.90
    assert sum(amounts(found)) == Decimal("4.50")
    assert found.usual == Decimal(0)
    assert context.balance(conn, "IBKR USD", owner=OWNER, currency="CHF") == Decimal("-4.50")
    assert context.balance(conn, "Nowhere", owner=OWNER, currency="CHF") is None


def test_both_people_need_twelve_months_each_for_a_usual_month(conn: sqlite3.Connection) -> None:
    found = shown(conn, "merchant", "Konsum Zuerich", owner=None)
    assert amounts(found)[-2] == Decimal(140)  # Anna's 60 in July too
    assert found.usual is None
    both = context.balance(conn, "Migros", currency="CHF")
    alone = context.balance(conn, "Migros", owner=OWNER, currency="CHF")
    assert both is not None
    assert alone is not None
    assert both - alone == Decimal(-60)


def test_a_name_the_database_does_not_have_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(query.QueryError, match="no category named 'Alimentar'"):
        shown(conn, "category", "Alimentar")
    with pytest.raises(query.QueryError, match="no account named 'IBKR USD' of Anna"):
        shown(conn, "account", "IBKR USD", owner=PARTNER)
    with pytest.raises(ValueError, match="'shop' is not one of category, merchant, account"):
        shown(conn, "shop", "Coop")
    with pytest.raises(MissingRateError, match="GBP"):
        context.run(conn, "category", "Alimentari", owner=OWNER, currency="GBP")


def test_nothing_committed_has_no_months() -> None:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    found = context.run(connection, "merchant", "Coop", currency="CHF")
    assert (found.months, found.usual, found.this_month, found.partial) == ((), None, None, False)
