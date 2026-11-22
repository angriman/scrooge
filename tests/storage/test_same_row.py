import sqlite3
from datetime import date

from scrooge.storage import store
from tests.storage.helpers import txn


def test_a_row_is_the_same_by_day_currency_amount_and_text() -> None:
    row = txn("-12.50", date=date(2026, 9, 10), description="KONSUM")
    assert store.transaction_same_row(row) == store.same_row(
        "2026-09-10", "CHF", -12_500_000_000, "KONSUM"
    )


def test_other_booking_days_or_ids_do_not_make_another_row() -> None:
    row = txn("-12.50", booking_date=date(2026, 9, 12), source_ref="9")
    assert store.transaction_same_row(row) == store.transaction_same_row(txn("-12.50"))
    assert store.transaction_same_row(row) != store.transaction_same_row(txn("-12.51"))
    assert store.transaction_same_row(row) != store.transaction_same_row(
        txn("-12.50", currency="EUR")
    )


def test_a_stored_row_has_the_key_of_the_statement_row_it_came_from(
    conn: sqlite3.Connection,
) -> None:
    row = txn("-12.50", date=date(2026, 9, 10), booking_date=date(2026, 9, 12))
    owner = conn.execute("INSERT INTO owners (name) VALUES ('Ada') RETURNING id").fetchone()[0]
    account = store.ensure_account(
        conn, owner, name="Migros", kind="bank", currency="CHF", source="migros"
    )
    file = store.upsert_statement_file(
        conn,
        owner,
        source="migros",
        filename="a.csv",
        sha256="a" * 64,
        period=(date(2026, 9, 1), date(2026, 9, 30)),
        reconciled="yes",
        imported_at="2026-10-06T12:00:00+00:00",
    )
    store.insert_transaction(
        conn,
        "t1",
        row,
        account=account,
        category=None,
        counterparty_owner=None,
        statement_file=file,
    )
    stored = store.statement_rows(conn, "migros", [account], (date(2026, 9, 1), date(2026, 9, 30)))
    assert stored["t1"].same == store.transaction_same_row(row)
