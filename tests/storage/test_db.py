import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from finanze.storage import db
from tests.storage.conftest import NOW


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def test_a_new_database_gets_the_whole_schema(conn: sqlite3.Connection) -> None:
    assert {"owners", "accounts", "transactions", "trades", "balances", "prices"} <= _tables(conn)
    assert conn.execute("SELECT version, applied_at FROM schema_version").fetchall()[0][:] == (
        1,
        "2026-10-06T12:00:00+00:00",
    )


def test_migrating_again_changes_nothing(conn: sqlite3.Connection) -> None:
    assert db.migrate(conn, NOW) == []
    applied = conn.execute("SELECT count(*) FROM schema_version").fetchone()[0]
    assert applied == len(db.migrations())


def test_no_column_can_hold_a_float(conn: sqlite3.Connection) -> None:
    for table in _tables(conn):
        strict = conn.execute("SELECT strict FROM pragma_table_list(?)", (table,)).fetchone()[0]
        assert strict == 1, table
        columns = conn.execute("SELECT name, type FROM pragma_table_info(?)", (table,))
        for column in columns.fetchall():
            assert column["type"] in {"INTEGER", "TEXT"}, (table, column["name"])


def test_foreign_keys_are_enforced(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute(
            "INSERT INTO accounts (owner_id, name, kind, currency) VALUES (9, 'x', 'bank', 'CHF')"
        )


def test_dates_must_be_real_iso_dates(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO owners (id, name) VALUES (1, 'A')")
    for bad in ("2026-09-31", "30.09.2026", "2026-09-15"):  # the last is not a month start
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute("INSERT INTO months VALUES (1, ?, 'open', NULL)", (bad,))


def test_a_failing_migration_leaves_nothing_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = [(1, "CREATE TABLE a (x INTEGER) STRICT;\nCREATE TABLE a (x INTEGER) STRICT;")]
    monkeypatch.setattr(db, "migrations", lambda: broken)
    conn = db.connect(":memory:")
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        db.migrate(conn, NOW)
    assert _tables(conn) == {"schema_version"}
    assert conn.execute("SELECT count(*) FROM schema_version").fetchone()[0] == 0


def test_a_database_from_newer_code_is_refused(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO schema_version VALUES (99, 'x')")
    with pytest.raises(db.MigrationError, match="version 99"):
        db.migrate(conn, NOW)


def test_a_misnamed_migration_file_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Entry:
        name = "first.sql"

    class Folder:
        def iterdir(self) -> list[Entry]:
            return [Entry()]

    monkeypatch.setattr("importlib.resources.files", lambda package: Folder())
    with pytest.raises(db.MigrationError, match="NNNN"):
        db.migrations()


def test_a_transaction_is_all_or_nothing(conn: sqlite3.Connection) -> None:
    def failing() -> None:
        with db.transaction(conn):
            conn.execute("INSERT INTO owners (name) VALUES ('A')")
            raise RuntimeError

    with pytest.raises(RuntimeError):
        failing()
    assert conn.execute("SELECT count(*) FROM owners").fetchone()[0] == 0
    with db.transaction(conn):
        conn.execute("INSERT INTO owners (name) VALUES ('A')")
    assert conn.execute("SELECT count(*) FROM owners").fetchone()[0] == 1


def test_timestamps_must_carry_a_timezone() -> None:
    with pytest.raises(ValueError, match="timezone"):
        db.timestamp(datetime(2026, 10, 6, 12, 0))  # noqa: DTZ001  # the naive value under test


def test_backup_copies_the_database(conn: sqlite3.Connection, tmp_path: Path) -> None:
    conn.execute("INSERT INTO owners (name) VALUES ('A')")
    target = tmp_path / "backups" / "copy.db"
    db.backup(conn, target)
    copy = db.connect(target)
    assert copy.execute("SELECT name FROM owners").fetchone()[0] == "A"
    copy.close()


def test_the_schema_version_can_be_read_without_changing_anything(
    conn: sqlite3.Connection,
) -> None:
    assert db.latest() == len(db.migrations())
    assert db.version(conn) == db.latest()
    empty = db.connect(":memory:")
    assert db.version(empty) == 0  # no migration has run on it
    assert _tables(empty) == set()
    empty.close()


def test_a_snapshot_is_the_whole_database_in_one_file_that_stays_one(tmp_path: Path) -> None:
    # In WAL mode a database is three files, and cannot be read where nothing can be written.
    live = db.connect(tmp_path / "live.db")
    assert live.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    db.migrate(live, NOW)
    live.execute("INSERT INTO owners (name) VALUES ('A')")
    folder = tmp_path / "copy"
    folder.mkdir()
    db.snapshot(live, folder / "finanze.db")
    live.close()
    assert [file.name for file in folder.iterdir()] == ["finanze.db"]
    with db.reading(folder / "finanze.db") as copy:
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert copy.execute("SELECT name FROM owners").fetchone()[0] == "A"
        assert db.version(copy) == db.latest()
    assert [file.name for file in folder.iterdir()] == ["finanze.db"]


def test_a_reading_connection_cannot_change_the_database(tmp_path: Path) -> None:
    path = tmp_path / "with space" / "finanze.db"
    path.parent.mkdir()
    writer = db.connect(path)
    db.migrate(writer, NOW)
    writer.close()
    with db.reading(path) as conn:
        assert conn.execute("SELECT count(*) FROM owners").fetchone()[0] == 0
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO owners (name) VALUES ('Lukas')")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")
