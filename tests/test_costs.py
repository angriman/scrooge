import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from scrooge import costs
from scrooge.costs import Account, Cost
from scrooge.models import Kind, Transaction
from scrooge.storage import db, store
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close, txn

START, END = date(2025, 1, 1), date(2026, 8, 31)
USD = {"account": "IBKR USD", "currency": "USD", "source_file": "ibkr.csv"}
GBP = {"account": "IBKR GBP", "currency": "GBP", "source_file": "ibkr gbp.csv"}
FUND = {"account": "Finpension", "source_file": "finpension.csv"}
COMMISSION = {"kind": Kind.COMMISSION, "category": "Commissioni"}
TAX = {"kind": Kind.TAX, "category": "Ritenute"}
DIVIDEND = {"kind": Kind.DIVIDEND, "category": "Dividendi"}
INTEREST = {"kind": Kind.INTEREST, "category": "Interessi"}


def row(amount: str, day: date, ref: str, **fields: Any) -> Transaction:
    return txn(amount, date=day, source_ref=ref, counterparty="ACME", **fields)


LUKAS_USD = [
    row("100", date(2025, 3, 15), "1", **DIVIDEND, **USD),
    row("-2", date(2025, 3, 15), "2", **COMMISSION, **USD),
    row("100", date(2026, 3, 16), "3", **DIVIDEND, **USD),
    row("-15", date(2026, 3, 16), "4", **TAX, **USD),
    row("-3", date(2026, 3, 16), "5", **COMMISSION, **USD),
    # A commission refunded lowers what commissions cost.
    row("1", date(2026, 6, 1), "6", **COMMISSION, **USD),
]
# No rate is stored for pounds: this commission cannot be converted.
LUKAS_GBP = [row("-7", date(2026, 5, 4), "1", **COMMISSION, **GBP)]
LUKAS_FUND = [
    row("50", date(2026, 4, 10), "1", **DIVIDEND, **FUND),
    row("-10", date(2026, 4, 10), "2", **COMMISSION, **FUND),
]
LUKAS = [
    # A bank's fee is no cost of investing; the tax withheld from its interest is.
    row("-5", date(2026, 5, 1), "1", **COMMISSION),
    row("5", date(2026, 5, 31), "2", **INTEREST),
    row("-1.75", date(2026, 5, 31), "3", **TAX),
]
ANNA_USD = [row("-4", date(2026, 2, 2), "1", **COMMISSION, **USD)]


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, CLOSED_AT)
    files = (("ibkr", LUKAS_USD), ("ibkr", LUKAS_GBP), ("finpension", LUKAS_FUND))
    close(connection, OWNER, (START, END), END, *files, ("migros", LUKAS))
    close(connection, PARTNER, (date(2026, 1, 1), END), END, ("ibkr", ANNA_USD))
    for day, currency, rate in (
        (date(2025, 1, 1), "EUR", "0.95"),
        (date(2025, 3, 14), "USD", "0.80"),
        (date(2026, 3, 16), "USD", "0.90"),
    ):
        store.set_rate(connection, day, currency, Decimal(rate), source="fetched:yahoo")
    yield connection
    connection.close()


def cost(commissions: str, tax: str) -> Cost:
    return Cost(Decimal(commissions), Decimal(tax))


def test_each_year_has_its_commissions_and_tax_each_row_at_its_day_s_rate(
    conn: sqlite3.Connection,
) -> None:
    shown = costs.run(conn, owner=OWNER, currency="CHF")
    assert (shown.currency, shown.through) == ("CHF", END)
    # Dollars at 0.80, then at 0.90 from March 2026; the bank's fee is not there, nor the
    # commission in pounds.
    first, second = shown.years
    assert (first.year, first.cost, first.paid, first.last) == (
        2025,
        cost("1.60", "0"),
        Decimal(80),
        date(2025, 12, 31),
    )
    assert first.whole
    assert first.tax_share == 0
    assert (second.year, second.cost, second.paid, second.last) == (
        2026,
        cost("11.80", "15.25"),
        Decimal(145),
        END,
    )
    assert second.cost.total == Decimal("27.05")
    # The year of the last day committed is counted so far.
    assert not second.whole
    assert second.tax_share == Decimal("0.105")
    assert shown.totals == cost("13.40", "15.25")
    assert shown.paid == Decimal(225)
    assert shown.tax_share == Decimal("0.068")


def test_each_account_what_cost_the_most_first(conn: sqlite3.Connection) -> None:
    assert costs.run(conn, owner=OWNER, currency="CHF").accounts == (
        Account(OWNER, "IBKR USD", "IBKR USD", cost("3.40", "13.50")),
        Account(OWNER, "Finpension", "Finpension", cost("10.00", "0")),
        Account(OWNER, "Migros", "Migros", cost("0", "1.75")),
    )


def test_both_people_together_name_their_accounts_of_a_name_after_each(
    conn: sqlite3.Connection,
) -> None:
    shown = costs.run(conn, currency="CHF")
    assert [year.cost for year in shown.years] == [cost("1.60", "0"), cost("15.00", "15.25")]
    assert [(each.name, each.owner, each.cost) for each in shown.accounts] == [
        (f"IBKR USD ({OWNER})", OWNER, cost("3.40", "13.50")),
        ("Finpension", OWNER, cost("10.00", "0")),
        (f"IBKR USD ({PARTNER})", PARTNER, cost("3.20", "0")),
        ("Migros", OWNER, cost("0", "1.75")),
    ]
    # Her figures alone: her statements start in 2026, and nothing was paid her.
    hers = costs.run(conn, owner=PARTNER, currency="CHF")
    assert [year.year for year in hers.years] == [2026]
    assert hers.accounts == (Account(PARTNER, "IBKR USD", "IBKR USD", cost("3.20", "0")),)
    assert hers.tax_share is None


def test_rows_in_a_currency_without_a_rate_are_left_out_and_said(conn: sqlite3.Connection) -> None:
    said = "no exchange rate is stored for GBP: run `scrooge prices`"
    assert costs.run(conn, owner=OWNER, currency="CHF").missing == (said,)
    # In pounds, only the pounds are counted.
    pounds = costs.run(conn, owner=OWNER, currency="GBP")
    assert pounds.totals == cost("7.00", "0")
    assert pounds.paid == 0
    assert [each.account for each in pounds.accounts] == ["IBKR GBP"]
    # Nothing else can be: no rate is stored for pounds.
    assert pounds.missing == (said,)


def test_nothing_committed_costs_nothing() -> None:
    empty = db.connect(":memory:")
    db.migrate(empty, CLOSED_AT)
    shown = costs.run(empty)
    assert (shown.through, shown.years, shown.totals, shown.accounts, shown.currency) == (
        None,
        (),
        costs.NOTHING,
        (),
        "EUR",
    )
