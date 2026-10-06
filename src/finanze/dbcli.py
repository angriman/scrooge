"""`finanze import | commit | reopen`: one person's month in the database.

    finanze import <folder>   reconcile and classify the statements, then stage the month
    finanze commit <YYYY-MM>  make a staged month visible to the dashboards
    finanze reopen <YYYY-MM>  take a committed month back, to import it again

The database path, the person and the names that recognise each person as a counterparty
come from `finanze.yaml` (`database`, `owner`, `owners`), or from `--db` and `--owner`.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TextIO

import yaml

from finanze import classification, monthclose, pipeline
from finanze.classification import ConfigError, UnknownInstrumentError
from finanze.errors import ExtractionError
from finanze.monthclose import Batch, MonthCloseError, People
from finanze.storage import db, store
from finanze.storage.db import MigrationError
from finanze.storage.money import PrecisionError
from finanze.validation import ReconciliationError

COMMANDS = ("import", "commit", "reopen")
OK, FAILED, NEEDS_REVIEW = 0, 1, 2
_ERRORS = (
    ExtractionError,
    ReconciliationError,
    ConfigError,
    UnknownInstrumentError,
    MonthCloseError,
    MigrationError,
    PrecisionError,
    store.UnknownCategoryError,
    store.AccountMismatchError,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def main(argv: Sequence[str], out: TextIO, now: Callable[[], datetime] = _utc_now) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args, out, now())
    except _ERRORS as exc:
        out.write(f"error: {exc}\n")
        return FAILED


def _month(text: str) -> date:
    try:
        return date.fromisoformat(f"{text}-01")
    except ValueError:
        msg = f"{text!r} is not a month like 2026-09"
        raise argparse.ArgumentTypeError(msg) from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finanze", description=__doc__)
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--config", type=Path, default=Path("finanze.yaml"))
    shared.add_argument("--db", type=Path, help="database file (default: 'database' in the config)")
    shared.add_argument("--owner", help="whose month this is (default: 'owner' in the config)")
    commands = parser.add_subparsers(dest="command", required=True)

    load = commands.add_parser("import", parents=[shared], help="stage a month of statements")
    load.add_argument("folder", type=Path, help="folder holding one month of statements")
    load.add_argument(
        "--rules",
        type=Path,
        action="append",
        help="rules file(s), first wins (default: rules.local.yaml, then rules.yaml)",
    )
    load.add_argument("--instruments", type=Path, default=Path("instruments.yaml"))
    for name, text in (("commit", "commit a staged month"), ("reopen", "reopen a committed month")):
        command = commands.add_parser(name, parents=[shared], help=text)
        command.add_argument("month", type=_month, help="the month, as YYYY-MM")
    return parser


def _config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        msg = f"{path}: expected a mapping"
        raise ConfigError(msg)
    return data


def _people(config: dict[str, object], path: Path) -> People:
    """`owners: {Name: {match: [regex, ...]}}` -> the patterns that recognise each person."""
    owners = config.get("owners", {})
    if not isinstance(owners, dict):
        msg = f"{path}: 'owners' must map each person to their 'match' list"
        raise ConfigError(msg)
    people: dict[str, list[re.Pattern[str]]] = {}
    for name, entry in owners.items():
        matches = (entry or {}).get("match", []) if isinstance(entry or {}, dict) else None
        if not isinstance(matches, list):
            msg = f"{path}: owners: {name}: expected 'match' with a list of names or IBANs"
            raise ConfigError(msg)
        try:
            people[str(name)] = [re.compile(str(match), re.IGNORECASE) for match in matches]
        except re.error as exc:
            msg = f"{path}: owners: {name}: {exc}"
            raise ConfigError(msg) from None
    return people


def _setting(args: argparse.Namespace, config: dict[str, object], flag: str, key: str) -> str:
    value = getattr(args, flag) or config.get(key)
    if not value:
        msg = f"no {key}: pass --{flag} or set '{key}' in {args.config}"
        raise ConfigError(msg)
    return str(value)


@dataclass(frozen=True, slots=True)
class _Session:
    """What every command needs once the command line and the config are resolved."""

    conn: sqlite3.Connection
    path: Path
    owner: str
    people: People
    out: TextIO
    now: datetime


def _run(args: argparse.Namespace, out: TextIO, now: datetime) -> int:
    config = _config(args.config)
    owner = _setting(args, config, "owner", "owner")
    path = Path(_setting(args, config, "db", "database")).expanduser()
    people = _people(config, args.config)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(path)
    try:
        db.migrate(conn, now)
        session = _Session(conn, path, owner, people, out, now)
        if args.command == "import":
            return _import(session, args)
        if args.command == "commit":
            return _commit(session, args.month)
        monthclose.reopen(conn, owner=owner, month=args.month)
        out.write(f"{args.month:%B %Y} is open again for {owner}.\n")
        return OK
    finally:
        conn.close()


def _import(session: _Session, args: argparse.Namespace) -> int:
    out = session.out
    statements, skipped = pipeline.load_statements(args.folder)
    rule_files = args.rules or [Path("rules.local.yaml"), Path("rules.yaml")]
    month = pipeline.process(
        [(s.source, s.extraction) for s in statements],
        classification.load_rules(rule_files),
        classification.load_instruments(args.instruments),
        skipped,
    )
    batch = Batch(session.owner, statements, month, session.people)
    staged = monthclose.stage(session.conn, batch, session.now)
    out.write(
        f"{month.month:%B %Y} for {session.owner}: {len(statements)} statements reconciled, "
        f"{staged.transactions} transactions staged.\n"
    )
    out.writelines(f"  skipped {path.name} (not a statement)\n" for path in month.skipped)
    out.writelines(
        f"  note: {account} includes rows with no balance to check against\n"
        for account in month.unreconciled
    )
    out.writelines(f"  missing: {gap}\n" for gap in staged.gaps)
    if staged.uncategorized:
        out.write(
            f"{staged.uncategorized} transactions need a category: add rules "
            f"(finanze {args.folder} --review) and import again.\n"
        )
        return NEEDS_REVIEW
    out.write(f"Next: finanze commit {month.month:%Y-%m}\n")
    return OK


def _commit(session: _Session, month: date) -> int:
    """Commit after saving a copy of the database; the copy is dropped if nothing changed."""
    path, owner, now = session.path, session.owner, session.now
    copy = path.parent / "backups" / f"{path.stem}-{now:%Y%m%dT%H%M%S}.db"
    db.backup(session.conn, copy)
    try:
        changed = monthclose.commit(session.conn, owner=owner, month=month, now=now)
    except BaseException:
        copy.unlink()
        raise
    if not changed:
        copy.unlink()
        session.out.write(f"{month:%B %Y} was already committed for {owner}.\n")
        return OK
    session.out.write(f"{month:%B %Y} committed for {owner}. Copy from before: {copy}\n")
    return OK
