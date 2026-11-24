"""The pensions as a group: what each is worth, how it changed, what was paid in, and what it
returned where that can be known."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from scrooge import pensions, staging
from scrooge.models import CashBalance, Kind, Transaction
from scrooge.networth import PENSION, PILLAR2
from scrooge.pensions import Change
from scrooge.pipeline import Statement
from scrooge.storage import db
from tests.storage.helpers import OWNER, PARTNER, certificate, certified, statement
from tests.test_networth import NOW, account, bought, close, ibkr, price, rate, spent

D = Decimal
UNITS = "CH0000000001"
PAID_IN = {"kind": Kind.TRANSFER, "category": "Investimenti"}
IN_3A = {"account": "Finpension 3a", "currency": "CHF"}


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def finpension(*rows: Transaction) -> Statement:
    """One report of a 3a portfolio from its first deposit, in September 2025, to the end of
    September 2026, as finpension gives one."""
    closing = sum((row.amount for row in rows), D(0))
    balance = CashBalance("Finpension 3a", "CHF", D(0), closing)
    rows = tuple(replace(row, source_file="3a.tsv") for row in rows)
    return statement(
        "finpension",
        rows,
        [balance],
        filename="3a.tsv",
        month=date(2025, 9, 1),
        end=date(2026, 9, 30),
    )


def three_a(conn: sqlite3.Connection) -> None:
    """1000 paid in and 10 units bought at 90 in September 2025; 10 at 100 at the year's end;
    1000 more paid in and 10 units bought at 95 in January; a fee of 5 in June; 110 a unit at
    the end of September 2026."""
    rows = (
        spent("1000", date(2025, 9, 10), **PAID_IN, **IN_3A),
        bought(UNITS, "10", "-900", date(2025, 9, 11), asset_tab="Funds", **IN_3A),
        spent("1000", date(2026, 1, 15), **PAID_IN, **IN_3A),
        bought(UNITS, "10", "-950", date(2026, 1, 16), asset_tab="Funds", **IN_3A),
        spent("-5", date(2026, 6, 30), kind=Kind.COMMISSION, category="Commissioni", **IN_3A),
    )
    close(conn, finpension(*rows), month=date(2025, 9, 1))
    price(conn, UNITS, date(2025, 12, 31), "100")
    price(conn, UNITS, date(2026, 9, 30), "110")


def test_nothing_committed_is_no_pension(conn: sqlite3.Connection) -> None:
    assert pensions.run(conn) == pensions.Pensions("EUR", None, ())


def test_without_a_pension_account_there_is_none_to_list(conn: sqlite3.Connection) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000"), ibkr())
    shown = pensions.run(conn, currency="CHF")
    assert (shown.day, shown.pensions) == (date(2026, 9, 30), ())


def test_a_3a_portfolio_is_its_units_and_its_cash_measured_as_an_investment_account(
    conn: sqlite3.Connection,
) -> None:
    three_a(conn)
    shown = pensions.run(conn, currency="CHF")
    assert shown.day == date(2026, 9, 30)
    [pension] = shown.pensions
    # 20 units at 110, and the 145 not invested.
    assert (pension.owner, pension.account, pension.pillar) == (OWNER, "Finpension 3a", PENSION)
    assert (pension.value, pension.cash) == (D("2345.00"), D("145.00"))
    assert (pension.stated, pension.typed) == (None, False)
    year, to_date = pension.changes
    # From the end of 30 September 2025: 10 units at 90 and 100 of cash; 1000 paid in since.
    assert year == Change(
        "1y", date(2025, 9, 30), D("1000.00"), D("1000.00"), D("22.83"), year.mwr, True, None
    )
    # 10% to the year's end, then 2345 out of the 2100 there was once January's 1000 was in
    # (the units bought at 95 are valued at that price the day they are bought).
    assert to_date == Change(
        "ytd", date(2025, 12, 31), D("1100.00"), D("1000.00"), D("11.67"), to_date.mwr
    )
    assert year.mwr is not None
    assert to_date.mwr is not None
    assert year.mwr > to_date.mwr > 0


def test_a_pension_fund_has_its_stated_figure_and_no_money_paid_in_or_return(
    conn: sqlite3.Connection,
) -> None:
    three_a(conn)
    scans = (certificate(date(2025, 12, 31), "40000"), certificate(date(2026, 6, 30), "43000"))
    staging.stage(conn, certified(*scans), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    shown = pensions.run(conn, currency="CHF")
    assert [(p.pillar, p.account) for p in shown.pensions] == [
        (PILLAR2, "Pillar 2 AXA"),
        (PENSION, "Finpension 3a"),
    ]
    fund = shown.pensions[0]
    assert (fund.value, fund.cash, fund.stated, fund.typed) == (
        D("43000.00"),
        None,
        date(2026, 6, 30),
        False,
    )
    # Nothing is known of it twelve months back, before its first certificate: not nothing.
    assert fund.changes == (
        Change("1y", date(2025, 9, 30), None, None, None, None),
        Change("ytd", date(2025, 12, 31), D("40000.00"), None, None, None),
    )


def test_a_figure_typed_for_a_pension_fund_is_the_one_it_is_carried_at(
    conn: sqlite3.Connection,
) -> None:
    staging.stage(conn, certified(certificate(date(2026, 9, 5), "30000")), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    staging.type_balance(
        conn,
        owner=OWNER,
        account="Pillar 2 AXA",
        day=date(2026, 9, 20),
        amount=D(31000),
        currency="CHF",
    )
    [fund] = pensions.run(conn, currency="CHF").pensions
    assert (fund.value, fund.stated, fund.typed) == (D("31000.00"), date(2026, 9, 20), True)


def test_each_person_has_their_own_and_one_can_be_shown_alone(conn: sqlite3.Connection) -> None:
    three_a(conn)
    staging.stage(conn, certified(certificate(date(2026, 9, 5), "3000"), owner=PARTNER), NOW)
    staging.commit(conn, owner=PARTNER, now=NOW)
    everyone = pensions.run(conn, currency="CHF")
    assert [(p.owner, p.pillar) for p in everyone.pensions] == [
        (PARTNER, PILLAR2),
        (OWNER, PENSION),
    ]
    assert [p.owner for p in pensions.run(conn, owner=PARTNER, currency="CHF").pensions] == [
        PARTNER
    ]


def test_a_pension_in_a_currency_without_a_rate_is_left_out_and_said_to_be(
    conn: sqlite3.Connection,
) -> None:
    three_a(conn)
    shown = pensions.run(conn, currency="EUR")
    assert shown.pensions == ()
    assert shown.left_out == ("Finpension 3a (Lukas)",)
    assert shown.missing == ("no exchange rate is stored for EUR: run `scrooge prices`",)
    rate(conn, "EUR", date(2025, 9, 1), "0.5")  # two euros a franc, every day since
    [pension] = pensions.run(conn, currency="EUR").pensions
    assert (pension.value, pension.cash) == (D("4690.00"), D("290.00"))
