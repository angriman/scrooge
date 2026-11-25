"""`scrooge backup`: a copy of the database in `backups/` beside it, and the older ones pruned.

The copy is made with SQLite's backup API from a connection that cannot change the database
(`db.snapshot`: consistent while the pages write, and one file), under a name of its own:
`<database>-backup-YYYYMMDDTHHMMSSZ.db`, the moment in UTC. It is written under a hidden name
and given that one once it is whole, so a backup cut short is never one of them.

Of the files with exactly such a name, these are kept and the rest removed:

- the newest of each of the 7 most recent days that have one,
- the newest of each of the 8 most recent weeks (ISO weeks) that have one,
- the newest of each of the 12 most recent months that have one.

That is at most 27 files. The days, weeks and months counted are those with a backup, not
those of the calendar, so a schedule that stopped for a while never empties the folder; and
which are kept follows from the names alone. Nothing else in `backups/` is ever removed here: the
copies a commit or a new schema keeps (`staging.prune_copies` prunes those), or a file put
there by hand.

A copy that SQLite's `quick_check` or a reference between tables finds damaged is kept, and
nothing is pruned: a database going wrong never pushes out the copies from before.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scrooge import doctor
from scrooge.staging import BACKUPS
from scrooge.storage import db

DAILY, WEEKLY, MONTHLY = 7, 8, 12
_MOMENT = "%Y%m%dT%H%M%SZ"


class BackupError(Exception):
    """No backup was made."""


@dataclass(frozen=True, slots=True)
class Backup:
    path: Path
    removed: tuple[Path, ...]  # older backups pruned, oldest first
    damage: tuple[str, ...]  # what the check of the copy found; nothing was pruned then


def name(database: Path, moment: datetime) -> str:
    """The name of the backup of `database` made at `moment`."""
    return f"{database.stem}-backup-{moment.astimezone(UTC).strftime(_MOMENT)}.db"


def found(database: Path) -> dict[datetime, Path]:
    """The backups of `database` in its `backups/`, by the moment each was made: only files
    with a name `name` gives."""
    folder = database.parent / BACKUPS
    pattern = re.compile(rf"{re.escape(database.stem)}-backup-(\d{{8}}T\d{{6}}Z)\.db")
    backups: dict[datetime, Path] = {}
    for path in folder.iterdir() if folder.is_dir() else ():
        match = pattern.fullmatch(path.name)
        if not match or not path.is_file():
            continue
        try:
            moment = datetime.fromisoformat(match[1])
        except ValueError:  # digits of no real moment: a file of someone else's
            continue
        backups[moment] = path
    return backups


def keep(moments: Iterable[datetime]) -> set[datetime]:
    """The backups the retention keeps, of those made at `moments`."""
    newest_first = sorted(set(moments), reverse=True)
    periods: tuple[tuple[int, Callable[[datetime], Hashable]], ...] = (
        (DAILY, lambda moment: moment.date()),
        (WEEKLY, lambda moment: moment.isocalendar()[:2]),
        (MONTHLY, lambda moment: (moment.year, moment.month)),
    )
    kept: set[datetime] = set()
    for count, period in periods:
        newest: dict[Hashable, datetime] = {}
        for moment in newest_first:
            newest.setdefault(period(moment), moment)
        kept.update(list(newest.values())[:count])
    return kept


def take(database: Path, now: datetime) -> Backup:
    """Back `database` up into its `backups/` and prune the older backups."""
    folder = database.parent / BACKUPS
    folder.mkdir(exist_ok=True)
    target = folder / name(database, now)
    if target.exists():
        msg = f"{target}: there is a backup of that second already"
        raise BackupError(msg)
    part = folder / f".{target.name}.part"
    try:
        with db.reading(database) as conn:
            db.snapshot(conn, part)
        with db.reading(part) as copy:
            damage = doctor.integrity(copy).problems
    except sqlite3.DatabaseError as exc:
        part.unlink(missing_ok=True)
        msg = f"{database}: cannot be read ({exc})"
        raise BackupError(msg) from None
    part.replace(target)
    if damage:
        return Backup(target, (), damage)
    backups = found(database)
    kept = keep(backups)
    removed = tuple(backups[moment] for moment in sorted(backups) if moment not in kept)
    for path in removed:
        path.unlink()
    return Backup(target, removed, ())
