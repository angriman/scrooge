"""Open the database and bring its schema up to date.

Migrations are the numbered files in `scrooge.storage.migrations`, applied in order, each in
one transaction together with its row in `schema_version`: a `.sql` file is run as it is, a
`.py` file is one whose `migrate(conn)` changes what SQL alone cannot (it computes in Python).

A `.py` file that says `REBUILDS = True` builds a table again that other tables refer to (a
CHECK cannot be changed in place). SQLite asks for foreign keys to be off for that, and they
cannot be turned off inside a transaction: such a migration runs with them off, and is
refused if any reference is broken when it is done.
"""

from __future__ import annotations

import importlib
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path

_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL PRIMARY KEY,
    applied_at TEXT    NOT NULL
) STRICT
"""


@dataclass(frozen=True)
class Rebuild:
    """A `.py` migration that builds a table again: run with foreign keys off."""

    migrate: Callable[[sqlite3.Connection], None]


# The SQL of a `.sql` migration, or the `migrate` function of a `.py` one.
Migration = str | Callable[[sqlite3.Connection], None] | Rebuild


class MigrationError(Exception):
    """The database is newer than this code, or a migration file is misnamed."""


def connect(path: Path | str) -> sqlite3.Connection:
    """Open (or create) the database. Transactions are explicit: see `transaction`."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def reading(path: Path) -> Iterator[sqlite3.Connection]:
    """A connection that cannot change the database, closed on the way out."""
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Everything inside is saved together, or not at all if anything raises."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def timestamp(moment: datetime) -> str:
    """A timezone-aware moment as the ISO 8601 text the database stores."""
    if moment.tzinfo is None:
        msg = "timestamps must be timezone-aware"
        raise ValueError(msg)
    return moment.isoformat(timespec="seconds")


def migrations() -> list[tuple[int, Migration]]:
    """(version, migration) of every migration file, in order."""
    package = "scrooge.storage.migrations"
    found: list[tuple[int, Migration]] = []
    for entry in resources.files(package).iterdir():
        stem, dot, suffix = entry.name.rpartition(".")
        if not dot or suffix not in {"sql", "py"} or stem.startswith("_"):
            continue
        number = stem.partition("_")[0]
        if not number.isdigit():
            msg = f"migration {entry.name!r} must be named NNNN_description.{suffix}"
            raise MigrationError(msg)
        if suffix == "sql":
            found.append((int(number), entry.read_text(encoding="utf-8")))
        else:
            module = importlib.import_module(f"{package}.{stem}")
            rebuilds = getattr(module, "REBUILDS", False)
            found.append((int(number), Rebuild(module.migrate) if rebuilds else module.migrate))
    return sorted(found, key=lambda migration: migration[0])


def latest() -> int:
    """The schema version this code brings a database to, and expects of one it only reads."""
    return max((version for version, _ in migrations()), default=0)


def version(conn: sqlite3.Connection) -> int:
    """The schema version the database is at, 0 before any migration; nothing is changed."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_version'").fetchone():
        return 0
    return int(conn.execute("SELECT coalesce(max(version), 0) FROM schema_version").fetchone()[0])


def migrate(conn: sqlite3.Connection, now: datetime) -> list[int]:
    """Apply the migrations the database has not seen yet; returns their versions."""
    conn.execute(_VERSION_TABLE)
    current = version(conn)
    pending = [(number, step) for number, step in migrations() if number > current]
    known = latest()
    if current > known:
        msg = f"the database is at schema version {current}, this code knows up to {known}"
        raise MigrationError(msg)
    for number, step in pending:
        if isinstance(step, Rebuild):
            _rebuild(conn, number, step, now)
            continue
        try:
            if isinstance(step, str):
                conn.executescript(f"BEGIN IMMEDIATE;\n{step}")
            else:
                conn.execute("BEGIN IMMEDIATE")
                step(conn)
            conn.execute("INSERT INTO schema_version VALUES (?, ?)", (number, timestamp(now)))
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    return [number for number, _ in pending]


def _rebuild(conn: sqlite3.Connection, number: int, step: Rebuild, now: datetime) -> None:
    """Run `step` as SQLite says a table is built again: foreign keys off, outside the
    transaction, and every reference checked before it commits; then as they were."""
    enforced = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        step.migrate(conn)
        broken = conn.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            table, _, parent, _ = broken[0]
            msg = f"migration {number} leaves rows of {table} without their {parent}"
            raise MigrationError(msg)
        conn.execute("INSERT INTO schema_version VALUES (?, ?)", (number, timestamp(now)))
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    finally:
        conn.execute("PRAGMA foreign_keys = ON" if enforced else "PRAGMA foreign_keys = OFF")


def backup(conn: sqlite3.Connection, target: Path) -> None:
    """Save a consistent copy of the whole database as `target`."""
    target.parent.mkdir(parents=True, exist_ok=True)
    copy = sqlite3.connect(target)
    try:
        conn.backup(copy)
    finally:
        copy.close()


def snapshot(conn: sqlite3.Connection, target: Path) -> None:
    """Save a consistent copy as `target` that is one file wherever it is read from.

    A copy takes the journal mode of its source, and a database in WAL mode cannot be read
    from a folder nothing can be written in: the copy keeps its journal in no file of its own.
    """
    copy = sqlite3.connect(target)
    try:
        conn.backup(copy)
        copy.execute("PRAGMA journal_mode = DELETE")
    finally:
        copy.close()
