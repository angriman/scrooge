"""Holding value history and latest committed transactions (`scrooge.holding_detail`)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal

import pytest

from scrooge import categories, holding_detail, query, staging
from scrooge.models import Kind, Transaction
from scrooge.networth import BILLS, ETFS, STOCKS
from scrooge.storage import db, store
from tests.storage.helpers import OWNER, PARTNER, batch, take_back
from tests.test_networth import NOW, account, bill, bought, close, day, ibkr, price, rate

D = Decimal


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def _txn(
    day_: date,
    amount: str,
    *,
    kind: Kind,
    category: str,
    counterparty: str,
    description: str = "",
    currency: str = "USD",
    account_name: str = "IBKR USD",
) -> Transaction:
    return Transaction(
        date=day_,
        booking_date=day_,
        account=account_name,
        amount=D(amount),
        currency=currency,
        description=description or counterparty,
        counterparty=counterparty,
        kind=kind,
        category=category,
        source_file="ibkr.csv",
        source_ref=f"{day_.isoformat()}:{counterparty}:{amount}",
    )


def test_series_and_transactions_for_split_adjusted_stock_and_payouts(
    conn: sqlite3.Connection,
) -> None:
    div = _txn(
        day(20),
        "12.50",
        kind=Kind.DIVIDEND,
        category=categories.DIVIDENDS,
        counterparty="ACME(US0000000001)",
        description="ACME Cash Dividend",
    )
    tax = _txn(
        day(20),
        "-1.88",
        kind=Kind.TAX,
        category=categories.WITHHOLDING_TAX,
        counterparty="ACME(US0000000001)",
        description="ACME Withholding Tax",
    )
    fee = _txn(
        day(5),
        "-1.00",
        kind=Kind.COMMISSION,
        category=categories.FEES,
        counterparty="ACME",
        description="Commission",
    )
    unrelated = _txn(
        day(21),
        "50.00",
        kind=Kind.DIVIDEND,
        category=categories.DIVIDENDS,
        counterparty="OTHER",
        description="OTHER Dividend",
    )
    close(
        conn,
        ibkr(
            bought("ACME", "10", "-1000", day(5), asset_tab="Stocks"),
            fee,
            div,
            tax,
            unrelated,
            opening="2000",
        ),
    )
    price(conn, "ACME", day(5), "102")
    price(conn, "ACME", day(10), "55")
    price(conn, "ACME", day(30), "60")
    instrument = conn.execute("SELECT id FROM instruments WHERE symbol = 'ACME'").fetchone()[0]
    store.add_split(conn, instrument, day(10), 2, 1)
    rate(conn, "USD", day(1), "0.80")

    found = holding_detail.run(conn, symbol="ACME", currency="CHF", asset=STOCKS)
    assert found is not None
    assert found.series_error is None
    assert found.series[0] == holding_detail.Point(day(5), D("816.00"))
    # After 2-for-1 split on day 10: 20 shares * 60 * 0.80 = 960.00 CHF on day 30.
    assert found.series[-1] == holding_detail.Point(day(30), D("960.00"))
    assert found.total_transactions == 4
    assert [row.kind for row in found.transactions] == [
        Kind.DIVIDEND,
        Kind.TAX,
        Kind.SECURITY_TRADE,
        Kind.COMMISSION,
    ] or {row.kind for row in found.transactions} == {
        Kind.DIVIDEND,
        Kind.TAX,
        Kind.SECURITY_TRADE,
        Kind.COMMISSION,
    }
    assert all(row.counterparty != "OTHER" for row in found.transactions)


def test_bill_series_and_split_of_interest_rows(conn: sqlite3.Connection) -> None:
    close(
        conn,
        ibkr(
            bill("10000", "-9850", day(5)),
            bill("-5000", "4960", day(20)),
            opening="20000",
        ),
    )
    # Give the split interest row a completely different counterparty to verify split_of matching.
    conn.execute(
        "UPDATE transactions SET counterparty = 'TREASURY INTEREST' WHERE split_of IS NOT NULL"
    )

    found = holding_detail.run(
        conn, symbol="912797ZZ1", instrument_currency="USD", asset=BILLS, currency="USD"
    )
    assert found is not None
    assert found.series[0].day == day(5)
    assert found.series[-1].day == day(30)
    assert found.series[-1].value > D("4925.00")
    # Buy + partial sale principal + split_of interest row = 3 transactions.
    assert found.total_transactions == 3
    assert any(row.counterparty == "TREASURY INTEREST" for row in found.transactions)


def test_single_day_holding_and_downsampling_long_span(conn: sqlite3.Connection) -> None:
    # Single-day holding bought on the last day of the statement.
    close(conn, ibkr(bought("ONE", "5", "-250", day(30), asset_tab="Stocks")))
    price(conn, "ONE", day(30), "52")
    single = holding_detail.run(conn, symbol="ONE", currency="USD")
    assert single is not None
    assert single.series == (holding_detail.Point(day(30), D("260.00")),)

    # Long span (> 180 days): prices extend 260 days past the statement.
    far = day(30) + timedelta(days=260)
    price(conn, "ONE", far, "60")
    long_detail = holding_detail.run(conn, symbol="ONE", currency="USD")
    assert long_detail is not None
    assert len(long_detail.series) == holding_detail.MAX_POINTS
    assert long_detail.series[0].day == day(30)
    assert long_detail.series[-1].day == far


def test_missing_fx_rate_isolates_chart_while_returning_transactions(
    conn: sqlite3.Connection,
) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15))))
    price(conn, "WIDG", day(30), "55")

    found = holding_detail.run(conn, symbol="WIDG", currency="JPY")
    assert found is not None
    assert found.series == ()
    assert found.series_error is not None
    assert "USD" in found.series_error or "JPY" in found.series_error
    assert found.total_transactions == 1
    assert len(found.transactions) == 1


def test_owner_and_same_symbol_different_currency_isolation(conn: sqlite3.Connection) -> None:
    usd_div = _txn(
        day(20),
        "10.00",
        kind=Kind.DIVIDEND,
        category=categories.DIVIDENDS,
        counterparty="ACME",
        currency="USD",
        account_name="IBKR USD",
    )
    eur_buy = bought(
        "ACME",
        "4",
        "-400",
        day(10),
        asset_tab="Stocks",
        account="IBKR EUR",
        currency="EUR",
    )
    eur_div = _txn(
        day(22),
        "8.00",
        kind=Kind.DIVIDEND,
        category=categories.DIVIDENDS,
        counterparty="ACME",
        currency="EUR",
        account_name="IBKR EUR",
    )
    close(
        conn,
        ibkr(bought("ACME", "10", "-1000", day(5), asset_tab="Stocks"), usd_div, opening="2000"),
        account("ibkr", "IBKR EUR", "EUR", "1000", eur_buy, eur_div),
        owner=OWNER,
    )
    close(
        conn,
        ibkr(bought("ACME", "3", "-300", day(12), asset_tab="Stocks"), opening="1000"),
        owner=PARTNER,
    )
    for row_id, curr in conn.execute("SELECT id, currency FROM instruments WHERE symbol = 'ACME'"):
        store.add_price(
            conn,
            int(row_id),
            day(30),
            D("110") if curr == "USD" else D("105"),
            source="manual",
            fetched_at=NOW.isoformat(),
        )
    rate(conn, "USD", day(1), "0.80")
    rate(conn, "EUR", day(1), "0.95")

    usd_owner = holding_detail.run(
        conn,
        symbol="ACME",
        instrument_currency="USD",
        asset=STOCKS,
        owner=OWNER,
        currency="CHF",
    )
    assert usd_owner is not None
    assert usd_owner.total_transactions == 2
    assert {r.owner for r in usd_owner.transactions} == {OWNER}
    assert {r.currency for r in usd_owner.transactions} == {"USD"}
    assert usd_owner.series[-1].value == D("880.00")

    eur_owner = holding_detail.run(
        conn,
        symbol="ACME",
        instrument_currency="EUR",
        asset=STOCKS,
        owner=OWNER,
        currency="CHF",
    )
    assert eur_owner is not None
    assert eur_owner.total_transactions == 2
    assert {r.currency for r in eur_owner.transactions} == {"EUR"}

    partner_only = holding_detail.run(
        conn,
        symbol="ACME",
        instrument_currency="USD",
        asset=STOCKS,
        owner=PARTNER,
        currency="CHF",
    )
    assert partner_only is not None
    assert partner_only.total_transactions == 1
    assert partner_only.transactions[0].owner == PARTNER
    assert partner_only.series[-1].value == D("264.00")

    # Limit caps returned rows while preserving total_transactions.
    capped = holding_detail.run(
        conn, symbol="ACME", instrument_currency="USD", owner=OWNER, currency="CHF", limit=1
    )
    assert capped is not None
    assert len(capped.transactions) == 1
    assert capped.total_transactions == 2


def test_unknown_holding_or_owner_handling(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15))))
    price(conn, "WIDG", day(30), "55")

    assert holding_detail.run(conn, symbol="MISSING") is None
    assert holding_detail.run(conn, symbol="WIDG", asset=STOCKS) is None
    with pytest.raises(query.QueryError, match="Nobody"):
        holding_detail.run(conn, symbol="WIDG", owner="Nobody")

    # Staged-only holding is not committed -> None.
    take_back(conn, OWNER)
    staging.stage(conn, batch(ibkr(bought("WIDG", "10", "-500", day(15)))), NOW)
    assert holding_detail.run(conn, symbol="WIDG", asset=ETFS) is None
