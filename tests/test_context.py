import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest

from scrooge import categories, context, query, spending
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
            category=categories.RENT,
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
        txn(
            "5000",
            date=month(n, 25),
            source_ref=f"pay{n}",
            kind=Kind.INCOME,
            category=categories.SALARY,
        )
        for n in range(23)
    ),
    # A refund from the shop lowers what was spent there.
    txn("20", date=date(2026, 7, 9), source_ref="1", kind=Kind.INCOME, counterparty=SHOP),
    # Paid to the other person, and paid back by her: not income.
    txn(
        "-200",
        date=date(2026, 7, 10),
        source_ref="2",
        category=categories.GIFTS,
        counterparty="Anna Meier",
    ),
    txn(
        "900",
        date=date(2026, 7, 15),
        source_ref="3",
        kind=Kind.INCOME,
        category=categories.REFUNDS,
        counterparty="ANNA MEIER",
    ),
    # Securities are not spending.
    txn(
        "-9920",
        date=date(2026, 7, 3),
        source_ref="4",
        kind=Kind.SECURITY_TRADE,
        category=categories.INVESTMENTS,
    ),
]
LUKAS_USD: list[Transaction] = [
    # Moved in from another account of his: not spent, but the account's own money in.
    txn(
        "1000",
        date=date(2026, 1, 20),
        source_ref="0",
        kind=Kind.TRANSFER,
        category=categories.OWN_TRANSFER,
        account="IBKR USD",
        currency="USD",
        source_file="ibkr.csv",
    ),
    txn(
        "-5",
        date=date(2026, 2, 1),
        source_ref="1",
        kind=Kind.COMMISSION,
        category=categories.FEES,
        counterparty="/ESZ6",
        account="IBKR USD",
        currency="USD",
        source_file="ibkr.csv",
    ),
]
ANNA = [txn("-60", date=date(2026, 7, 12), source_ref="1", counterparty=SHOP)]


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    close(connection, OWNER, (START, END), END, ("migros", LUKAS), ("ibkr", LUKAS_USD))
    close(connection, PARTNER, (date(2026, 7, 1), END), END, ("migros", ANNA))
    store.set_rate(connection, START, "USD", Decimal("0.90"), source="fetched:yahoo")
    store.set_rate(connection, date(2026, 2, 1), "USD", Decimal("0.80"), source="fetched:yahoo")
    yield connection
    connection.close()


def shown(
    conn: sqlite3.Connection, kind: str, name: str, owner: str | None = OWNER
) -> context.Context:
    return context.run(conn, kind, name, owner=owner, currency="CHF")


def amounts(found: context.Context) -> list[Decimal]:
    return [each.amount for each in found.months]


def test_a_category_month_by_month_against_its_usual_month(conn: sqlite3.Connection) -> None:
    found = shown(conn, "category", categories.GROUP_FOOD)
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
    assert amounts(shown(conn, "category", categories.GROCERIES)) == amounts(found)  # its one leaf


def test_a_category_of_income_is_money_earned(conn: sqlite3.Connection) -> None:
    found = shown(conn, "category", categories.SALARY)
    assert found.earned
    assert amounts(found) == [Decimal(5000)] * 23 + [Decimal(0)]
    assert (found.usual, found.this_month) == (Decimal(5000), Decimal(0))
    assert found.merchants == (Merchant("Migros", Decimal(115000), 23),)  # it names nobody
    # What the other person pays back is not income.
    assert set(amounts(shown(conn, "category", categories.REFUNDS))) == {Decimal(0)}


def test_only_the_largest_merchants_of_a_category_are_listed(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(context, "MERCHANTS", 1)
    found = shown(conn, "category", categories.GROUP_FOOD)
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


def test_an_account_is_its_net_flow_each_row_converted_on_its_day(
    conn: sqlite3.Connection,
) -> None:
    found = shown(conn, "account", "IBKR USD")
    assert found.net
    assert not found.earned
    # January 2026: 1000 dollars moved in at 0.90. February's fee of 5 is what the money
    # cost, not money taken out: it is only in the balance.
    assert (amounts(found)[16], amounts(found)[17]) == (Decimal(900), Decimal(0))
    assert sum(amounts(found)) == Decimal(900)
    assert (found.usual, found.merchants) == (Decimal(0), None)
    assert context.balance(conn, "IBKR USD", owner=OWNER, currency="CHF") == Decimal(796)
    assert context.balance(conn, "Nowhere", owner=OWNER, currency="CHF") is None


def test_an_account_counts_every_row_of_it_in_and_out(conn: sqlite3.Connection) -> None:
    found = shown(conn, "account", "Migros")
    # The salary less the rent and the shops: less in the months of both shops.
    assert amounts(found)[:2] == [Decimal(3360), Decimal(3400)]
    # July 2026: the refund, what went to and came back from Anna; the securities bought stay
    # inside the account, which holds them.
    assert amounts(found)[-2] == Decimal(3360 + 20 - 200 + 900)
    assert amounts(found)[-1] == Decimal(-1600)  # August: no salary yet
    assert found.usual == Decimal(3400)
    assert (found.name, found.this_month) == ("Migros", Decimal(-1600))


def test_only_an_account_is_a_net_flow(conn: sqlite3.Connection) -> None:
    assert not shown(conn, "category", categories.GROUP_FOOD).net
    assert not shown(conn, "merchant", "Coop Bern").net


@pytest.mark.parametrize("owner", [OWNER, None])
@pytest.mark.parametrize("name", ["KONSUM ZUERICH", "Coop Bern", "Anna Meier", "Migros"])
def test_the_transactions_of_a_merchant_add_up_to_what_the_cash_flow_spent_there(
    conn: sqlite3.Connection, name: str, owner: str | None
) -> None:
    first, last = date(2025, 9, 1), date(2026, 7, 31)
    listed = query.run(conn, query.Filter(first=first, last=last, owner=owner, merchant=name))
    flow = spending.run(conn, first=first, last=last, owner=owner, currency="CHF")
    spent = {merchant.name: merchant.spent for merchant in flow.merchants}
    # Migros names the salary and the trade only, which are not spent; Anna Meier was paid by
    # Lukas, which is not spent for both together.
    expected = [("CHF", -spent[name])] if name in spent else []
    assert [(each.currency, each.total) for each in listed.totals] == expected


def test_both_people_need_twelve_months_each_for_a_usual_month(conn: sqlite3.Connection) -> None:
    found = shown(conn, "merchant", "Konsum Zuerich", owner=None)
    assert amounts(found)[-2] == Decimal(140)  # Anna's 60 in July too
    assert found.usual is None
    # Both people's rows of an account of that name, and still no usual month.
    flow = shown(conn, "account", "Migros", owner=None)
    assert amounts(flow)[-2] == amounts(shown(conn, "account", "Migros"))[-2] - 60
    assert flow.usual is None
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
        context.run(conn, "category", categories.GROUP_FOOD, owner=OWNER, currency="GBP")


def test_an_exchange_nets_to_its_spread_and_trading_stays_inside() -> None:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    # An account holds one currency: an exchange is a row out of one and a row into another.
    francs = {
        "account": "Revolut CHF",
        "source_file": "revolut.csv",
        "category": categories.buy("EUR"),
    }
    euros = francs | {"account": "Revolut EUR", "currency": "EUR", "source_file": "eur.csv"}
    out = [txn("-1000", date=date(2026, 7, 3), source_ref="1", kind=Kind.FX, **francs)]
    rows = [
        txn("1050", date=date(2026, 7, 3), source_ref="2", kind=Kind.FX, **euros),
        # The results of trading stay inside the account.
        txn(
            "-75",
            date=date(2026, 7, 9),
            source_ref="3",
            kind=Kind.TRADING,
            **euros | {"category": categories.TRADING},
        ),
    ]
    july_days = (date(2026, 7, 1), date(2026, 7, 31))
    close(connection, OWNER, july_days, july_days[1], ("revolut", out), ("eur", rows))
    store.set_rate(connection, date(2026, 7, 1), "EUR", Decimal("0.95"), source="fetched:yahoo")

    def july(account: str) -> Decimal:
        found = context.run(connection, "account", account, owner=OWNER, currency="CHF")
        assert found.this_month is not None
        return found.this_month

    assert (july("Revolut CHF"), july("Revolut EUR")) == (Decimal(-1000), Decimal("997.50"))
    # Both together: the spread, 1050 euros at 0.95 for 1000 francs.
    assert july("Revolut CHF") + july("Revolut EUR") == Decimal("-2.50")
    connection.close()


def test_an_account_net_flow_leaves_out_what_its_money_earned_or_cost() -> None:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    broker = {"account": "IBKR CHF", "source_file": "ibkr.csv", "date": date(2026, 7, 10)}
    rows = [
        # Money put in: the only row that counts.
        txn(
            "2000",
            source_ref="1",
            kind=Kind.TRANSFER,
            category=categories.OWN_TRANSFER,
            **broker,
        ),
        # What the money earned or cost is a return, not money put in or taken out.
        txn("40", source_ref="2", kind=Kind.DIVIDEND, category=categories.DIVIDENDS, **broker),
        txn(
            "-6",
            source_ref="3",
            kind=Kind.TAX,
            category=categories.WITHHOLDING_TAX,
            **broker,
        ),
        txn("12", source_ref="4", kind=Kind.INTEREST, category=categories.INTEREST, **broker),
        txn("-3", source_ref="5", kind=Kind.INTEREST, category=categories.INTEREST, **broker),
        txn("-2", source_ref="6", kind=Kind.COMMISSION, category=categories.FEES, **broker),
        # A bill's principal repaid stays inside the account, like the bill bought.
        txn(
            "990",
            source_ref="7",
            kind=Kind.INCOME,
            category=categories.INVESTMENTS,
            **broker,
        ),
    ]
    july_days = (date(2026, 7, 1), date(2026, 7, 31))
    close(connection, OWNER, july_days, july_days[1], ("ibkr", rows))
    found = context.run(connection, "account", "IBKR CHF", owner=OWNER, currency="CHF")
    assert found.this_month == Decimal(2000)
    connection.close()


def test_nothing_committed_has_no_months() -> None:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    found = context.run(connection, "merchant", "Coop", currency="CHF")
    assert (found.months, found.usual, found.this_month, found.partial) == ((), None, None, False)
