"""`scrooge doctor`: what is wrong with a setup, said before it gets in the way.

It reads the database as it is, through a connection that cannot change it, and never brings
its schema up to date: whether a release would migrate it is one of the things it says. It
looks at the schema version, at the database file itself, at the rules and the files read
with them, at the originals kept beside the database and at how far the prices go. It
fetches nothing and writes nothing. Every problem is listed, not only the first.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from scrooge import categories, classification, importing, marketdata
from scrooge.errors import ConfigError, not_yaml
from scrooge.storage import db, originals, store


@dataclass(frozen=True, slots=True)
class Check:
    """One thing looked at: what was found, and what is wrong with it."""

    name: str
    said: str  # what was found, in a few words
    problems: tuple[str, ...] = ()
    advice: str = ""  # what to do about the problems


def examine(
    database: Path,
    setup: importing.Setup,
    today: date,
    currencies: Collection[str] = (),
) -> tuple[Check, ...]:
    """Every check, in the order they are shown. `setup` is what `import` reads besides the
    statements; `currencies` those `prices` fetches rates of."""
    rules = _rules(setup)
    try:
        with db.reading(database) as conn:
            schema = _schema(conn)
            checks = [schema, _integrity(conn), rules]
            if schema.problems:
                skipped = f"not looked at: they read schema version {db.latest()}"
                return (*checks, Check("originals", skipped), Check("prices", skipped))
            return (*checks, _originals(conn, database), _prices(conn, today, currencies))
    except sqlite3.DatabaseError as exc:
        return (Check("database", "cannot be read", (f"{database}: {exc}",)), rules)


def _schema(conn: sqlite3.Connection) -> Check:
    found, latest = db.version(conn), db.latest()
    if found == latest:
        return Check("schema", f"version {found}, the one this code expects")
    if found == 0:
        return Check("schema", "none", ("this is no scrooge database: it has no schema version",))
    if found > latest:
        problem = f"version {found}: this code knows up to {latest}"
        return Check("schema", "newer than this code", (problem,), "Update scrooge.")
    problem = f"version {found}: this code brings it to {latest}"
    advice = (
        "The next command that opens it to write migrates it, after a copy in backups/; "
        "take a copy of your own first if it matters."
    )
    return Check("schema", "older than this code", (problem,), advice)


def _integrity(conn: sqlite3.Connection) -> Check:
    """SQLite's own check of the file, and every reference between tables."""
    damage = [str(row[0]) for row in conn.execute("PRAGMA quick_check")]
    found = [line for line in damage if line != "ok"]
    found += [
        f"a row of {row[0]} refers to a missing row of {row[2]}"
        for row in conn.execute("PRAGMA foreign_key_check")
    ]
    if not found:
        return Check("integrity", "no damage found")
    advice = "Restore the newest copy in backups/ that has none."
    return Check("integrity", "the database is damaged", tuple(found), advice)


def _rules(setup: importing.Setup) -> Check:
    """The files `import` reads besides the statements, each of them in full, for each person:
    a problem in any would stop an import."""
    problems = [f"no rules file at {path.absolute()}" for path in setup.rules if not path.is_file()]
    try:
        extras = categories.load(setup.categories_file)
    except (ConfigError, yaml.YAMLError) as exc:
        problems.append(_said(exc, setup.categories_file))
        extras = ()
    known = categories.names(extras)
    files = [*(setup.own(owner) for owner in setup.people), *setup.rules]
    problems += classification.problems(files, known)
    if setup.unmatched and setup.unmatched not in known:
        problems.append(f"unmatched: {setup.unmatched!r} is no category")
    try:
        classification.load_instruments(setup.instruments)
    except (ConfigError, yaml.YAMLError) as exc:
        problems.append(_said(exc, setup.instruments))
    if problems:
        return Check("rules", "an import would be refused", tuple(problems))
    present = [path for path in dict.fromkeys(files) if path.is_file()]
    count = len(classification.load_rules(present, known))
    added = f", {_many(len(extras), 'category')} added" if extras else ""
    return Check("rules", f"{_many(count, 'rule')} in {_many(len(present), 'file')}{added}")


def _originals(conn: sqlite3.Connection, database: Path) -> Check:
    """The file of every statement stored, kept under its digest beside the database."""
    folder = originals.folder_of(database)
    known = store.statement_names(conn)
    missing = sorted(
        name for digest, name in known.items() if originals.find(folder, digest, name) is None
    )
    if not missing:
        return Check("originals", f"the file of each of {_many(len(known), 'statement')} is kept")
    advice = (
        f"'scrooge keep <folder>' copies them into {folder} from a folder that holds the files."
    )
    said = f"{len(missing)} of {_many(len(known), 'statement')} without their file"
    return Check("originals", said, tuple(missing), advice)


def _prices(conn: sqlite3.Connection, today: date, currencies: Collection[str]) -> Check:
    stale = marketdata.stale(conn, today, currencies)
    if not stale:
        return Check("prices", "a close or a rate of the last week for all that is held")
    days = marketdata.STALE.days
    advice = (
        "'scrooge prices' fetches what is missing; one it still cannot find needs a "
        "price_symbol in instruments.yaml."
    )
    said = f"out of date: no close or rate in the {days} days before yesterday"
    return Check("prices", said, tuple(stale), advice)


def _said(exc: Exception, path: Path) -> str:
    return str(exc) if isinstance(exc, ConfigError) else not_yaml(path, exc)


def _many(count: int, thing: str) -> str:
    if count == 1:
        return f"1 {thing}"
    plural = f"{thing[:-1]}ies" if thing.endswith("y") else f"{thing}s"
    return f"{count} {plural}"


def problems(checks: Iterable[Check]) -> int:
    """How many problems were found altogether."""
    return sum(len(check.problems) for check in checks)
