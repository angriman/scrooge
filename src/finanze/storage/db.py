"""Open the database and bring its schema up to date.

Migrations are the numbered `.sql` files in `finanze.storage.migrations`, applied in order,
each in one transaction together with its row in `schema_version`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from importlib import resources
from pathlib import Path

_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL PRIMARY KEY,
    applied_at TEXT    NOT NULL
) STRICT
"""


class MigrationError(Exception):
    """The database is newer than this code, or a migration file is misnamed."""


def connect(path: Path | str) -> sqlite3.Connection:
    """Open (or create) the database. Transactions are explicit: see `transaction`."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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


def migrations() -> list[tuple[int, str]]:
    """(version, SQL) of every migration file, in order."""
    found: list[tuple[int, str]] = []
    for entry in resources.files("finanze.storage.migrations").iterdir():
        if not entry.name.endswith(".sql"):
            continue
        number = entry.name.partition("_")[0]
        if not number.isdigit():
            msg = f"migration {entry.name!r} must be named NNNN_description.sql"
            raise MigrationError(msg)
        found.append((int(number), entry.read_text(encoding="utf-8")))
    return sorted(found)


def migrate(conn: sqlite3.Connection, now: datetime) -> list[int]:
    """Apply the migrations the database has not seen yet; returns their versions."""
    conn.execute(_VERSION_TABLE)
    current = conn.execute("SELECT coalesce(max(version), 0) FROM schema_version").fetchone()[0]
    pending = [(version, sql) for version, sql in migrations() if version > current]
    known = max((version for version, _ in migrations()), default=0)
    if current > known:
        msg = f"the database is at schema version {current}, this code knows up to {known}"
        raise MigrationError(msg)
    for version, sql in pending:
        try:
            conn.executescript(f"BEGIN IMMEDIATE;\n{sql}")
            conn.execute("INSERT INTO schema_version VALUES (?, ?)", (version, timestamp(now)))
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    return [version for version, _ in pending]


def backup(conn: sqlite3.Connection, target: Path) -> None:
    """Save a consistent copy of the whole database as `target`."""
    target.parent.mkdir(parents=True, exist_ok=True)
    copy = sqlite3.connect(target)
    try:
        conn.backup(copy)
    finally:
        copy.close()
