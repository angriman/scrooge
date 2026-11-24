import sqlite3
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from scrooge import categories, staging
from scrooge.models import CashBalance, Transaction
from scrooge.storage import db, pairs, store
from scrooge.storage.ids import transaction_ids
from tests.storage.conftest import NOW
from tests.storage.helpers import CLOSED_AT, OWNER, batch, dump, migros, statement, txn

_EVERY = db.migrations  # every migration, whatever a test puts in its place


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def _migrate_up_to(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, last: int
) -> list[int]:
    """Migrate as the code whose newest migration is `last` did: no later one ever runs.

    A migration's test stays as it was written when migrations are added after it.
    """
    monkeypatch.setattr(db, "migrations", lambda: [m for m in _EVERY() if m[0] <= last])
    return db.migrate(conn, NOW)


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


_PARENT_AND_CHILD = """
CREATE TABLE parent (id INTEGER PRIMARY KEY, side TEXT NOT NULL CHECK (side IN ('a'))) STRICT;
CREATE TABLE child (parent_id INTEGER REFERENCES parent (id)) STRICT;
INSERT INTO parent VALUES (1, 'a');
INSERT INTO child VALUES (1);
"""


def _rebuild_parent(conn: sqlite3.Connection, *, keep: bool = True) -> None:
    """Build `parent` again with another CHECK, as a table others refer to is changed."""
    conn.execute("CREATE TABLE new (id INTEGER PRIMARY KEY, side TEXT NOT NULL) STRICT")
    if keep:
        conn.execute("INSERT INTO new SELECT id, 'b' FROM parent")
    conn.execute("DROP TABLE parent")
    conn.execute("ALTER TABLE new RENAME TO parent")


def test_a_rebuild_runs_with_foreign_keys_off_and_turns_them_back_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps: list[tuple[int, db.Migration]] = [
        (1, _PARENT_AND_CHILD),
        (2, db.Rebuild(_rebuild_parent)),
    ]
    monkeypatch.setattr(db, "migrations", lambda: steps)
    conn = db.connect(":memory:")
    assert db.migrate(conn, NOW) == [1, 2]
    assert conn.execute("SELECT side FROM parent").fetchone()[0] == "b"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute("INSERT INTO child VALUES (9)")


def test_a_rebuild_that_breaks_a_reference_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def losing(conn: sqlite3.Connection) -> None:
        _rebuild_parent(conn, keep=False)

    steps: list[tuple[int, db.Migration]] = [(1, _PARENT_AND_CHILD), (2, db.Rebuild(losing))]
    monkeypatch.setattr(db, "migrations", lambda: steps)
    conn = sqlite3.connect(":memory:", isolation_level=None)  # foreign keys left off
    with pytest.raises(db.MigrationError, match="rows of child without their parent"):
        db.migrate(conn, NOW)
    assert conn.execute("SELECT side FROM parent").fetchone()[0] == "a"
    assert db.version(conn) == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0  # as it was


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
    assert db.latest() == db.migrations()[-1][0]  # numbers may skip: other branches take some
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
    db.snapshot(live, folder / "scrooge.db")
    live.close()
    assert [file.name for file in folder.iterdir()] == ["scrooge.db"]
    with db.reading(folder / "scrooge.db") as copy:
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert copy.execute("SELECT name FROM owners").fetchone()[0] == "A"
        assert db.version(copy) == db.latest()
    assert [file.name for file in folder.iterdir()] == ["scrooge.db"]


def test_a_reading_connection_cannot_change_the_database(tmp_path: Path) -> None:
    path = tmp_path / "with space" / "scrooge.db"
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
    assert _migrate_up_to(conn, monkeypatch, 2) == [1, 2]

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

    assert _migrate_up_to(conn, monkeypatch, 3) == [3]
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
    assert _migrate_up_to(conn, monkeypatch, 3) == [1, 2, 3]

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

    assert _migrate_up_to(conn, monkeypatch, 4) == [4]
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
    assert _migrate_up_to(conn, monkeypatch, 4) == [1, 2, 3, 4]
    monkeypatch.undo()
    # pairing is of the schema of today (migration 0007) and nothing here is paired
    monkeypatch.setattr(pairs, "mark", lambda conn: 0)
    # the categories are found by their key since 0011: it is there while today's code stages
    conn.execute("ALTER TABLE categories ADD COLUMN key TEXT NOT NULL DEFAULT ''")
    rows = _revolut_rows()
    revolut = statement(
        "revolut", rows, [CashBalance("Revolut EUR", "EUR", Decimal(20), Decimal(8))]
    )
    staging.stage(conn, batch(revolut, migros("100", "-1", description=TWICE)), CLOSED_AT)
    conn.execute("ALTER TABLE categories DROP COLUMN key")
    monkeypatch.undo()
    return conn


def test_migration_0005_reads_revolut_accents_as_the_extractor_now_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _staged_before_0005(monkeypatch)
    before = transaction_ids(OWNER, [("revolut", row) for row in _revolut_rows()])
    conn.execute("INSERT INTO overrides VALUES (?, NULL, 'expense', '', 'x')", (before[1],))

    assert _migrate_up_to(conn, monkeypatch, 5) == [5]
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
        _migrate_up_to(conn, monkeypatch, 5)
    assert dump(conn) == was
    assert db.version(conn) == 4
    conn.close()


def test_migration_0007_marks_nothing_as_made_by_pairing(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = db.connect(":memory:")
    assert _migrate_up_to(conn, monkeypatch, 6) == [1, 2, 3, 4, 5, 6]
    conn.executescript(
        """
        INSERT INTO owners (id, name) VALUES (1, 'Lukas'), (2, 'Anna');
        INSERT INTO currencies (code, decimals) VALUES ('EUR', 2);
        INSERT INTO accounts (id, owner_id, name, kind, currency, source)
            VALUES (1, 1, 'Revolut EUR', 'bank', 'EUR', 'revolut');
        INSERT INTO transactions (id, account_id, date, booking_date, amount_nano, currency,
                                  description, kind, counterparty_owner_id)
            VALUES ('t1', 1, '2026-09-07', '2026-09-07', -1000000000, 'EUR', 'x', 'expense', 2);
        """
    )
    assert _migrate_up_to(conn, monkeypatch, 7) == [7]
    row = conn.execute("SELECT counterparty_owner_id, counterparty_paired FROM transactions")
    assert tuple(row.fetchone()) == (2, 0)  # a mark made before is kept as the statement's own
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE transactions SET counterparty_paired = 2")
    conn.close()


def test_migration_0008_takes_stored_closes_as_asked_for_as_the_old_rule_did(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = db.connect(":memory:")
    assert _migrate_up_to(conn, monkeypatch, 7) == [1, 2, 3, 4, 5, 6, 7]
    monkeypatch.undo()
    conn.executescript(
        """
        INSERT INTO currencies VALUES ('CHF', 2);
        INSERT INTO currencies VALUES ('USD', 2);
        INSERT INTO instruments (id, symbol, type, currency) VALUES (1, 'WIDG', 'etf', 'USD');
        INSERT INTO prices VALUES (1, '2026-09-30', '55', 'fetched:fake', 'x');
        INSERT INTO prices VALUES (1, '2026-09-28', '54', 'fetched:fake', 'x');
        INSERT INTO prices VALUES (1, '2026-09-01', '50', 'manual', NULL);
        INSERT INTO fx_rates VALUES ('2026-09-29', 'USD', '0.8', 'fetched:fake', 'x');
        INSERT INTO fx_rates VALUES ('2026-09-05', 'USD', '0.8', 'manual', NULL);
        """
    )
    assert _migrate_up_to(conn, monkeypatch, 8) == [8]
    stored = conn.execute("SELECT series, source, first_day FROM asked_from ORDER BY 1")
    assert [tuple(row) for row in stored] == [
        ("close:1", "fetched:fake", "2026-09-08"),  # 20 days before the first close
        ("rate:USD", "fetched:fake", "2026-09-09"),
    ]
    conn.close()


def test_migration_0009_moves_printed_rates_to_their_statement_and_drops_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = db.connect(":memory:")
    assert _migrate_up_to(conn, monkeypatch, 8) == [1, 2, 3, 4, 5, 6, 7, 8]
    conn.execute("INSERT INTO owners (id, name) VALUES (1, 'A')")
    conn.execute("INSERT OR IGNORE INTO currencies (code, decimals) VALUES ('USD', 2)")
    for source, name, end in (("ibkr", "i", "2026-09-30"), ("migros", "m", "2026-09-30")):
        conn.execute(
            "INSERT INTO statement_files (owner_id, source, filename, sha256, period_start, "
            "period_end, reconciled, imported_at) VALUES (1, ?, ?, ?, '2026-09-01', ?, 'yes', 'x')",
            (source, name, name, end),
        )
    conn.execute("INSERT INTO instruments (symbol, type, currency) VALUES ('WIDG', 'stock', 'USD')")
    for day, source, rate in (
        ("2026-09-30", "statement", "0.8"),
        ("2026-10-31", "statement", "0.9"),  # of a statement discarded: nothing has it
        ("2026-09-30", "fetched:x", "0.7"),
    ):
        conn.execute("INSERT INTO fx_rates VALUES (?, 'USD', ?, ?, NULL)", (day, rate, source))
    for source in ("statement", "manual"):
        conn.execute("INSERT INTO prices VALUES (1, '2026-09-30', '50', ?, NULL)", (source,))

    assert _migrate_up_to(conn, monkeypatch, 9) == [9]
    kept = conn.execute(
        "SELECT f.source, r.currency, r.date, r.rate_to_chf FROM statement_rates r "
        "JOIN statement_files f ON f.id = r.statement_file_id"
    ).fetchall()
    assert [tuple(row) for row in kept] == [("ibkr", "USD", "2026-09-30", "0.8")]
    left = conn.execute("SELECT source FROM fx_rates").fetchall()
    assert [row[0] for row in left] == ["fetched:x"]
    assert [row[0] for row in conn.execute("SELECT source FROM prices")] == ["manual"]
    conn.execute("DELETE FROM statement_files WHERE source = 'ibkr'")
    assert conn.execute("SELECT count(*) FROM statement_rates").fetchone()[0] == 0
    conn.close()


def test_migration_0010_gives_the_taxonomy_its_english_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = db.connect(":memory:")
    assert _migrate_up_to(conn, monkeypatch, 9) == list(range(1, 10))
    monkeypatch.undo()
    italian, english = categories.LABELS_IT, categories.LABELS_EN
    insert = "INSERT INTO categories (name, parent_id, side) VALUES (?, ?, ?)"
    expected: list[tuple[str, str, str | None]] = []

    def add(key: str, side: str, parent: int | None = None, group: str | None = None) -> int:
        expected.append((side, english[key], group and english[group]))
        row = conn.execute(insert, (italian[key], parent, side)).lastrowid
        assert row is not None
        return row

    groups = {group: add(group, "uscite") for group in categories.EXPENSE_GROUPS}
    for group, leaves in categories.EXPENSE_GROUPS.items():
        for leaf in leaves:
            add(leaf, "uscite", groups[group], group)
    for side, names in (("entrate", categories.INCOME_LEAVES), ("transfer", categories.TRANSFERS)):
        for name in names:
            add(name, side)
    # added by hand: under a group of the taxonomy, and a name of the taxonomy under one's own
    conn.execute(insert, ("Spedizioni", groups[categories.GROUP_TRANSPORT], "uscite"))
    own = conn.execute(insert, ("Prestiti", None, "uscite")).lastrowid
    conn.execute(insert, ("Affitto", own, "uscite"))
    expected += [
        ("uscite", "Spedizioni", english[categories.GROUP_TRANSPORT]),
        ("uscite", "Prestiti", None),
        ("uscite", "Affitto", "Prestiti"),  # not the taxonomy's Affitto: left alone
    ]

    assert _migrate_up_to(conn, monkeypatch, 10) == [10]
    tree = conn.execute(
        "SELECT c.side, c.name, p.name FROM categories c "
        "LEFT JOIN categories p ON p.id = c.parent_id ORDER BY c.id"
    )
    assert [tuple(row) for row in tree] == expected
    conn.close()


def _italian_taxonomy(conn: sqlite3.Connection) -> dict[str, int]:
    """The taxonomy as a database had it before 0010, by the Italian names: id of each key,
    a top-level category of the expenses under `group:` and its key."""
    insert = "INSERT INTO categories (name, parent_id, side) VALUES (?, ?, ?)"
    ids: dict[str, int] = {}
    for group, leaves in categories.EXPENSE_GROUPS.items():
        top = conn.execute(insert, (categories.LABELS_IT[group], None, "uscite")).lastrowid
        assert top is not None
        ids[f"group:{group}"] = top
        for leaf in leaves:
            row = conn.execute(insert, (categories.LABELS_IT[leaf], top, "uscite")).lastrowid
            assert row is not None
            ids[f"uscite:{leaf}"] = row
    for side, names in (("entrate", categories.INCOME_LEAVES), ("transfer", categories.TRANSFERS)):
        for name in names:
            row = conn.execute(insert, (categories.LABELS_IT[name], None, side)).lastrowid
            assert row is not None
            ids[f"{side}:{name}"] = row
    return ids


def test_migration_0011_gives_each_category_a_key_and_the_rows_their_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = db.connect(":memory:")
    # as the database of the NAS: at schema 6, then every migration since in one go
    assert _migrate_up_to(conn, monkeypatch, 6) == list(range(1, 7))
    ids = _italian_taxonomy(conn)
    insert = "INSERT INTO categories (name, parent_id, side) VALUES (?, ?, ?)"

    def added(name: str, side: str = "uscite", parent: int | None = None) -> int:
        row = conn.execute(insert, (name, parent, side)).lastrowid
        assert row is not None
        return row

    transport = ids[f"group:{categories.GROUP_TRANSPORT}"]
    own = added("Prestiti")
    mine = {
        "Spedizioni": added("Spedizioni", parent=transport),  # added in categories.yaml
        "Corriere espresso": added("Corriere espresso", parent=transport),  # renamed on a page
        "Prestiti": added("Prestiti", parent=own),  # a leaf of its group's name
        "Spesa": added("Spesa", parent=own),  # a name of the taxonomy under a group of one's own
        "Groceries": added("Groceries", parent=own),  # in English, the same
        "Car-wash": added("Car-wash", parent=transport),
        "Car wash": added("Car wash", parent=transport),  # another name, the same key
        "Attività extra": added("Attività extra", parent=own),
        "?!": added("?!", parent=own),  # no letter at all
        "Affitti": added("Affitti", "entrate"),
    }
    conn.executescript(
        """
        INSERT INTO owners (id, name) VALUES (1, 'Lukas');
        INSERT INTO currencies (code, decimals) VALUES ('CHF', 2);
        INSERT INTO accounts (id, owner_id, name, kind, currency, source)
            VALUES (1, 1, 'Migros', 'bank', 'CHF', 'migros');
        """
    )
    carried = (ids[f"uscite:{categories.GROCERIES}"], mine["Spedizioni"])
    for n, category in enumerate(carried):
        conn.execute(
            "INSERT INTO transactions (id, account_id, date, booking_date, amount_nano, currency, "
            "description, kind, category_id, category_source) VALUES (?, 1, '2026-09-01', "
            "'2026-09-01', -1, 'CHF', 'x', 'expense', ?, 'rule')",
            (f"t{n}", category),
        )

    assert _migrate_up_to(conn, monkeypatch, 11) == list(range(7, 12))
    keys = {row["id"]: row["key"] for row in conn.execute("SELECT id, key FROM categories")}
    for name, number in ids.items():
        assert keys[number] == name.partition(":")[2]  # every built-in one found where it is
    assert {name: keys[number] for name, number in mine.items()} == {
        "Spedizioni": "spedizioni",
        "Corriere espresso": "corriere_espresso",
        "Prestiti": "prestiti",
        "Spesa": "spesa",
        "Groceries": "groceries_2",  # never mistaken for the taxonomy's
        "Car-wash": "car_wash",
        "Car wash": "car_wash_2",
        "Attività extra": "attivita_extra",
        "?!": f"category_{mine['?!']}",
        "Affitti": "affitti",
    }
    assert keys[own] == "prestiti"
    names = conn.execute("SELECT name FROM categories WHERE id = ?", (mine["Car wash"],))
    assert names.fetchone()[0] == "Car wash"  # an added category keeps its name: its label
    shown = conn.execute("SELECT id, category, category_parent FROM committed_transactions")
    assert [tuple(row) for row in shown] == [
        ("t0", categories.GROCERIES, categories.GROUP_FOOD),
        ("t1", "spedizioni", categories.GROUP_TRANSPORT),
    ]
    with pytest.raises(sqlite3.IntegrityError):  # a key is unique where the name was
        conn.execute(
            "INSERT INTO categories (key, name, parent_id, side) VALUES ('spedizioni', 'Ship', ?, "
            "'uscite')",
            (transport,),
        )
    monkeypatch.undo()
    db.migrate(conn, NOW)
    count = "SELECT count(*) FROM categories"
    before = conn.execute(count).fetchone()[0]
    store.seed_categories(conn)  # the taxonomy of today finds every key it has
    assert conn.execute(count).fetchone()[0] == before
    conn.close()
