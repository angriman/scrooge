"""`scrooge migrate --dry-run`: what this code would do to a database, tried on a copy.

The database is read through a connection that cannot change it, copied whole into a folder
of its own outside the data (SQLite's backup API: a consistent copy even while the pages
write), and the copy is brought up to date one migration at a time, as the next command that
opens the database would. Each migration says whether it went through; then the copy is
checked as `doctor` checks a database (SQLite's `quick_check` and every reference between
tables), and removed. Nothing is written beside the database.
"""

from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from scrooge import doctor
from scrooge.storage import db


@dataclass(frozen=True, slots=True)
class Step:
    """One migration tried on the copy."""

    version: int
    name: str
    failed: str | None = None  # why, when it did not go through
    tried: bool = True  # False: one before it failed


@dataclass(frozen=True, slots=True)
class DryRun:
    found: int  # the schema version of the database
    latest: int  # the one this code brings it to
    steps: tuple[Step, ...]
    integrity: doctor.Check  # of the copy, once migrated as far as it went

    @property
    def ok(self) -> bool:
        """Every migration went through and the copy has no damage."""
        failed = any(step.failed is not None or not step.tried for step in self.steps)
        return not failed and not self.integrity.problems


def run(database: Path, now: datetime) -> DryRun:
    """Try the migrations this code has and the database has not on a copy of it.

    A database newer than this code, or a file that is no database, is refused
    (`db.MigrationError`) before anything is tried.
    """
    with tempfile.TemporaryDirectory(prefix="scrooge-dry-run-") as folder:
        path = Path(folder) / database.name
        try:
            with db.reading(database) as conn:
                db.snapshot(conn, path)
        except sqlite3.DatabaseError as exc:
            msg = f"{database}: cannot be read ({exc})"
            raise db.MigrationError(msg) from None
        copy = db.connect(path)
        try:
            found = db.version(copy)
            steps = _steps(copy, now)
            return DryRun(found, db.latest(), steps, doctor.integrity(copy))
        finally:
            copy.close()


def _steps(copy: sqlite3.Connection, now: datetime) -> tuple[Step, ...]:
    names = db.names()
    steps: list[Step] = []
    failed = False
    for number, _ in db.pending(copy):
        name = names.get(number, f"{number:04d}")
        if failed:
            steps.append(Step(number, name, tried=False))
            continue
        try:
            db.migrate(copy, now, through=number)
        except Exception as exc:  # noqa: BLE001  # whatever stops a migration is what dry-run reports
            steps.append(Step(number, name, str(exc) or type(exc).__name__))
            failed = True
        else:
            steps.append(Step(number, name))
    return tuple(steps)
