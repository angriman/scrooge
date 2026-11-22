"""What is kept of an instrument a statement names."""

import sqlite3

from finanze.storage import store


def name(conn: sqlite3.Connection, number: int) -> str:
    return str(conn.execute("SELECT name FROM instruments WHERE id = ?", (number,)).fetchone()[0])


def test_the_name_a_statement_gives_is_kept_once(conn: sqlite3.Connection) -> None:
    store.ensure_currency(conn, "CHF")
    first = store.instrument_id(conn, "CH0000000011", "Funds", "CHF")
    assert name(conn, first) == ""
    again = store.instrument_id(conn, "CH0000000011", "Funds", "CHF", name="ACME Index Fund")
    assert (again, name(conn, first)) == (first, "ACME Index Fund")
    store.instrument_id(conn, "CH0000000011", "Funds", "CHF", name="ACME Fund (other)")
    store.instrument_id(conn, "CH0000000011", "Funds", "CHF")
    assert name(conn, first) == "ACME Index Fund"
