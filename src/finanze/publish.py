"""Copy the database to a folder another machine shows it from (`finanze publish`).

The folder is a share of that machine, mounted on this one. SQLite is never used over it: a
consistent snapshot is made on this disk, copied whole into the folder under another name,
and only then renamed over the copy that was there. Whoever reads there finds the old copy
or the new one, never half of one.

The database is all that goes: no config, statements, backups or rules. It goes whole, with
the months that are staged and not committed; `Published` says how much of it that is.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from finanze.storage import db

NAME = "finanze.db"  # what the copy is called, whatever the database is called here
_PART = f".{NAME}.part"  # while it is being written


class PublishError(Exception):
    """Nothing was published; a copy published before is as it was."""


@dataclass(frozen=True, slots=True)
class Published:
    path: Path
    size: int  # bytes
    version: int  # of the schema: the code that shows the copy must read the same
    committed: int  # transactions of committed months, which the pages show
    staged: int  # transactions of the months still open, which the copy holds too
    open_months: int


def run(conn: sqlite3.Connection, database: Path, folder: Path) -> Published:
    """Publish a snapshot of `conn`, the open `database`, as `finanze.db` in `folder`.

    The folder is never created: a share that is not mounted must not become a folder of
    this machine under the name of the share.
    """
    if not folder.is_dir():
        msg = f"{folder}: no such folder: nothing is published to a share that is not mounted"
        raise PublishError(msg)
    if folder.resolve() == database.parent.resolve():
        msg = f"{folder}: the database itself is kept there: publish its copy somewhere else"
        raise PublishError(msg)
    with tempfile.TemporaryDirectory(dir=database.parent, prefix=".publish-") as local:
        snapshot = Path(local) / NAME
        db.snapshot(conn, snapshot)
        with db.reading(snapshot) as copy:
            version = db.version(copy)
            rows = copy.execute("SELECT count(*) FROM transactions").fetchone()[0]
            committed = copy.execute("SELECT count(*) FROM committed_transactions").fetchone()[0]
            waiting = copy.execute("SELECT count(*) FROM months WHERE status = 'open'").fetchone()
        size = snapshot.stat().st_size
        path = _deliver(snapshot, folder)
    return Published(path, size, version, committed, rows - committed, waiting[0])


def _deliver(snapshot: Path, folder: Path) -> Path:
    """Copy the finished snapshot into the folder, and give it its name once it is whole."""
    part, target = folder / _PART, folder / NAME
    try:
        # The bytes alone: no attributes of this machine's file, which a share keeps in
        # files of their own next to it.
        with snapshot.open("rb") as source, part.open("wb") as copy:
            shutil.copyfileobj(source, copy)
            copy.flush()
            os.fsync(copy.fileno())  # on the other machine's disk before it takes the name
        part.replace(target)
    except OSError as exc:
        with contextlib.suppress(OSError):
            part.unlink(missing_ok=True)
        msg = f"{folder}: nothing was published, and what was there is as it was: {exc}"
        raise PublishError(msg) from None
    return target
