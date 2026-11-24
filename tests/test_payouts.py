import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from scrooge import categories, payouts
from scrooge.models import Kind, Transaction
from scrooge.payouts import Holding, Paid
from scrooge.storage import db, store
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close, txn

START, END = date(2025, 1, 1), date(2026, 8, 31)
USD = {"account": "IBKR USD", "currency": "USD", "source_file": "ibkr.csv"}
FUND = {"account": "Finpension", "source_file": "finpension.csv"}
DIVIDEND = {"kind": Kind.DIVIDEND, "category": categories.DIVIDENDS}
INTEREST = {"kind": Kind.INTEREST, "category": categories.INTEREST}
TAX = {"kind": Kind.TAX, "category": categories.WITHHOLDING_TAX}
CUSIP, ISIN = "912797ZZ1", "CH0000000001"


def paid(amount: str, day: date, ref: str, counterparty: str = "", **fields: Any) -> Transaction:
    return txn(amount, date=day, source_ref=ref, counterparty=counterparty, **fields)


LUKAS_USD = [
    paid("100", date(2025, 3, 15), "1", "ACME", **DIVIDEND, **USD),
    paid("100", date(2026, 3, 16), "2", "ACME", **DIVIDEND, **USD),
    paid("-15", date(2026, 3, 16), "3", "ACME", **TAX, **USD),
    # The same holding, as a statement may write it.
    paid("100", date(2026, 6, 15), "4", " acme ", **DIVIDEND, **USD),
    # Interest on cash names nothing; that split off a bill names the bill.
    paid("20", date(2026, 7, 1), "5", **INTEREST, **USD),
    paid("80", date(2026, 7, 24), "6", CUSIP, **INTEREST, **USD),
    # Tax withheld that names what no dividend does.
    paid("-5", date(2026, 8, 3), "7", "WIDG", **TAX, **USD),
]
LUKAS_FUND = [paid("50", date(2026, 4, 10), "1", ISIN, **DIVIDEND, **FUND)]
LUKAS = [
    paid("5", date(2026, 5, 31), "1", **INTEREST),
    paid("5000", date(2026, 5, 25), "2", kind=Kind.INCOME, category=categories.SALARY),
    paid("-40", date(2026, 5, 26), "3"),
]
ANNA = [paid("1", date(2026, 2, 28), "1", **INTEREST)]


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    files = (("ibkr", LUKAS_USD), ("finpension", LUKAS_FUND), ("migros", LUKAS))
    close(connection, OWNER, (START, END), END, *files)
    close(connection, PARTNER, (date(2026, 1, 1), END), END, ("migros", ANNA))
    store.instrument_id(connection, "ACME", "Stocks", "USD", name="ACME Corp")
    store.instrument_id(connection, ISIN, "Funds", "CHF", name="ACME Global Fund")
    store.instrument_id(connection, CUSIP, "Bonds", "USD", date(2026, 7, 24))
    for day, currency, rate in (
        (date(2025, 1, 1), "EUR", "0.95"),
        (date(2025, 3, 14), "USD", "0.80"),
        (date(2026, 3, 16), "USD", "0.90"),
    ):
        store.set_rate(connection, day, currency, Decimal(rate), source="fetched:yahoo")
    yield connection
    connection.close()


def his(
    conn: sqlite3.Connection,
    *,
    first: date | None = None,
    last: date | None = None,
    owner: str | None = OWNER,
    currency: str = "CHF",
) -> payouts.Payouts:
    return payouts.run(conn, first=first, last=last, owner=owner, currency=currency)


def amounts(dividends: str, interest: str, tax: str = "0") -> Paid:
    return Paid(Decimal(dividends), Decimal(interest), Decimal(tax))


def test_the_range_is_the_twelve_months_up_to_the_last_day_committed(
    conn: sqlite3.Connection,
) -> None:
    shown = his(conn)
    assert (shown.first, shown.last, shown.through) == (date(2025, 9, 1), END, END)
    assert [month.name for month in shown.months] == [
        *(f"2025-{m:02}" for m in range(9, 13)),
        *(f"2026-{m:02}" for m in range(1, 9)),
    ]
    # Every row at the rate of its own day: dollars at 0.90 from March 2026. The salary and
    # the groceries are not there, nor the dividend of March 2025.
    assert shown.totals == amounts("230", "95", "-18")
    assert shown.totals.paid == Decimal(325)
    assert shown.totals.net == Decimal(307)
    # The months before the first statement are not known: there is nothing to compare.
    assert shown.year_before is None


def test_each_month_has_what_was_paid_in_it(conn: sqlite3.Connection) -> None:
    months = {month.name: month.paid for month in his(conn).months}
    assert months["2025-12"] == payouts.NOTHING
    assert months["2026-03"] == amounts("90", "0", "-13.50")
    assert months["2026-04"] == amounts("50", "0")
    assert months["2026-07"] == amounts("0", "90")
    assert months["2026-08"] == amounts("0", "0", "-4.50")


def test_holdings_are_grouped_by_counterparty_what_paid_the_most_first(
    conn: sqlite3.Connection,
) -> None:
    assert his(conn).holdings == (
        Holding(
            "ACME",
            "ACME Corp",
            "ACME",
            None,
            None,
            amounts("180", "0", "-13.50"),
            3,
            date(2026, 6, 15),
        ),
        # A bill's CUSIP and a fund's ISIN are its symbol: the bill has no name of its own.
        Holding(CUSIP, CUSIP, CUSIP, None, None, amounts("0", "72"), 1, date(2026, 7, 24)),
        Holding(
            ISIN, "ACME Global Fund", ISIN, None, None, amounts("50", "0"), 1, date(2026, 4, 10)
        ),
        # Rows that name nothing are the account's.
        Holding(
            "IBKR USD", "IBKR USD", None, "IBKR USD", None, amounts("0", "18"), 1, date(2026, 7, 1)
        ),
        Holding("Migros", "Migros", None, "Migros", None, amounts("0", "5"), 1, date(2026, 5, 31)),
        # Only tax withheld, under a name no instrument has: last is when it was withheld.
        Holding("WIDG", "WIDG", None, None, None, amounts("0", "0", "-4.50"), 1, date(2026, 8, 3)),
    )


def test_a_range_whose_year_before_is_known_is_compared_with_it(conn: sqlite3.Connection) -> None:
    shown = his(conn, first=date(2026, 1, 1), last=date(2026, 3, 31), currency="EUR")
    assert [month.name for month in shown.months] == ["2026-01", "2026-02", "2026-03"]
    # Dollars into francs, francs into euros: 100 at 0.90 / 0.95.
    assert shown.totals == amounts("94.74", "0", "-14.21")
    assert shown.year_before == amounts("84.21", "0")  # 100 at 0.80 / 0.95, in March 2025
    assert shown.currency == "EUR"


def test_without_a_start_the_range_takes_the_eleven_months_before_its_end(
    conn: sqlite3.Connection,
) -> None:
    shown = his(conn, last=date(2025, 12, 31))
    assert (shown.first, shown.last) == (date(2025, 1, 1), date(2025, 12, 31))
    assert shown.totals == amounts("80", "0")
    assert shown.year_before is None


def test_both_people_together(conn: sqlite3.Connection) -> None:
    shown = his(conn, owner=None, first=date(2026, 1, 1))
    assert shown.totals == amounts("230", "96", "-18")
    accounts = {
        (holding.name, holding.owner): holding.paid.interest
        for holding in shown.holdings
        if holding.account
    }
    # Both have an account called Migros: each is a line of its own, named after its owner.
    assert accounts == {
        ("IBKR USD", None): Decimal(18),
        (f"Migros ({OWNER})", OWNER): Decimal(5),
        (f"Migros ({PARTNER})", PARTNER): Decimal(1),
    }
    # Her statements start in 2026: the year before is not known for both.
    assert shown.year_before is None
    hers = his(conn, owner=PARTNER)
    assert hers.totals == amounts("0", "1")
    assert hers.first == date(2025, 9, 1)


def test_a_range_that_ends_before_it_starts_has_nothing(conn: sqlite3.Connection) -> None:
    shown = his(conn, first=date(2026, 8, 1), last=date(2026, 1, 31))
    assert (shown.months, shown.holdings, shown.totals) == ((), (), payouts.NOTHING)


def test_nothing_committed_has_no_range(conn: sqlite3.Connection) -> None:
    empty = db.connect(":memory:")
    db.migrate(empty, CLOSED_AT)
    shown = payouts.run(empty)
    assert (shown.first, shown.through, shown.months, shown.currency) == (None, None, (), "EUR")


def test_without_a_rate_for_the_currency_asked_nothing_is_converted_and_it_is_said(
    conn: sqlite3.Connection,
) -> None:
    shown = his(conn, currency="GBP")
    assert (shown.totals, shown.holdings) == (payouts.NOTHING, ())
    assert shown.missing == ("no exchange rate is stored for GBP: run `scrooge prices`",)


def test_the_last_twelve_months_are_added_up_whatever_the_range(conn: sqlite3.Connection) -> None:
    shown = his(conn, first=date(2026, 1, 1), last=date(2026, 3, 31))
    assert shown.totals == amounts("90", "0", "-13.50")
    assert shown.trailing == amounts("230", "95", "-18")
    assert his(conn).trailing == his(conn).totals
    # A range that ends before it starts still says what the last twelve months paid.
    assert his(conn, first=date(2026, 8, 1), last=date(2026, 1, 31)).trailing == shown.trailing


GBP = {"account": "Revolut GBP", "currency": "GBP", "source_file": "revolut.csv"}


@pytest.fixture
def lone() -> Iterator[sqlite3.Connection]:
    """A database of one person's dividends in dollars, in pounds and in francs, with a rate for
    dollars only."""
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    span = (date(2026, 1, 1), date(2026, 6, 30))
    close(
        connection,
        OWNER,
        span,
        span[1],
        (
            "ibkr",
            [
                # IBKR may name what paid with its ISIN in brackets, the tax withheld without.
                paid("100", date(2026, 3, 16), "1", "ACME(US0000000001)", **DIVIDEND, **USD),
                paid("-15", date(2026, 3, 16), "2", "ACME", **TAX, **USD),
                # Only the ISIN is an instrument's symbol: the holding is the ISIN.
                paid("10", date(2026, 4, 1), "3", "FUND(IE0000000002)", **DIVIDEND, **USD),
                paid("-1", date(2026, 4, 1), "4", "IE0000000002", **TAX, **USD),
                # Neither is: the holding is the symbol, and only an ISIN is the ISIN.
                paid("10", date(2026, 5, 1), "5", "WIDG (US0000000003)", **DIVIDEND, **USD),
                paid("10", date(2026, 5, 2), "6", "(US0000000004)", **DIVIDEND, **USD),
            ],
        ),
        ("revolut", [paid("50", date(2026, 4, 10), "1", "BRIT", **DIVIDEND, **GBP)]),
        ("migros", [paid("5", date(2026, 5, 31), "1", **INTEREST)]),
    )
    store.instrument_id(connection, "ACME", "Stocks", "USD", name="ACME Corp")
    store.instrument_id(connection, "IE0000000002", "Funds", "USD", name="ACME Fund")
    store.set_rate(connection, date(2026, 1, 1), "USD", Decimal("0.90"), source="fetched:yahoo")
    yield connection
    connection.close()


def test_rows_in_a_currency_without_a_rate_are_left_out_and_said(lone: sqlite3.Connection) -> None:
    shown = his(lone)
    # 130 dollars at 0.90 and 5 francs: the 50 pounds are not there.
    assert shown.totals == amounts("117", "5", "-14.40")
    assert "BRIT" not in {holding.key for holding in shown.holdings}
    assert shown.missing == ("no exchange rate is stored for GBP: run `scrooge prices`",)
    # In pounds, the pounds are counted and the rest cannot be.
    pounds = his(lone, currency="GBP")
    assert pounds.totals == amounts("50", "0")
    assert pounds.missing == shown.missing


def test_a_symbol_with_its_isin_in_brackets_is_the_same_holding(lone: sqlite3.Connection) -> None:
    holdings = {holding.key: holding for holding in his(lone).holdings}
    assert set(holdings) == {"ACME", "IE0000000002", "WIDG", "US0000000004", "Migros"}
    assert holdings["ACME"].paid == amounts("90", "0", "-13.50")
    assert (holdings["ACME"].name, holdings["ACME"].count) == ("ACME Corp", 2)
    assert holdings["IE0000000002"].paid == amounts("9", "0", "-0.90")
    assert holdings["IE0000000002"].name == "ACME Fund"
    assert holdings["WIDG"].symbol is None


@pytest.fixture
def household() -> Iterator[sqlite3.Connection]:
    """A database of two people who both hold ACME and both have an account called Migros,
    and one of whom has an account no one else has."""
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    span = (date(2026, 1, 1), date(2026, 6, 30))
    broker = {"account": "IBKR", "source_file": "ibkr.csv"}
    cash = {"account": "Revolut", "source_file": "revolut.csv"}
    close(
        connection,
        OWNER,
        span,
        span[1],
        ("ibkr", [paid("100", date(2026, 3, 16), "1", "ACME", **DIVIDEND, **broker)]),
        ("migros", [paid("10", date(2026, 5, 31), "1", **INTEREST)]),
    )
    close(
        connection,
        PARTNER,
        span,
        span[1],
        ("ibkr", [paid("50", date(2026, 3, 16), "1", "ACME", **DIVIDEND, **broker)]),
        ("migros", [paid("20", date(2026, 5, 31), "1", **INTEREST)]),
        ("revolut", [paid("1", date(2026, 4, 30), "1", **INTEREST, **cash)]),
    )
    store.instrument_id(connection, "ACME", "Stocks", "CHF", name="ACME Corp")
    yield connection
    connection.close()


def test_for_everyone_a_holding_both_have_is_one_and_their_accounts_of_a_name_two(
    household: sqlite3.Connection,
) -> None:
    lines = {
        holding.name: (holding.key, holding.account, holding.owner, holding.paid)
        for holding in his(household, owner=None).holdings
    }
    assert lines == {
        # What a symbol names is the household's, whoever holds it.
        "ACME Corp": ("ACME", None, None, amounts("150", "0")),
        f"Migros ({PARTNER})": ("Migros", "Migros", PARTNER, amounts("0", "20")),
        f"Migros ({OWNER})": ("Migros", "Migros", OWNER, amounts("0", "10")),
        # A name only one person has needs no one's name after it, nor its link anyone's.
        "Revolut": ("Revolut", "Revolut", None, amounts("0", "1")),
    }
    # One person's view is as it was: their accounts, without their name.
    hers = his(household, owner=PARTNER).holdings
    assert [(holding.name, holding.owner) for holding in hers] == [
        ("ACME Corp", None),
        ("Migros", None),
        ("Revolut", None),
    ]
