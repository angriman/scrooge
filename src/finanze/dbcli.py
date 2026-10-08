"""`finanze import | commit | reopen`: one person's month in the database.

    finanze import <path>...  reconcile and classify the statements, then stage their months
    finanze commit <YYYY-MM>  make a staged month visible to the dashboards
    finanze reopen <YYYY-MM>  take a committed month back, to import it again
    finanze balance <account> <YYYY-MM-DD> <amount> <currency>
                              type what an account without statements held on a day

`commit` and `reopen` take a second month to cover every month from the first to it.

The database path, the person and the names that recognise each person as a counterparty
come from `finanze.yaml` (`database`, `owner`, `owners`), or from `--db` and `--owner`.
`account_labels` there names a further account at a broker by its account number.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO

import yaml

from finanze import categories, classification, monthclose, pipeline, review
from finanze.classification import UnknownInstrumentError
from finanze.errors import ConfigError, ExtractionError
from finanze.extractors.common import month_end, months_between
from finanze.monthclose import Batch, MonthCloseError, People
from finanze.storage import db, store
from finanze.storage.db import MigrationError
from finanze.storage.money import PrecisionError
from finanze.validation import ReconciliationError

COMMANDS = ("import", "commit", "reopen", "balance")
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


def main(
    argv: Sequence[str],
    out: TextIO,
    ask: Callable[[str], str] = input,
    now: Callable[[], datetime] = _utc_now,
) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args, _Console(out, ask), now())
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
    load.add_argument(
        "paths",
        type=Path,
        nargs="+",
        metavar="path",
        help="statement files, or folders holding them (subfolders are not searched)",
    )
    load.add_argument(
        "--rules",
        type=Path,
        action="append",
        help="rules file(s), first wins (default: rules.local.yaml, then rules.yaml); the "
        "owner's own rules.<owner>.yaml, next to the first, is read before them",
    )
    load.add_argument("--instruments", type=Path, default=Path("instruments.yaml"))
    load.add_argument(
        "--review",
        action="store_true",
        help="ask for the category of unmatched rows and save the answers as rules; new "
        "categories go in categories.yaml, next to the first rules file",
    )
    for name, text in (("commit", "commit a staged month"), ("reopen", "reopen a committed month")):
        command = commands.add_parser(name, parents=[shared], help=text)
        command.add_argument("month", type=_month, help="the month, as YYYY-MM")
        command.add_argument(
            "through", type=_month, nargs="?", help="the last month, to cover several"
        )
    typed = commands.add_parser("balance", parents=[shared], help="type an account's balance")
    typed.add_argument("account", help="name of an account that has no statements")
    typed.add_argument("day", type=date.fromisoformat, help="the day, as YYYY-MM-DD")
    typed.add_argument("amount", type=_amount, help="what it held at the end of that day")
    typed.add_argument("currency", help="CHF, EUR, ...")
    return parser


def _amount(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation:
        msg = f"{text!r} is not an amount like 1234.56"
        raise argparse.ArgumentTypeError(msg) from None


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


def _labels(config: dict[str, object], path: Path) -> dict[str, str]:
    """`account_labels: {<account number>: <name>}`."""
    labels = config.get("account_labels", {})
    if not isinstance(labels, dict):
        msg = f"{path}: 'account_labels' must map account numbers to names"
        raise ConfigError(msg)
    return {str(number): str(label) for number, label in labels.items()}


def _setting(args: argparse.Namespace, config: dict[str, object], flag: str, key: str) -> str:
    value = getattr(args, flag) or config.get(key)
    if not value:
        msg = f"no {key}: pass --{flag} or set '{key}' in {args.config}"
        raise ConfigError(msg)
    return str(value)


@dataclass(frozen=True, slots=True)
class _Console:
    out: TextIO
    ask: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class _Session:
    """What every command needs once the command line and the config are resolved."""

    conn: sqlite3.Connection
    path: Path
    owner: str
    people: People
    labels: Mapping[str, str]
    console: _Console
    now: datetime


def _run(args: argparse.Namespace, console: _Console, now: datetime) -> int:
    out = console.out
    config = _config(args.config)
    owner = _setting(args, config, "owner", "owner")
    path = Path(_setting(args, config, "db", "database")).expanduser()
    people = _people(config, args.config)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(path)
    try:
        db.migrate(conn, now)
        labels = _labels(config, args.config)
        session = _Session(conn, path, owner, people, labels, console, now)
        if args.command == "import":
            return _import(session, args)
        if args.command == "balance":
            currency = args.currency.upper()
            monthclose.type_balance(
                conn,
                owner=owner,
                account=args.account,
                day=args.day,
                amount=args.amount,
                currency=currency,
            )
            out.write(f"{args.account} of {owner}: {args.amount} {currency} on {args.day}.\n")
            return OK
        last = args.through or args.month
        if last < args.month:
            msg = f"{last:%Y-%m} is before {args.month:%Y-%m}"
            raise ConfigError(msg)
        months = months_between(args.month, last)
        if args.command == "commit":
            return _commit(session, months)
        for month in months:
            monthclose.reopen(conn, owner=owner, month=month)
        out.write(f"{_span(months[0], months[-1])} is open again for {owner}.\n")
        return OK
    finally:
        conn.close()


def _import(session: _Session, args: argparse.Namespace) -> int:
    out = session.console.out
    statements, skipped = pipeline.load_files(_files(args.paths), database=True)
    statements = pipeline.relabel(statements, session.labels)
    owner, people = session.owner, session.people
    shared = args.rules or [Path("rules.local.yaml"), Path("rules.yaml")]
    own = classification.personal_file(shared[0], owner)
    rule_files = [own, *shared]
    instruments = classification.load_instruments(args.instruments)

    categories_file = shared[0].with_name(categories.FILE)

    others = [pattern for name, patterns in people.items() if name != owner for pattern in patterns]
    everyone = [pattern for patterns in people.values() for pattern in patterns]

    def process() -> pipeline.Month:
        known = categories.names(categories.load(categories_file))
        rules = classification.between_people(classification.load_rules(rule_files, known), others)
        extractions = [(s.source, s.extraction) for s in statements]
        return pipeline.process(extractions, rules, instruments, skipped, database=True)

    month = process()
    if args.review and month.uncategorized:
        ask = session.console.ask
        review.ask_categories(month.uncategorized, shared[0], out, ask, (own, everyone))
        month = process()
    added = categories.load(categories_file)
    batch = Batch(session.owner, statements, month, session.people, added)
    staged = monthclose.stage(session.conn, batch, session.now)
    first, last = month.period
    out.write(
        f"{_span(first, last)} for {session.owner}: {len(statements)} statements reconciled, "
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
            f"{staged.uncategorized} transactions need a category: "
            "import again with --review, or add rules by hand.\n"
        )
        return NEEDS_REVIEW
    through = "" if month.end is None else f" {last:%Y-%m}"
    out.write(f"Next: finanze commit {first:%Y-%m}{through}\n")
    return OK


def _files(paths: Sequence[Path]) -> list[Path]:
    """The files named, and the files directly inside the folders named."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files += [p for p in path.iterdir() if p.is_file() and not p.name.startswith(".")]
        elif path.is_file():
            files.append(path)
        else:
            msg = f"{path}: no such file or folder"
            raise ConfigError(msg)
    return files


def _span(first: date, last: date) -> str:
    """`September 2026`, or `September 2022 to July 2026` for several months."""
    if last <= month_end(first):
        return f"{first:%B %Y}"
    return f"{first:%B %Y} to {last:%B %Y}"


def _commit(session: _Session, months: Sequence[date]) -> int:
    """Commit after saving a copy of the database; the copy is dropped if nothing changed."""
    path, owner, now, out = session.path, session.owner, session.now, session.console.out
    copy = path.parent / "backups" / f"{path.stem}-{now:%Y%m%dT%H%M%S}.db"
    db.backup(session.conn, copy)
    done: list[date] = []
    try:
        for month in months:
            if monthclose.commit(session.conn, owner=owner, month=month, now=now):
                done.append(month)
    except BaseException:
        if done:  # each month is its own transaction: those before the failure stay committed
            out.write(f"{_span(done[0], done[-1])} committed for {owner}, then:\n")
        else:
            copy.unlink()
        raise
    span = _span(months[0], months[-1])
    if not done:
        copy.unlink()
        out.write(f"{span} was already committed for {owner}.\n")
        return OK
    out.write(f"{span} committed for {owner}. Copy from before: {copy}\n")
    return OK
