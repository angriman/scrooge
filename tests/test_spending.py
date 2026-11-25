import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scrooge import categories, query, spending
from scrooge.cashflow import Counted, Grouping
from scrooge.models import Kind, Transaction
from scrooge.networth import MissingRateError
from scrooge.spending import Category, Leaf, Merchant
from scrooge.storage import db, store
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close, txn
from tests.test_income import salaries

START, END = date(2025, 1, 1), date(2026, 8, 31)
SHOP = "KONSUM-1234 ZUERICH"
LANDLORD = "Hans Muster, Beispielweg 8, 5000 Aarau"


def monthly(amount: str, day: int, ref: str, **fields: object) -> list[Transaction]:
    return [
        txn(
            amount,
            date=date(2025 + n // 12, n % 12 + 1, day),
            source_ref=f"{ref}{n}",
            **fields,
        )
        for n in range(20)
    ]


LUKAS = [
    *monthly("-1800", 1, "rent", category=categories.RENT, counterparty=LANDLORD),
    *monthly("-100", 5, "food", counterparty=SHOP),
    *salaries("5000", START, 20),
    # The same shop, written another way, and a refund from it.
    txn("-50", date=date(2026, 7, 8), source_ref="1", counterparty="Konsum-77 Zuerich"),
    txn("30", date=date(2026, 7, 20), source_ref="2", kind=Kind.INCOME, counterparty=SHOP),
    txn(
        "-60",
        date=date(2025, 3, 12),
        source_ref="3",
        category=categories.EATING_OUT,
        counterparty="MARIAS CAFÉ BISTRO, ZÜRICH",
    ),
    txn(
        "-40",
        date=date(2026, 3, 10),
        source_ref="4",
        category=categories.EATING_OUT,
        counterparty="MARIAS CAFÉ BISTRO, ZÜRICH",
    ),
    # Paid to the other person, and paid back by her.
    txn(
        "-200",
        date=date(2026, 7, 10),
        source_ref="5",
        category=categories.GIFTS,
        counterparty="Anna Meier",
    ),
    txn(
        "900",
        date=date(2026, 7, 15),
        source_ref="6",
        kind=Kind.INCOME,
        category=categories.REFUNDS,
        counterparty="ANNA MEIER",
    ),
    # Securities are not spending.
    txn(
        "-9920",
        date=date(2026, 7, 3),
        source_ref="7",
        kind=Kind.SECURITY_TRADE,
        category=categories.INVESTMENTS,
    ),
]
LUKAS_USD = [
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
    )
]
ANNA = [
    txn("-60", date=date(2026, 7, 12), source_ref="1", counterparty=SHOP),
    txn(
        "200",
        date=date(2026, 7, 10),
        source_ref="2",
        kind=Kind.INCOME,
        category=categories.REFUNDS,
        counterparty="LUKAS MEIER",
    ),
]


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    close(connection, OWNER, (START, END), END, ("migros", LUKAS), ("ibkr", LUKAS_USD))
    close(connection, PARTNER, (date(2026, 7, 1), END), END, ("migros", ANNA))
    store.set_rate(connection, START, "USD", Decimal("0.90"), source="fetched:yahoo")
    store.set_rate(connection, START, "EUR", Decimal("0.95"), source="fetched:yahoo")
    yield connection
    connection.close()


def spent(  # the keywords of `run`, with this file's defaults
    conn: sqlite3.Connection,
    *,
    first: date | None = date(2026, 1, 1),
    last: date | None = END,
    owner: str | None = OWNER,
    grouping: Grouping = Grouping.MONTH,
    currency: str = "CHF",
) -> spending.Spending:
    return spending.run(
        conn, first=first, last=last, owner=owner, grouping=grouping, currency=currency
    )


def D(value: str | int) -> Decimal:  # reads like the type it makes
    return Decimal(value)


def test_each_category_against_its_usual_month_and_the_year_before(
    conn: sqlite3.Connection,
) -> None:
    result = spent(conn)
    assert result.months == 8
    assert (result.spent, result.paid_back, result.net) == (D("15464.50"), D(900), D("14564.50"))
    assert result.categories == (
        Category(
            categories.GROUP_HOME,
            8,
            D(14400),
            D(14400) / D("15464.50"),
            D(1800),
            D(1800),
            D(14400),
            (Leaf(categories.RENT, 8, D(14400)),),
        ),
        # Eight months of shopping, the same shop once more, a refund from it.
        Category(
            categories.GROUP_FOOD,
            10,
            D(820),
            D(820) / D("15464.50"),
            D("102.50"),
            D(100),
            D(800),
            (Leaf(categories.GROCERIES, 10, D(820)),),
        ),
        Category(
            categories.GROUP_GIFTS,
            1,
            D(200),
            D(200) / D("15464.50"),
            D(25),
            D(0),
            D(0),
            (Leaf(categories.GIFTS, 1, D(200)),),
        ),
        Category(
            categories.EATING_OUT,
            1,
            D(40),
            D(40) / D("15464.50"),
            D(5),
            D(0),
            D(60),
            (Leaf(categories.EATING_OUT, 1, D(40)),),
        ),
        # 5 USD at 0.90.
        Category(
            categories.GROUP_BANKING,
            1,
            D("4.50"),
            D("4.50") / D("15464.50"),
            D("0.56"),
            D(0),
            D(0),
            (Leaf(categories.FEES, 1, D("4.50")),),
        ),
    )


def test_merchants_are_named_without_numbers_places_or_addresses(
    conn: sqlite3.Connection,
) -> None:
    result = spent(conn)
    assert result.merchants == (
        Merchant(
            "Hans Muster", categories.GROUP_HOME, 8, D(14400), date(2026, 1, 1), date(2026, 8, 1)
        ),
        Merchant(
            "KONSUM ZUERICH", categories.GROUP_FOOD, 10, D(820), date(2026, 1, 5), date(2026, 8, 5)
        ),
        Merchant(
            "Anna Meier", categories.GROUP_GIFTS, 1, D(200), date(2026, 7, 10), date(2026, 7, 10)
        ),
        Merchant(
            "MARIAS CAFÉ BISTRO",
            categories.EATING_OUT,
            1,
            D(40),
            date(2026, 3, 10),
            date(2026, 3, 10),
        ),
        # A fee is the account's, whatever it names.
        Merchant(
            "IBKR USD", categories.GROUP_BANKING, 1, D("4.50"), date(2026, 2, 1), date(2026, 2, 1)
        ),
    )
    assert result.merchants[1].average == D(82)
    assert (result.other_merchants, result.other_spent) == (0, D(0))


def test_only_the_largest_merchants_are_listed(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spending, "MERCHANTS", 2)
    result = spent(conn)
    assert [merchant.name for merchant in result.merchants] == ["Hans Muster", "KONSUM ZUERICH"]
    assert (result.other_merchants, result.other_spent) == (3, D("244.50"))


def test_each_period_stacks_the_largest_categories_and_hangs_what_came_back(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    july = spent(conn, first=date(2026, 7, 1), last=date(2026, 7, 31))
    assert july.parts == (
        categories.GROUP_HOME,
        categories.GROUP_GIFTS,
        categories.GROUP_FOOD,
        "Paid back",
    )
    assert july.periods[0].parts == {
        categories.GROUP_HOME: D(1800),
        categories.GROUP_GIFTS: D(200),
        categories.GROUP_FOOD: D(120),
        "Paid back": D(-900),
    }
    assert july.periods[0].total == D(1220)
    monkeypatch.setattr(spending, "SHOWN", 1)
    fewer = spent(conn)
    assert fewer.parts == (categories.GROUP_HOME, "Others", "Paid back")
    by_name = {period.name: period.parts for period in fewer.periods}
    assert by_name["2026-02"] == {
        categories.GROUP_HOME: D(1800),
        "Others": D("104.50"),
        "Paid back": D(0),
    }


def test_a_payment_and_its_reversal_still_show_what_came_back() -> None:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    first, last = date(2026, 1, 1), date(2026, 1, 31)
    rows = [
        txn("-10", date=date(2026, 1, 5), source_ref="1"),
        txn(
            "50",
            date=date(2026, 1, 6),
            source_ref="2",
            kind=Kind.INCOME,
            category=categories.REFUNDS,
            counterparty="Anna Meier",
        ),
        txn(
            "-50",
            date=date(2026, 1, 7),
            source_ref="3",
            kind=Kind.INCOME,
            category=categories.REFUNDS,
            counterparty="Anna Meier",
        ),
    ]
    close(connection, OWNER, (first, last), last, ("migros", rows))
    result = spent(connection, first=first, last=last)
    assert (result.spent, result.paid_back) == (D(10), D(0))
    assert result.parts == (categories.GROUP_FOOD, "Paid back")
    assert result.periods[0].parts == {categories.GROUP_FOOD: D(10), "Paid back": D(0)}
    connection.close()


def test_both_people_without_what_they_pay_each_other(conn: sqlite3.Connection) -> None:
    both = spent(conn, owner=None, first=date(2026, 7, 1), last=date(2026, 7, 31))
    assert both.parts == (categories.GROUP_HOME, categories.GROUP_FOOD)
    assert (both.spent, both.paid_back) == (D(1980), D(0))
    hers = spent(conn, owner=PARTNER, first=date(2026, 7, 1), last=date(2026, 7, 31))
    assert (hers.spent, hers.paid_back, hers.net) == (D(60), D(200), D(-140))
    # Her months before July are not known: nothing to set them against.
    assert (hers.categories[0].usual, hers.categories[0].year_before) == (None, None)


def test_the_usual_month_needs_the_twelve_before_the_range(conn: sqlite3.Connection) -> None:
    result = spent(conn, first=date(2025, 12, 1), last=date(2025, 12, 31))
    assert result.categories[0].usual is None
    assert result.categories[0].year_before is None


def test_each_row_is_converted_at_the_rate_of_its_own_day(conn: sqlite3.Connection) -> None:
    euros = spent(conn, first=date(2026, 2, 1), last=date(2026, 2, 28), currency="EUR")
    assert euros.spent == D("2004.74")  # 1800 + 100 + 4.50 francs at 0.95
    with pytest.raises(MissingRateError, match="GBP"):
        spent(conn, currency="GBP")
    with pytest.raises(query.QueryError):
        spent(conn, owner="Nobody")


def test_an_empty_database_spends_nothing() -> None:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    result = spending.run(connection, currency="CHF")
    assert (result.periods, result.categories, result.through) == ((), (), None)


def test_a_row_without_a_counterparty_is_the_accounts() -> None:
    row = Counted(
        date(2026, 7, 1),
        OWNER,
        "Revolut CHF",
        Kind.EXPENSE,
        "CHF",
        "expenses",
        categories.GROUP_HOME,
        categories.UTILITIES,
        0,
        " 42 ",
        None,
        "1",
    )
    assert spending.merchant(row) == "Revolut CHF"


@given(
    first=st.dates(date(2024, 6, 1), date(2026, 12, 31)),
    days=st.integers(0, 700),
    grouping=st.sampled_from(list(Grouping)),
    owner=st.sampled_from([OWNER, PARTNER, None]),
)
def test_periods_categories_and_merchants_add_up_to_what_was_spent(
    conn: sqlite3.Connection, first: date, days: int, grouping: Grouping, owner: str | None
) -> None:
    last = date.fromordinal(first.toordinal() + days)
    result = spent(conn, first=first, last=last, grouping=grouping, owner=owner, currency="EUR")
    slack = Decimal("0.01") * (len(result.periods) + len(result.categories) + 5)
    by_periods = sum((period.total for period in result.periods), Decimal(0))
    assert abs(by_periods - result.net) <= slack
    assert abs(sum(c.spent for c in result.categories) - result.spent) <= slack
    by_merchants = sum((m.spent for m in result.merchants), result.other_spent)
    assert abs(by_merchants - result.spent) <= slack


def test_what_was_paid_back_is_not_spending_a_year_later_either() -> None:
    # His food every month, and her 900 paid back in July 2025, which is not food.
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    back = txn(
        "900",
        date=date(2025, 7, 15),
        source_ref="back",
        kind=Kind.INCOME,
        category=categories.REFUNDS,
        counterparty="ANNA MEIER",
    )
    rows = [*monthly("-100", 5, "food", counterparty=SHOP), back]
    close(connection, OWNER, (START, END), END, ("migros", rows))
    july = spent(connection, first=date(2026, 7, 1), last=date(2026, 7, 31))
    assert [(c.name, c.year_before) for c in july.categories] == [(categories.GROUP_FOOD, D(100))]


def test_a_category_usually_spent_on_and_not_in_the_range_is_there_at_nothing(
    conn: sqlite3.Connection,
) -> None:
    # The rent is paid on the first: not in these days.
    result = spent(conn, first=date(2026, 8, 2))
    rent = next(
        category for category in result.categories if category.name == categories.GROUP_HOME
    )
    assert rent == Category(categories.GROUP_HOME, 0, D(0), D("0.000"), D(0), D(1800), D(0), ())
    assert result.categories[-1] == rent
    assert result.parts == (categories.GROUP_FOOD,)
