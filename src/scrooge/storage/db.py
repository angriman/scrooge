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
from importlib.resources.abc import Traversable
from pathlib import Path

_PACKAGE = "scrooge.storage.migrations"
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


def create(path: Path, now: datetime) -> None:
    """Make a new database at `path`, and its folder, with this code's schema.

    The one place a database is made (`scrooge init`): every command opens one that exists.
    A file already at `path` is left as it is: FileExistsError.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x"):  # an empty file is an empty database; one that is there stays
        pass
    conn = connect(path)
    try:
        migrate(conn, now)
    finally:
        conn.close()


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


def _files() -> Iterator[tuple[int, str, Traversable]]:
    """(version, name without suffix, file) of every migration file, in no order."""
    for entry in resources.files(_PACKAGE).iterdir():
        stem, dot, suffix = entry.name.rpartition(".")
        if not dot or suffix not in {"sql", "py"} or stem.startswith("_"):
            continue
        number = stem.partition("_")[0]
        if not number.isdigit():
            msg = f"migration {entry.name!r} must be named NNNN_description.{suffix}"
            raise MigrationError(msg)
        yield int(number), stem, entry


def migrations() -> list[tuple[int, Migration]]:
    """(version, migration) of every migration file, in order."""
    found: list[tuple[int, Migration]] = []
    for number, stem, entry in _files():
        if entry.name.endswith(".sql"):
            found.append((number, entry.read_text(encoding="utf-8")))
        else:
            module = importlib.import_module(f"{_PACKAGE}.{stem}")
            rebuilds = getattr(module, "REBUILDS", False)
            found.append((number, Rebuild(module.migrate) if rebuilds else module.migrate))
    return sorted(found, key=lambda migration: migration[0])


def names() -> dict[int, str]:
    """The name of each migration's file, without its suffix, by its version."""
    return {number: stem for number, stem, _ in _files()}


def latest() -> int:
    """The schema version this code brings a database to, and expects of one it only reads."""
    return max((version for version, _ in migrations()), default=0)


def version(conn: sqlite3.Connection) -> int:
    """The schema version the database is at, 0 before any migration; nothing is changed."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_version'").fetchone():
        return 0
    return int(conn.execute("SELECT coalesce(max(version), 0) FROM schema_version").fetchone()[0])


def pending(conn: sqlite3.Connection) -> list[tuple[int, Migration]]:
    """The migrations the database has not seen yet, in order; nothing is changed. A database
    newer than this code is refused."""
    current, known = version(conn), latest()
    if current > known:
        msg = f"the database is at schema version {current}, this code knows up to {known}"
        raise MigrationError(msg)
    return [(number, step) for number, step in migrations() if number > current]


def migrate(conn: sqlite3.Connection, now: datetime, through: int | None = None) -> list[int]:
    """Apply the migrations the database has not seen yet, those up to version `through` when
    it is given; returns their versions."""
    conn.execute(_VERSION_TABLE)
    applied: list[int] = []
    for number, step in pending(conn):
        if through is not None and number > through:
            break
        try:
            if isinstance(step, Rebuild):
                _rebuild(conn, number, step, now)
            else:
                _apply(conn, number, step, now)
        except Exception:
            # Another process opening the same database (the pages and the prices of one
            # deployment, started together) may have applied it while this one waited for
            # the lock: done then, by the other. The step failed on what was already there,
            # or on its row in `schema_version`, and left nothing.
            if version(conn) < number:
                raise
        else:
            applied.append(number)
    return applied


def _apply(
    conn: sqlite3.Connection,
    number: int,
    step: str | Callable[[sqlite3.Connection], None],
    now: datetime,
) -> None:
    """Run `step` and record it, in one transaction."""
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


def _check_foreign_keys(conn: sqlite3.Connection, number: int) -> None:
    broken = conn.execute("PRAGMA foreign_key_check").fetchall()
    if broken:
        table, _, parent, _ = broken[0]
        msg = f"migration {number} leaves rows of {table} without their {parent}"
        raise MigrationError(msg)


def _rebuild(conn: sqlite3.Connection, number: int, step: Rebuild, now: datetime) -> None:
    """Run `step` as SQLite says a table is built again: foreign keys off, outside the
    transaction, and every reference checked before it commits; then as they were."""
    enforced = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        step.migrate(conn)
        _check_foreign_keys(conn, number)
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
