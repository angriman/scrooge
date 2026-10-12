"""`finanze import | commit | reopen | balance | keep | prices | query | serve | password |
publish`: the database from the terminal.

    finanze import <path>...  reconcile and classify the statements, then stage their months
    finanze commit <YYYY-MM>  make a staged month visible to the dashboards
    finanze reopen <YYYY-MM>  take a committed month back, to import it again
    finanze balance <account> <YYYY-MM-DD> <amount> <currency>
                              type what an account without statements held on a day
    finanze keep <path>...    keep the files of statements imported before originals were kept
    finanze prices            fetch the closes, splits and exchange rates not stored yet
    finanze query [filters]   list the committed transactions that match, with their totals
    finanze serve             show the database as web pages, on this machine only unless
                              `serve` in the config says otherwise; its Import page takes
                              statements as `import` does, for the person chosen there
    finanze password          ask for a password and print its digest for `serve`
    finanze publish [folder]  copy the database to the folder another machine shows it from

`commit` and `reopen` take a second month to cover every month from the first to it.

`import` also takes the scan of a second-pillar pension certificate: it gives the pension
fund's account the balance it states for its day, in the month of that day, which is
committed like any other. What was read is printed, to be compared with the paper.

`import` keeps every file it stages in the folder `originals` next to the database, under its
SHA-256: the rows of the database say which file they came from, and that file is there.
`keep` does the same for statements imported earlier: of the files named it keeps those the
database was filled from, and says which statements still have no original.

The database path, the person and the names that recognise each person as a counterparty
come from `finanze.yaml` (`database`, `owner`, `owners`), or from `--db` and `--owner`.
`account_labels` there names a further account at a broker by its account number.
`keep`, `prices`, `query`, `serve` and `publish` are the exceptions: they are about both
people.
`prices` is the only command that uses the network (D9).

`serve` listens on 127.0.0.1 and asks for no password, unless the config has (D18):

    serve:
      host: 192.168.1.20     # this machine's address the other devices reach; 0.0.0.0 for all
      names: [finanze.home]  # what else they call it
      password: scrypt$...   # the line `finanze password` prints

Another address than this machine's own is refused without a password. There is no flag for
any of it: where the pages can be seen from is written down once, not typed.

On a machine that only shows a copy of the database, `serve` also has `read_only: true`:
the copy is never created, brought up to date or changed, wherever it lies, and the pages
have no forms. A copy this code's schema does not fit is refused.

`publish` is how the copy gets there: into the folder named, or `publish` in the config, which
is that machine's share as it is mounted on this one. The database is never used over the
share: a snapshot is made here, copied whole, and renamed over the copy published before as
`finanze.db`. Only the database goes, whole: the config, the statements, the backups and the
rules stay here. A folder that does not exist is not created, since a share that is not
mounted is no folder.
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO

import uvicorn
import yaml

from finanze import (
    classification,
    importing,
    marketdata,
    monthclose,
    networth,
    pipeline,
    prices,
    publish,
    query,
    review,
)
from finanze.classification import UnknownInstrumentError
from finanze.errors import ConfigError, ExtractionError
from finanze.extractors.common import month_end, month_start, months_between
from finanze.models import Kind
from finanze.monthclose import MonthCloseError, People
from finanze.storage import db, originals, store
from finanze.storage.db import MigrationError
from finanze.storage.money import PrecisionError
from finanze.validation import ReconciliationError
from finanze.web import app as webapp
from finanze.web import auth

COMMANDS = (
    "import",
    "commit",
    "reopen",
    "balance",
    "keep",
    "prices",
    "query",
    "serve",
    "password",
    "publish",
)
# Commands that are not about one person's month.
_READ_BOTH = ("keep", "prices", "query", "serve", "publish")
OK, FAILED, NEEDS_REVIEW = 0, 1, 2
_AMOUNT = 3  # the column `query` prints flush right
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
    query.QueryError,
    publish.PublishError,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def main(
    argv: Sequence[str],
    out: TextIO,
    ask: Callable[[str], str] = input,
    now: Callable[[], datetime] = _utc_now,
    source: prices.PriceSource | None = None,
) -> int:
    """Run one command. `source` stands in for the network in tests."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "password":  # about no database and no config
            return _password(out)
        return _run(args, _Console(out, ask, source), now())
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
    files = argparse.ArgumentParser(add_help=False)
    files.add_argument("--config", type=Path, default=Path("finanze.yaml"))
    files.add_argument("--db", type=Path, help="database file (default: 'database' in the config)")
    shared = argparse.ArgumentParser(add_help=False, parents=[files])
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
    keep = commands.add_parser("keep", parents=[files], help="keep the files of stored statements")
    keep.add_argument(
        "paths",
        type=Path,
        nargs="+",
        metavar="path",
        help="statement files, or folders holding them (subfolders are not searched)",
    )
    fetch = commands.add_parser("prices", parents=[files], help="fetch closes and rates")
    fetch.add_argument(
        "--through",
        type=date.fromisoformat,
        help="the last day to fetch, as YYYY-MM-DD (default: today)",
    )
    fetch.add_argument(
        "--instruments",
        type=Path,
        default=Path("instruments.yaml"),
        help="where 'price_symbol' names the symbol an instrument's prices are found under",
    )
    _query_parser(commands.add_parser("query", parents=[files], help="find transactions"))
    serve = commands.add_parser("serve", parents=[files], help="browse the database")
    serve.add_argument("--port", type=int, default=8765, help="default: 8765")
    serve.add_argument(
        "--rules",
        type=Path,
        default=Path("rules.local.yaml"),
        help="where a remembered category is saved as a rule (default: rules.local.yaml); "
        "rows naming a person go to that owner's rules.<owner>.yaml next to it",
    )
    serve.add_argument(
        "--instruments",
        type=Path,
        default=Path("instruments.yaml"),
        help="what the import page reads about instruments (default: instruments.yaml)",
    )
    commands.add_parser("password", help="print a password's digest, for 'serve' in the config")
    send = commands.add_parser("publish", parents=[files], help="copy the database to be shown")
    send.add_argument(
        "folder",
        type=Path,
        nargs="?",
        help="the mounted folder the copy goes to (default: 'publish' in the config)",
    )
    return parser


def _query_parser(find: argparse.ArgumentParser) -> None:
    day = "a day, a month or a year: 2025-05-17, 2025-05, 2025"
    find.add_argument("--from", dest="first", type=_first_day, help=f"from {day}")
    find.add_argument("--to", dest="last", type=_last_day, help=f"up to and including {day}")
    find.add_argument(
        "--booking",
        action="store_true",
        help="compare and group by the booking date, not the day it really happened",
    )
    find.add_argument("--owner", help="only this person's (default: both)")
    find.add_argument("--account", help="only this account, by its name")
    find.add_argument("--category", help="a category, or a top-level one for all its leaves")
    find.add_argument("--kind", type=Kind, choices=list(Kind), help="only rows of this kind")
    find.add_argument("--min", dest="at_least", type=_amount, help="amounts of at least this size")
    find.add_argument("--max", dest="at_most", type=_amount, help="amounts of at most this size")
    find.add_argument("--text", default="", help="words in the counterparty or the description")
    find.add_argument("--summary", action="store_true", help="print the totals without the rows")


def _span_of(text: str) -> tuple[date, date]:
    try:
        return query.span(text)
    except ValueError:
        msg = f"{text!r} is not a day, a month or a year like 2025-05-17, 2025-05 or 2025"
        raise argparse.ArgumentTypeError(msg) from None


def _first_day(text: str) -> date:
    return _span_of(text)[0]


def _last_day(text: str) -> date:
    return _span_of(text)[1]


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
    source: prices.PriceSource | None = None  # None: the real one


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
    owner = "" if args.command in _READ_BOTH else _setting(args, config, "owner", "owner")
    path = Path(_setting(args, config, "db", "database")).expanduser()
    people = _people(config, args.config)
    if args.command == "serve":  # the pages open the database themselves, read-only
        reach = _reach(config, args.config)
        if reach.read_only:
            _check_copy(path)
        else:
            _open(path, now).close()
        # What the import page reads, as `finanze import` would here: the rules both share,
        # then the generic ones next to them.
        shared = [args.rules, args.rules.with_name("rules.yaml")]
        imports = importing.Setup(shared, args.instruments, people, _labels(config, args.config))
        pages = _Pages(webapp.Rules(args.rules, people), imports)
        return _serve(path, args.port, pages, reach, out)
    if args.command == "publish" and not path.is_file():
        msg = f"{path}: no database to publish"
        raise ConfigError(msg)
    conn = _open(path, now)
    try:
        if args.command == "publish":
            return _publish(conn, path, args.folder or config.get("publish"), args.config, out)
        if args.command == "query":
            return _query(conn, args, out)
        if args.command == "keep":
            return _keep(conn, path, _files(args.paths), out)
        if args.command == "prices":
            return _prices(conn, args, console, now)
        labels = _labels(config, args.config)
        return _for_owner(_Session(conn, path, owner, people, labels, console, now), args)
    finally:
        conn.close()


def _open(path: Path, now: datetime) -> sqlite3.Connection:
    """The database, created if there is none, with its schema brought up to date."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(path)
    try:
        db.migrate(conn, now)
    except BaseException:
        conn.close()
        raise
    return conn


def _check_copy(path: Path) -> None:
    """A copy is shown as it was published or not at all: nothing here writes to it or next
    to it, so its schema must already be the one this code reads."""
    again = "publish it again, with the same version of finanze on both machines"
    if not path.is_file():
        msg = f"{path}: no copy of the database to show: run 'finanze publish' where the data is"
        raise ConfigError(msg)
    try:
        with db.reading(path) as conn:
            found = db.version(conn)
    except sqlite3.DatabaseError as exc:
        msg = f"{path}: not a database ({exc}): {again}"
        raise ConfigError(msg) from None
    wanted = db.latest()
    if found != wanted:
        msg = f"{path}: schema version {found}, where this code reads version {wanted}: {again}"
        raise ConfigError(msg)


def _for_owner(session: _Session, args: argparse.Namespace) -> int:
    """The commands about one person's months."""
    conn, owner, out = session.conn, session.owner, session.console.out
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


@dataclass(frozen=True, slots=True)
class _Reach:
    """Where the pages are served and who may see them: `serve` in the config."""

    host: str = webapp.HOST
    names: tuple[str, ...] = ()  # what other devices call this machine
    password: str = ""  # its digest; empty: nobody is asked
    read_only: bool = False  # the database is a copy: shown, never written


def _reach(config: dict[str, object], path: Path) -> _Reach:
    """`serve: {host, names, password, read_only}`. Beyond this machine only with a password
    (D18)."""
    serve = config.get("serve") or {}
    if not isinstance(serve, dict) or set(serve) - {"host", "names", "password", "read_only"}:
        msg = f"{path}: 'serve' takes 'host', 'names', 'password' and 'read_only'"
        raise ConfigError(msg)
    host, names = str(serve.get("host") or webapp.HOST), serve.get("names") or []
    password, read_only = str(serve.get("password") or ""), serve.get("read_only", False)
    if not isinstance(read_only, bool):
        msg = f"{path}: serve: read_only: expected true or false, not {read_only!r}"
        raise ConfigError(msg)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        msg = f"{path}: serve: host: {host!r} is not an address like 192.168.1.20"
        raise ConfigError(msg) from None
    if not isinstance(names, list):
        msg = f"{path}: serve: names: expected a list of the names devices call this machine"
        raise ConfigError(msg)
    if password:
        try:
            auth.check(password)
        except auth.MalformedDigestError as exc:
            msg = f"{path}: serve: password: {exc}"
            raise ConfigError(msg) from None
    elif not address.is_loopback or names:
        msg = (
            f"{path}: serve: the pages are not served beyond this machine without a password: "
            "run 'finanze password' and put its line under 'serve'"
        )
        raise ConfigError(msg)
    called = [str(name) for name in names]
    if address.is_unspecified and not called:
        msg = f"{path}: serve: names: say what the devices call this machine: {host} is no name"
        raise ConfigError(msg)
    if not address.is_loopback and not address.is_unspecified:
        called.insert(0, host)
    return _Reach(host, tuple(called), password, read_only)


@dataclass(frozen=True, slots=True)
class _Pages:
    """What the pages that change the database read and write besides it."""

    rules: webapp.Rules
    imports: importing.Setup


def _serve(database: Path, port: int, pages: _Pages, reach: _Reach, out: TextIO) -> int:
    """Serve the pages until interrupted, where `serve` in the config says: there is no flag
    to listen anywhere else (D18)."""
    shown = f"[{reach.host}]" if ":" in reach.host else reach.host
    out.write(f"{database} is at http://{shown}:{port} (Ctrl-C to stop)\n")
    if reach.names:
        out.write(f"  for other devices, with the password: {', '.join(reach.names)}\n")
    if reach.read_only:
        out.write("  a copy, shown as 'finanze publish' left it: nothing here changes it\n")
    app = webapp.create_app(
        database,
        rules=pages.rules,
        imports=pages.imports,
        names=reach.names,
        password=reach.password,
        read_only=reach.read_only,
    )
    uvicorn.run(app, host=reach.host, port=port, log_level="warning")
    return OK


def _publish(
    conn: sqlite3.Connection, database: Path, folder: object, config: Path, out: TextIO
) -> int:
    """Copy the database to `folder`, and say what went: all of it, open months included."""
    if folder is None:
        msg = f"no folder to publish to: name one, or set 'publish' in {config}"
        raise ConfigError(msg)
    if not isinstance(folder, (str, Path)):
        msg = f"{config}: 'publish' must be the folder the copy goes to"
        raise ConfigError(msg)
    done = publish.run(conn, database, Path(folder).expanduser())
    out.write(
        f"Published {done.path}: {done.size:,} bytes, {done.committed} committed transactions, "
        f"schema version {done.version}.\n"
    )
    if done.open_months:
        out.write(
            f"  with it: {done.staged} transactions of {done.open_months} open "
            f"month{'' if done.open_months == 1 else 's'}, which only the months pages show\n"
        )
    return OK


def _password(out: TextIO) -> int:
    """Ask twice for a password and print the line that stands for it in the config."""
    secret = getpass.getpass  # what is typed does not show on the screen
    password = secret("Password: ")
    if len(password) < auth.SHORTEST:
        out.write(f"error: a password has at least {auth.SHORTEST} characters\n")
        return FAILED
    if secret("Once more: ") != password:
        out.write("error: the two do not match\n")
        return FAILED
    out.write(f"Put this under 'serve' in finanze.yaml:\n  password: {auth.digest(password)}\n")
    return OK


def _prices(
    conn: sqlite3.Connection, args: argparse.Namespace, console: _Console, now: datetime
) -> int:
    """Fetch what is missing; what could not be fetched is listed and nothing is guessed.

    Up to yesterday at most: today's close is not final while a market is open, and what is
    stored is never fetched again.
    """
    yesterday = now.date() - timedelta(days=1)
    typed = classification.load_instruments(args.instruments)
    refreshed = marketdata.refresh(
        conn,
        console.source or prices.Yahoo(),
        through=min(args.through or yesterday, yesterday),
        now=now,
        symbols={symbol: entry.price_symbol for symbol, entry in typed.items()},
        currencies=networth.CURRENCIES,
    )
    out = console.out
    out.write(
        f"{refreshed.closes} closes, {refreshed.rates} exchange rates and "
        f"{refreshed.splits} splits stored.\n"
    )
    out.writelines(f"  missing: {problem}\n" for problem in refreshed.problems)
    return NEEDS_REVIEW if refreshed.problems else OK


def _query(conn: sqlite3.Connection, args: argparse.Namespace, out: TextIO) -> int:
    wanted = query.Filter(
        first=args.first,
        last=args.last,
        booking=args.booking,
        owner=args.owner,
        account=args.account,
        category=args.category,
        kind=args.kind,
        at_least=args.at_least,
        at_most=args.at_most,
        text=args.text,
    )
    result = query.run(conn, wanted)
    if not args.summary:
        _print_rows(result.rows, out, booking=args.booking)
    out.write(f"{len(result.rows)} transaction{'' if len(result.rows) == 1 else 's'}\n")
    _print_sums("Total", result.totals, out)
    for title, sums in (("By year", result.by_year), ("By month", result.by_month)):
        if len({item.period for item in sums}) > 1:
            _print_sums(title, sums, out)
    return OK


def _print_rows(rows: Sequence[query.Row], out: TextIO, *, booking: bool) -> None:
    """One line per transaction, in columns as wide as their longest value."""
    lines = [
        (
            (row.booking_date if booking else row.date).isoformat(),
            row.owner,
            row.account,
            f"{row.amount:,.2f}",
            row.currency,
            row.category or "",
            row.counterparty or row.description,
        )
        for row in rows
    ]
    widths = [max(len(value) for value in column) for column in zip(*lines, strict=True)]
    for line in lines:
        cells = [
            value.rjust(width) if column == _AMOUNT else value.ljust(width)
            for column, (value, width) in enumerate(zip(line, widths, strict=True))
        ]
        out.write("  ".join(cells).rstrip() + "\n")


def _print_sums(title: str, sums: Sequence[query.Sum], out: TextIO) -> None:
    if not sums:
        return
    out.write(f"{title}:\n")
    width = max(len(f"{item.total:,.2f}") for item in sums)
    for item in sums:
        period = f"{item.period}  " if item.period else ""
        out.write(f"  {period}{item.total:>{width},.2f} {item.currency}  ({item.count})\n")


def _import(session: _Session, args: argparse.Namespace) -> int:
    out = session.console.out
    shared = args.rules or [Path("rules.local.yaml"), Path("rules.yaml")]
    setup = importing.Setup(shared, args.instruments, session.people, session.labels)
    found = importing.read(session.owner, _files(args.paths), setup)
    if args.review and found.month.uncategorized:
        everyone = [pattern for patterns in session.people.values() for pattern in patterns]
        personal = (setup.own(session.owner), everyone)
        ask = session.console.ask
        review.ask_categories(found.month.uncategorized, shared[0], out, ask, personal)
        found = importing.again(found, setup)
    staged = importing.stage(session.conn, session.path, found, setup, session.now)
    month, statements, certificates = found.month, found.statements, found.certificates
    first, last = month.period
    if statements:
        out.write(
            f"{_span(first, last)} for {session.owner}: {len(statements)} statements "
            f"reconciled, {staged.transactions} transactions staged.\n"
        )
    _print_certificates(certificates, session.owner, out)
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
    if statements:
        out.write(f"Next: finanze commit {first:%Y-%m}{through}\n")
    covered = set(months_between(first, last)) if statements else set()
    stated = {month_start(certificate.stated.as_of) for certificate in certificates}
    out.writelines(f"Next: finanze commit {each:%Y-%m}\n" for each in sorted(stated - covered))
    return OK


def _keep(conn: sqlite3.Connection, database: Path, files: Sequence[Path], out: TextIO) -> int:
    """Keep those of `files` the database was filled from, and say what is still not kept."""
    folder = originals.folder_of(database)
    known = store.statement_names(conn)
    kept = originals.keep(folder, files, known)
    out.write(f"{len(kept)} of {len(files)} files kept in {folder}.\n")
    without = sorted(
        name for digest, name in known.items() if originals.find(folder, digest, name) is None
    )
    out.writelines(f"  no original: {name}\n" for name in without)
    return OK


def _print_certificates(
    certificates: Sequence[pipeline.Certificate], owner: str, out: TextIO
) -> None:
    """What was read off each certificate: the one check there is for a figure nothing on the
    paper adds up to."""
    for certificate in sorted(certificates, key=lambda c: (c.stated.as_of, c.account)):
        stated = certificate.stated
        out.write(
            f"{certificate.account} of {owner}: {stated.balance:,.2f} {stated.currency} on "
            f"{stated.as_of}, read off the {stated.source} certificate {certificate.filename} "
            f"(mandatory part {stated.mandatory:,.2f}).\n"
        )
        if stated.opened:
            day, balance = stated.opened
            out.write(f"  and {balance:,.2f} {stated.currency} on {day}, where its year began\n")
        if not certificate.adds_up:
            out.write(
                "  note: nothing on this certificate adds up to that figure: compare it with "
                "the paper\n"
            )


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
    copy = monthclose.copy_before(session.conn, path, now)
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
