import sqlite3
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from finanze import staging
from finanze.models import CashBalance, Transaction
from finanze.storage import db
from finanze.storage.ids import transaction_ids
from tests.storage.conftest import NOW
from tests.storage.helpers import CLOSED_AT, OWNER, batch, dump, migros, statement, txn


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
            # a logo is an image, kept as the bytes it came as
            kinds = {"INTEGER", "TEXT"}
            if (table, column["name"]) == ("logos", "image"):
                kinds = {*kinds, "BLOB"}
            assert column["type"] in kinds, (table, column["name"])


def test_foreign_keys_are_enforced(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute(
            "INSERT INTO accounts (owner_id, name, kind, currency) VALUES (9, 'x', 'bank', 'CHF')"
        )


def test_dates_must_be_real_iso_dates(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO owners (id, name) VALUES (1, 'A')")
    for bad in ("2026-09-31", "30.09.2026"):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(
                "INSERT INTO statement_files (owner_id, source, filename, sha256, period_start, "
                "period_end, reconciled, imported_at) VALUES (1, 'x', 'f', 's', ?, '2026-10-01', "
                "'yes', 'x')",
                (bad,),
            )


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


@pytest.mark.parametrize("name", ["first.sql", "fix.py"])
def test_a_misnamed_migration_file_is_an_error(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    class Entry:
        def __init__(self, name: str) -> None:
            self.name = name

    class Folder:
        def iterdir(self) -> list[Entry]:
            return [Entry("__init__.py"), Entry("__pycache__"), Entry("notes.txt"), Entry(name)]

    monkeypatch.setattr("importlib.resources.files", lambda package: Folder())
    with pytest.raises(db.MigrationError, match=f"NNNN_description.{name.rpartition('.')[2]}"):
        db.migrations()


def test_a_python_migration_runs_in_its_own_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    def add_owner(conn: sqlite3.Connection) -> None:
        conn.execute("INSERT INTO a VALUES ('A')")

    def failing(conn: sqlite3.Connection) -> None:
        conn.execute("INSERT INTO a VALUES ('B')")
        raise db.MigrationError("stop")

    steps: list[tuple[int, db.Migration]] = [
        (1, "CREATE TABLE a (name TEXT) STRICT;"),
        (2, add_owner),
        (3, failing),
    ]
    monkeypatch.setattr(db, "migrations", lambda: steps)
    conn = db.connect(":memory:")
    with pytest.raises(db.MigrationError, match="stop"):
        db.migrate(conn, NOW)
    assert [row[0] for row in conn.execute("SELECT name FROM a")] == ["A"]
    assert db.version(conn) == 2


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


def test_migration_0003_commits_the_statements_of_committed_months(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = db.connect(":memory:")
    every = db.migrations()
    monkeypatch.setattr(db, "migrations", lambda: every[:2])
    assert db.migrate(conn, NOW) == [1, 2]
    monkeypatch.setattr(db, "migrations", lambda: every[:3])

    conn.execute("INSERT INTO owners (id, name) VALUES (1, 'A')")
    conn.execute("INSERT INTO currencies VALUES ('CHF', 2)")
    conn.execute(
        "INSERT INTO accounts (id, owner_id, name, kind, currency, source) "
        "VALUES (1, 1, 'Migros', 'bank', 'CHF', 'migros')"
    )
    conn.executescript(
        """
        INSERT INTO months VALUES (1, '2026-08-01', 'committed', '2026-09-02T08:00:00+00:00');
        INSERT INTO months VALUES (1, '2026-09-01', 'committed', '2026-10-02T08:00:00+00:00');
        INSERT INTO months VALUES (1, '2026-10-01', 'open', NULL);
        INSERT INTO source_waivers VALUES (1, '2026-10-01', 'revolut', '');
        INSERT INTO statement_files (id, owner_id, source, filename, sha256, period_start,
            period_end, reconciled, imported_at) VALUES
            (1, 1, 'migros', 'august', 'a', '2026-08-01', '2026-08-31', 'yes', 'x'),
            (2, 1, 'migros', 'both', 'b', '2026-08-15', '2026-09-30', 'yes', 'x'),
            (3, 1, 'migros', 'open', 'c', '2026-10-01', '2026-10-31', 'yes', 'x'),
            (4, 1, 'migros', 'across', 'd', '2026-09-20', '2026-10-10', 'yes', 'x');
        INSERT INTO transactions (id, account_id, date, booking_date, amount_nano, currency,
            description, kind, statement_file_id) VALUES
            ('t1', 1, '2026-08-05', '2026-08-05', -1000000000, 'CHF', 'x', 'expense', 1),
            ('t3', 1, '2026-10-05', '2026-10-05', -2000000000, 'CHF', 'y', 'expense', 3);
        INSERT INTO balances VALUES (1, '2026-09-30', 5, 'statement', 2);
        """
    )

    assert db.migrate(conn, NOW) == [3]
    stored = conn.execute("SELECT filename, committed_at FROM statement_files ORDER BY id")
    assert [tuple(row) for row in stored] == [
        ("august", "2026-09-02T08:00:00+00:00"),
        ("both", "2026-10-02T08:00:00+00:00"),  # the latest commit of the months it covers
        ("open", None),  # October is open: staged
        ("across", None),  # touches it
    ]
    linked = conn.execute("SELECT statement_file_id FROM statement_accounts ORDER BY 1")
    assert [row[0] for row in linked] == [1, 2, 3]  # the accounts the files fed
    visible = conn.execute("SELECT id FROM committed_transactions")
    assert [row[0] for row in visible] == ["t1"]
    assert _tables(conn).isdisjoint({"months", "source_waivers"})
    conn.close()


def test_migration_0004_gives_the_restaurants_category_the_name_of_its_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = db.connect(":memory:")
    every = db.migrations()
    monkeypatch.setattr(db, "migrations", lambda: every[:3])
    assert db.migrate(conn, NOW) == [1, 2, 3]
    monkeypatch.setattr(db, "migrations", lambda: every[:4])

    conn.executescript(
        """
        INSERT INTO categories (id, name, parent_id, side) VALUES
            (1, 'Ristoranti e bar', NULL, 'uscite'),
            (2, 'Ristoranti', 1, 'uscite'),
            (3, 'Viaggi e svago', NULL, 'uscite'),
            (4, 'Ristoranti', 3, 'uscite');
        INSERT INTO overrides VALUES ('t1', 2, NULL, '', 'x');
        """
    )

    assert db.migrate(conn, NOW) == [4]
    names = conn.execute("SELECT id, name FROM categories ORDER BY id")
    assert [tuple(row) for row in names] == [
        (1, "Ristoranti e bar"),
        (2, "Ristoranti e bar"),  # same row: what pointed to it follows
        (3, "Viaggi e svago"),
        (4, "Ristoranti"),  # only the one under its own group
    ]
    conn.close()


TWICE = "Caff\u00c3\u00a8 \u00c3\u0096lberg"  # Caffè Ölberg, as Revolut's export spells it
LOOKS_IT = "Konsum \u00e0\u0080\u0080"  # but is no UTF-8 read as Latin-1


def _revolut_rows() -> list[Transaction]:
    fields = {"account": "Revolut EUR", "currency": "EUR", "source_file": "revolut.tsv"}
    return [
        txn("-4.5", description=TWICE, counterparty=TWICE, source_ref="1", **fields),
        txn("-4.5", description=TWICE, counterparty=TWICE, source_ref="2", **fields),  # alike
        txn("-3", description=LOOKS_IT, counterparty="Konsum", source_ref="3", **fields),
    ]


def _staged_before_0005(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    conn = db.connect(":memory:")
    every = db.migrations()
    monkeypatch.setattr(db, "migrations", lambda: every[:4])
    assert db.migrate(conn, NOW) == [1, 2, 3, 4]
    monkeypatch.undo()
    rows = _revolut_rows()
    revolut = statement(
        "revolut", rows, [CashBalance("Revolut EUR", "EUR", Decimal(20), Decimal(8))]
    )
    staging.stage(conn, batch(revolut, migros("100", "-1", description=TWICE)), CLOSED_AT)
    return conn


def test_migration_0005_reads_revolut_accents_as_the_extractor_now_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _staged_before_0005(monkeypatch)
    before = transaction_ids(OWNER, [("revolut", row) for row in _revolut_rows()])
    conn.execute("INSERT INTO overrides VALUES (?, NULL, 'expense', '', 'x')", (before[1],))

    assert db.migrate(conn, NOW)[0] == 5
    meant = [
        replace(row, description="Caffè Ölberg", counterparty="Caffè Ölberg")
        if row.description == TWICE
        else row
        for row in _revolut_rows()
    ]
    after = transaction_ids(OWNER, [("revolut", row) for row in meant])
    assert after[2] == before[2]  # nothing to repair
    stored = conn.execute(
        "SELECT id, description, counterparty FROM transactions WHERE account_id = "
        "(SELECT id FROM accounts WHERE name = 'Revolut EUR') ORDER BY source_ref"
    )
    assert [tuple(row) for row in stored] == [
        (after[0], "Caffè Ölberg", "Caffè Ölberg"),
        (after[1], "Caffè Ölberg", "Caffè Ölberg"),
        (after[2], LOOKS_IT, "Konsum"),
    ]
    moved = conn.execute("SELECT transaction_id FROM overrides").fetchall()
    assert [row[0] for row in moved] == [after[1]]
    other = conn.execute("SELECT description FROM transactions WHERE description = ?", (TWICE,))
    assert len(other.fetchall()) == 1  # Migros's own text is not Revolut's to repair
    conn.close()


def test_migration_0005_stops_at_a_row_whose_id_does_not_follow_from_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _staged_before_0005(monkeypatch)
    first = transaction_ids(OWNER, [("revolut", _revolut_rows()[0])])[0]
    conn.execute("UPDATE transactions SET id = 'typed' WHERE id = ?", (first,))
    was = dump(conn)
    with pytest.raises(db.MigrationError, match="transaction typed: its id does not follow"):
        db.migrate(conn, NOW)
    assert dump(conn) == was
    assert db.version(conn) == 4
    conn.close()
