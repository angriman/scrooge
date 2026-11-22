import sqlite3
from datetime import date

from scrooge.storage import store

DAY = date(2026, 9, 30)


def _file(conn: sqlite3.Connection, owner: int, sha: str, *, committed: bool) -> int:
    file = store.upsert_statement_file(
        conn,
        owner,
        source="migros",
        filename=f"{sha}.pdf",
        sha256=sha,
        period=(date(2026, 9, 1), DAY),
        reconciled="yes",
        imported_at="2026-10-06T12:00:00+00:00",
    )
    if committed:
        conn.execute(
            "UPDATE statement_files SET committed_at = '2026-10-06T12:00:00+00:00' WHERE id = ?",
            (file,),
        )
    return file


def test_a_row_counts_when_its_statement_is_committed_or_it_was_typed(
    conn: sqlite3.Connection,
) -> None:
    conn.execute("INSERT INTO currencies (code, decimals) VALUES ('CHF', 2)")
    owner = conn.execute("INSERT INTO owners (name) VALUES ('Ada') RETURNING id").fetchone()[0]
    committed = _file(conn, owner, "a" * 64, committed=True)
    staged = _file(conn, owner, "b" * 64, committed=False)
    # One account per row: a balance is one per account and day.
    for name, file in (("Committed", committed), ("Staged", staged), ("Typed", None)):
        account = conn.execute(
            "INSERT INTO accounts (owner_id, name, kind, currency) "
            "VALUES (?, ?, 'manual', 'CHF') RETURNING id",
            (owner, name),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO balances (account_id, date, amount_nano, source, statement_file_id) "
            "VALUES (?, ?, 1, ?, ?)",
            (account, DAY.isoformat(), "manual" if file is None else "statement", file),
        )
    kept = conn.execute(
        "SELECT a.name FROM balances b JOIN accounts a ON a.id = b.account_id "  # noqa: S608  # only the fixed condition of `store` is interpolated
        f"WHERE {store.committed_or_typed('b.statement_file_id')} ORDER BY a.name"
    ).fetchall()
    assert [row[0] for row in kept] == ["Committed", "Typed"]
