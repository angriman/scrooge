"""`scrooge init | import | commit | statements | uncommit | discard | balance | keep | logo |
prices | query | serve | password | publish | migrate-files | doctor`: the database from the
terminal.

    scrooge init              first run: ask who you are and make your setup in users/<name>/,
                              its database too (--container: for the app in a container, its
                              pages reached from outside it with a password; --db <path>: only
                              an empty database there)
    scrooge import <path>...  reconcile and classify the statements, then stage them
    scrooge commit            make what is staged visible to the dashboards
    scrooge statements        list the statements stored, staged ones first, with their ids
    scrooge uncommit <id>     take a committed statement back: it is staged again
    scrooge discard <id>      remove a staged statement and everything it staged
    scrooge balance <account> <YYYY-MM-DD> <amount> <currency>
                              type what an account without statements, or a pension fund
                              between its certificates, held on a day (--remove: take it back)
    scrooge position <account> <YYYY-MM-DD> <symbol> <quantity> <currency>
                              type how many shares of one stock or ETF it held on a day
                              (--remove: take it back)
    scrooge keep <path>...    keep the files of statements imported before originals were kept
    scrooge logo <symbol> <file>
                              keep an SVG or PNG as the logo of an instrument (--remove to drop it)
    scrooge prices            fetch the closes, splits and exchange rates not stored yet
    scrooge query [filters]   list the committed transactions that match, with their totals
    scrooge serve             show the database as web pages, on this machine only unless
                              `serve` in the config says otherwise; its Import page takes
                              statements as `import` does, for the person chosen there
    scrooge password          ask for a password and print its digest for `serve`
    scrooge publish [folder]  copy the database to the folder another machine shows it from
    scrooge migrate-files     write the rules, categories.yaml and the config again with
                              today's category names (--write; without it, only say what)
    scrooge doctor            say what is wrong: the schema, the database file, the rules,
                              the originals, the prices; changes nothing

There are no months to close (decided 2026-10-16): statements of any period are imported
whenever they are at hand, and `commit` commits what is staged. Of an account, what is
committed already is left as it is: a statement is staged from the day after. A file that is
not recognised, does not reconcile or is of another person's account is refused on its own,
and the others are staged; `import` then exits 1, whatever else was staged or needs a
category (it exits 2 only when nothing was refused). What a statement books on the day of the
import waits for the next one, since that day is not over.

`import` opens a zip and reads each file in it as if it had been named on its own; the
zip itself is never kept. A zip that is broken or too big is refused as a whole.

`import` also takes the scan of a second-pillar pension certificate: it gives the pension
fund's account the balance it states for its day, committed like any other statement. What
was read is printed, to be compared with the paper.

`import` keeps every file it stages in the folder `originals` next to the database, under its
SHA-256: the rows of the database say which file they came from, and that file is there.
`keep` does the same for statements imported earlier: of the files named it keeps those the
database was filled from, and says which statements still have no original.

The config is `--config`; else `scrooge.yaml` in the folder the command runs in; else the
setup `users/<name>/scrooge.yaml` that `--user` or $SCROOGE_USER names, or the only one in
`users/`. Its relative paths are relative to the folder the command runs in, as before: the
config `scrooge init` writes names `users/<name>/...`, so the commands run from the folder
`users/` is in. Run from another, they name files that are not there: a database or a rules
file that is not there is refused, with the path looked for and the folder run in, never
made or read as empty. `rules.<owner>.yaml`, `categories.yaml` and `instruments.yaml` may be
missing: they are written when there is something to keep in them.
The database path, the person and the names that recognise each person as a counterparty
come from the config (`database`, `owner`, `owners`), or from `--db` and `--owner`. `currency`
there is what the pages show figures in until another is chosen on them.
`unmatched` there is the category of the expenses and income no rule matches: they then need
no review, for who tracks net worth alone. `scrooge init` writes `unmatched: Uncategorized`.
`account_labels` there names a further account at a broker by its account number.
`rules` there lists the rules files, first wins, and `instruments` names the instruments
file, for `import`, `serve` and `prices`, or `--rules` and `--instruments`; without either,
`rules.yaml` and `instruments.yaml`, in the folder the command runs in. The first rules file
is the one both people share: each person's `rules.<owner>.yaml` and `categories.yaml` are
next to it, and answers and categories given are written there.
`keep`, `logo`, `prices`, `query`, `serve` and `publish` are the exceptions: they are about both
people.
`logo` keeps the image in the database (decided 2026-11-07), under the symbol `instruments`
has for it: a ticker, an ISIN or a CUSIP. The holdings page lists the holdings that still have
none. Nothing fetches a logo.
`migrate-files` writes again, once, the names the app still reads but no longer writes: the
Italian names of the categories (until 2026-11-24) and `uscite`/`entrate` in categories.yaml.
It reads the files `import` and `serve` read, as they find them, and each person's own rules
file; it says what it would change and changes nothing unless given `--write`, which keeps
each file it changes as it was, next to it (`<name>.before-migrate-files`). It needs no
database.
`doctor` reads the database without bringing its schema up to date, so it says whether the
next command would migrate it. It reads the rules files, each person's own, categories.yaml,
`unmatched` and instruments.yaml as `import` would and lists every problem in them; it lists
the statements whose file is not in `originals`, and the holdings and currencies with no close
or rate of the week before yesterday. It exits 1 when anything is wrong.
`prices` is the only command that uses the network (D9); `serve` too, only when the import
page's button looks up a T-bill's maturity (decided 2026-11-03).

Only `scrooge init` makes a database: every other command refuses one that is not there, an
explicit `--db` too. Every command but a read-only `serve` brings its schema up to date: a
database at an older schema is copied to `backups/` next to it first, and a migration that
fails stops the command (`serve` does not start).

`serve` listens on 127.0.0.1 and asks for no password, unless the config has (D18):

    serve:
      host: 192.168.1.20     # this machine's address the other devices reach; 0.0.0.0 for all
      names: [scrooge.home]  # what else they call it
      password: scrypt$...   # the line `scrooge password` prints

Another address than this machine's own is refused without a password. There is no flag for
any of it: where the pages can be seen from is written down once, not typed.

On a machine that only shows a copy of the database, `serve` also has `read_only: true`:
the copy is never created, brought up to date or changed, wherever it lies, and the pages
have no forms. A copy this code's schema does not fit is refused. Of the commands only
`serve`, `query` and `statements` run there, reading the copy as it is; every other one is
refused before it opens anything.

`publish` is how the copy gets there: into the folder named, or `publish` in the config, which
is that machine's share as it is mounted on this one. The database is never used over the
share: a snapshot is made here, copied whole, and renamed over the copy published before as
`scrooge.db`. Only the database goes, whole: the config, the statements, the backups and the
rules stay here. A folder that does not exist is not created, since a share that is not
mounted is no folder.
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import os
import re
import sqlite3
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO

import uvicorn
import yaml

from scrooge import (
    benchmark,
    categories,
    classification,
    doctor,
    importing,
    marketdata,
    migratefiles,
    networth,
    onboarding,
    pipeline,
    prices,
    publish,
    query,
    review,
    router,
    staging,
)
from scrooge.classification import UnknownInstrumentError
from scrooge.errors import ConfigError, ExtractionError, not_yaml
from scrooge.models import Kind, TypedHolding
from scrooge.staging import People, StagingError
from scrooge.storage import db, logos, money, originals, store
from scrooge.storage.db import MigrationError
from scrooge.storage.money import PrecisionError
from scrooge.validation import ReconciliationError
from scrooge.web import app as webapp
from scrooge.web import auth

COMMANDS = (
    "import",
    "commit",
    "statements",
    "uncommit",
    "discard",
    "balance",
    "position",
    "keep",
    "logo",
    "prices",
    "query",
    "serve",
    "password",
    "publish",
    "init",
    "migrate-files",
    "doctor",
)
# Commands that are not about one person's statements.
_READ_BOTH = ("keep", "logo", "prices", "query", "serve", "publish", "doctor")
# What a machine that only shows a copy of the database may run: the rest would write to it.
_SHOWING = ("serve", "query", "statements")
OK, FAILED, NEEDS_REVIEW = 0, 1, 2
# Where the rules and the instruments are read from when neither a flag nor the config says.
RULES = ["rules.yaml"]
INSTRUMENTS = "instruments.yaml"
USER = "SCROOGE_USER"  # the setup in users/ to use, when --user does not say
_ONLY_INIT = "Only 'scrooge init' makes a database ('scrooge init --db <path>' an empty one alone)"
_CODE = 3  # letters in a currency code
_AMOUNT = 3  # the column `query` prints flush right
_ERRORS = (
    ExtractionError,
    ReconciliationError,
    ConfigError,
    UnknownInstrumentError,
    StagingError,
    MigrationError,
    PrecisionError,
    store.UnknownCategoryError,
    logos.LogoError,
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
        if args.command == "init":
            if args.db is not None:
                return _create(args.db.expanduser(), now(), out)
            return onboarding.run(Path(onboarding.USERS), out, ask, now(), container=args.container)
        args.config = _find_config(args)
        if args.command == "migrate-files":  # about the files alone, not the database
            return _migrate_files(args, out)
        return _run(args, _Console(out, ask, source), now())
    except _ERRORS as exc:
        out.write(f"error: {exc}\n")
        return FAILED


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scrooge", description=__doc__)
    setup, files = _setup_flags()
    shared = argparse.ArgumentParser(add_help=False, parents=[files])
    shared.add_argument("--owner", help="whose statements (default: 'owner' in the config)")
    commands = parser.add_subparsers(dest="command", required=True)

    load = commands.add_parser("import", parents=[shared], help="stage statements")
    load.add_argument(
        "paths",
        type=Path,
        nargs="+",
        metavar="path",
        help="statement files, zips of them, or folders holding them (subfolders too; hidden "
        "files are skipped)",
    )
    _rules_flags(load)
    load.add_argument(
        "--review",
        action="store_true",
        help="ask for the category of unmatched rows and save the answers as rules; new "
        "categories go in categories.yaml, next to the first rules file",
    )
    commands.add_parser("commit", parents=[shared], help="commit what is staged")
    commands.add_parser("statements", parents=[shared], help="list the statements stored")
    for name, text in (
        ("uncommit", "take a committed statement back"),
        ("discard", "remove a staged statement"),
    ):
        command = commands.add_parser(name, parents=[shared], help=text)
        command.add_argument("id", type=int, help="the statement, as `scrooge statements` lists it")
    typed = commands.add_parser("balance", parents=[shared], help="type an account's balance")
    typed.add_argument(
        "account",
        help="an account without statements, a pension fund, or one an export that states no "
        "balance feeds (Schwab)",
    )
    typed.add_argument("day", type=date.fromisoformat, help="the day, as YYYY-MM-DD")
    typed.add_argument(
        "amount", type=_amount, nargs="?", help="what it held at the end of that day"
    )
    typed.add_argument("currency", nargs="?", help="CHF, EUR, ...")
    typed.add_argument(
        "--remove", action="store_true", help="remove the balance typed for that day instead"
    )
    held = commands.add_parser(
        "position", parents=[shared], help="type how many shares an account held"
    )
    held.add_argument(
        "account",
        help="an account without statements, or one an export that states no holdings feeds "
        "(Schwab)",
    )
    held.add_argument("day", type=date.fromisoformat, help="the day, as YYYY-MM-DD")
    held.add_argument("symbol", help="the ticker, as Yahoo knows it (e.g. AAPL, NESN.SW)")
    held.add_argument(
        "quantity", type=_quantity, nargs="?", help="shares held at the end of that day; 0: none"
    )
    held.add_argument("currency", nargs="?", help="the currency it trades in: USD, CHF, ...")
    held.add_argument(
        "--remove", action="store_true", help="remove what was typed of it that day instead"
    )
    held.add_argument(
        "--tab",
        choices=("Stocks", "ETFs"),
        help="for an instrument not known yet (default: Stocks); a known one keeps its own",
    )
    _rules_flags(held)
    keep = commands.add_parser("keep", parents=[files], help="keep the files of stored statements")
    keep.add_argument(
        "paths",
        type=Path,
        nargs="+",
        metavar="path",
        help="statement files, or folders holding them (subfolders too; hidden files are skipped)",
    )
    mark = commands.add_parser("logo", parents=[files], help="keep an instrument's logo")
    mark.add_argument("symbol", help="the instrument's symbol: a ticker, an ISIN or a CUSIP")
    mark.add_argument("file", type=Path, nargs="?", help="an SVG or PNG image of at most 256 KiB")
    mark.add_argument("--source", default="", help="where the image came from (default: its name)")
    mark.add_argument("--remove", action="store_true", help="drop the logo of the symbol")
    fetch = commands.add_parser("prices", parents=[files], help="fetch closes and rates")
    fetch.add_argument(
        "--through",
        type=date.fromisoformat,
        help="the last day to fetch, as YYYY-MM-DD (default: today)",
    )
    fetch.add_argument(
        "--instruments",
        type=Path,
        help="where 'price_symbol' names the symbol an instrument's prices are found under "
        "(default: 'instruments' in the config, or instruments.yaml)",
    )
    _query_parser(commands.add_parser("query", parents=[files], help="find transactions"))
    serve = commands.add_parser("serve", parents=[files], help="browse the database")
    serve.add_argument("--port", type=int, default=8765, help="default: 8765")
    _rules_flags(serve)
    commands.add_parser("password", help="print a password's digest, for 'serve' in the config")
    first = commands.add_parser("init", help="first run: make a setup of your own in users/<name>/")
    first.add_argument(
        "--container",
        action="store_true",
        help="for the app in a container: its pages listen on every address of it, under the "
        "names and with the password asked for",
    )
    first.add_argument(
        "--db",
        type=Path,
        help="make only an empty database there, for a config written by hand; nothing is asked",
    )
    _care_parsers(commands, setup, files)
    return parser


def _care_parsers(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    setup: argparse.ArgumentParser,
    files: argparse.ArgumentParser,
) -> None:
    """The commands that look after the setup: `publish`, `migrate-files`, `doctor`."""
    send = commands.add_parser("publish", parents=[files], help="copy the database to be shown")
    send.add_argument(
        "folder",
        type=Path,
        nargs="?",
        help="the mounted folder the copy goes to (default: 'publish' in the config)",
    )
    migrate = commands.add_parser(
        "migrate-files", parents=[setup], help="write the files again with today's names"
    )
    _migrate_flags(migrate)
    check = commands.add_parser("doctor", parents=[files], help="say what is wrong; change nothing")
    _rules_flags(check)


def _setup_flags() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    """`--config` and `--user`, of every command that reads the config; and those with `--db`,
    of the commands that open the database."""
    setup = argparse.ArgumentParser(add_help=False)
    setup.add_argument(
        "--config",
        type=Path,
        help="the config (default: scrooge.yaml here, else the setup in users/ --user names, "
        "or the only one there)",
    )
    setup.add_argument(
        "--user", help=f"the setup users/<name>/ to use (default: ${USER}, or the only one)"
    )
    files = argparse.ArgumentParser(add_help=False, parents=[setup])
    files.add_argument("--db", type=Path, help="database file (default: 'database' in the config)")
    return setup, files


def _migrate_flags(migrate: argparse.ArgumentParser) -> None:
    _rules_flag(migrate)
    migrate.add_argument(
        "--write",
        action="store_true",
        help="write the changes, keeping each file as it was next to it (default: only say them)",
    )


def _rules_flags(command: argparse.ArgumentParser) -> None:
    """`--rules` and `--instruments`, of the commands that classify statements."""
    _rules_flag(command)
    command.add_argument(
        "--instruments",
        type=Path,
        help="what is known about instruments (default: 'instruments' in the config, or "
        "instruments.yaml)",
    )


def _rules_flag(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--rules",
        type=Path,
        action="append",
        help="rules file(s), first wins (default: 'rules' in the config, or rules.yaml); "
        "the owner's own rules.<owner>.yaml, next to the first, is read "
        "before them; a remembered category is saved in the first, or the owner's",
    )


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
        amount = Decimal(text)
    except InvalidOperation:
        amount = Decimal("NaN")
    if not (amount.is_finite() and money.fits(amount)):
        msg = f"{text!r} is not an amount like 1234.56"
        raise argparse.ArgumentTypeError(msg)
    return amount


def _currency_code(text: str) -> str:
    """A currency as the database keeps it: three capital letters."""
    code = text.upper()
    if not (len(code) == _CODE and code.isascii() and code.isalpha()):
        msg = f"{text!r} is not a currency code like CHF or USD"
        raise StagingError(msg)
    return code


def _quantity(text: str) -> Decimal:
    quantity = _amount(text)
    if quantity < 0:
        msg = f"{text!r} is no number of shares held: 0 or more"
        raise argparse.ArgumentTypeError(msg)
    return quantity


def _find_config(args: argparse.Namespace) -> Path:
    """`--config`; else scrooge.yaml in the folder the command runs in, as before users/;
    else the setup `--user` (or $SCROOGE_USER) names in users/; else the only one there.

    A config here comes first, so that a setup in users/ never takes over one that was
    already in use. A name is the folder's own name, or what `scrooge init` made of it.
    """
    if args.config is not None:
        return Path(args.config)
    here = Path(onboarding.CONFIG)
    named = args.user or os.environ.get(USER, "")
    if here.is_file() and not named:
        return here
    users = Path(onboarding.USERS)
    found = sorted(config.parent.name for config in users.glob(f"*/{onboarding.CONFIG}"))
    if named:
        chosen = onboarding.find_setup(named, found)
        if chosen is None:
            there = f"there are {', '.join(found)}" if found else "there is none yet"
            msg = f"no setup {named!r} in {users}/ ({there}): make one with 'scrooge init'"
            raise ConfigError(msg)
        return users / chosen / onboarding.CONFIG
    if len(found) > 1:
        msg = f"{users}/ holds several setups ({', '.join(found)}): choose one with --user"
        raise ConfigError(msg)
    return users / found[0] / onboarding.CONFIG if found else here


def _config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(not_yaml(path, exc)) from None
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


def _rules(
    args: argparse.Namespace, config: dict[str, object], *, present: bool = True
) -> list[Path]:
    """`--rules`, or `rules: [path, ...]` in the config, or `rules.yaml` in the folder the
    command runs in. Each must be there, unless not `present` is asked for (a copy shown has
    none): a file that is not, from another folder, would read as no rules at all."""
    if args.rules:
        found, origin = list(args.rules), "--rules"
    else:
        listed = config.get("rules", RULES)
        if (
            not isinstance(listed, list)
            or not listed
            or not all(isinstance(p, str) for p in listed)
        ):
            msg = (
                f"{args.config}: 'rules' must list the rules files, the one both people share first"
            )
            raise ConfigError(msg)
        found = [Path(path).expanduser() for path in listed]
        named = "rules" in config
        origin = (
            f"'rules' in {args.config}" if named else f"{RULES[0]}, as {args.config} names none,"
        )
    for path in found if present else ():
        then = "A rules file with no rules yet holds the line 'rules: []'"
        _refuse_missing(path, "rules file", origin, then)
    return found


def _refuse_missing(path: Path, what: str, origin: str, then: str) -> None:
    """A file the config or a flag names that is not there is refused, never taken as empty:
    a relative path is read from the folder the command runs in, which may be another."""
    if path.is_file():
        return
    if path.is_absolute():
        msg = f"no {what} at {path} ({origin}). {then}"
    else:
        msg = (
            f"no {what} at {path.absolute()}: {origin} is relative to the folder this runs in, "
            f"{Path.cwd()}: run scrooge from the folder 'scrooge init' was run in. {then}"
        )
    raise ConfigError(msg)


def _instruments(args: argparse.Namespace, config: dict[str, object]) -> Path:
    """`--instruments`, or `instruments: path` in the config, or `instruments.yaml`."""
    if args.instruments:
        return Path(args.instruments)
    named = config.get("instruments", INSTRUMENTS)
    if not isinstance(named, str) or not named:
        msg = f"{args.config}: 'instruments' must be the path of the instruments file"
        raise ConfigError(msg)
    return Path(named).expanduser()


def _currency(config: dict[str, object], path: Path) -> str:
    """`currency`: what the pages show figures in until another is chosen there."""
    named = config.get("currency", networth.DEFAULT_CURRENCY)
    if not isinstance(named, str) or named.upper() not in networth.CURRENCIES:
        msg = f"{path}: 'currency' must be one of {', '.join(networth.CURRENCIES)}"
        raise ConfigError(msg)
    return named.upper()


def _currencies(config: dict[str, object], path: Path) -> tuple[str, ...]:
    """`currencies`: those the month-end table of net worth shows side by side."""
    named = config.get("currencies", list(networth.DEFAULT_COLUMNS))
    if not isinstance(named, list) or not named:
        named = [None]
    codes = [code.upper() if isinstance(code, str) else "" for code in named]
    if not set(codes) <= set(networth.CURRENCIES):
        msg = f"{path}: 'currencies' must be a list of {', '.join(networth.CURRENCIES)}"
        raise ConfigError(msg)
    return tuple(dict.fromkeys(codes))  # each once, in the order named


def _benchmarks(config: dict[str, object], path: Path) -> tuple[str, ...]:
    """`benchmarks`: the symbols, as Yahoo knows them, of what the investments are compared
    with; VT and VOO without it, none when it is empty."""
    named = config.get("benchmarks", list(benchmark.DEFAULT))
    if not isinstance(named, list) or not all(
        isinstance(symbol, str) and symbol.strip() for symbol in named
    ):
        msg = f"{path}: 'benchmarks' must list symbols as Yahoo knows them, like [VT, VOO]"
        raise ConfigError(msg)
    return tuple(dict.fromkeys(symbol.strip().upper() for symbol in named))


def _frozen(config: dict[str, object], path: Path) -> tuple[networth.FrozenAccount, ...]:
    """`frozen: [<account>, {<owner>: <account>}, ...]`: the accounts, as the pages name them,
    whose money cannot be reached (a rent deposit); a bare name is every account so called,
    an owner's is theirs alone. The pensions are frozen without it."""
    named = config.get("frozen", [])
    msg = f"{path}: 'frozen' must list accounts as the pages name them, or as {{owner: account}}"
    if not isinstance(named, list):
        raise ConfigError(msg)
    found: list[networth.FrozenAccount] = []
    for entry in named:
        if isinstance(entry, dict) and len(entry) == 1:
            [(owner, account)] = entry.items()
        else:
            owner, account = None, entry  # a mapping of several is no account name either
        texts = [account] if owner is None else [owner, account]
        if not all(isinstance(text, str) and text.strip() for text in texts):
            raise ConfigError(msg)
        found.append(networth.FrozenAccount(account.strip(), owner.strip() if owner else None))
    return tuple(dict.fromkeys(found))  # each once, in the order named


def _unmatched(config: dict[str, object], path: Path) -> str:
    """`unmatched`: the category of the expenses and income no rule matches, for who tracks
    net worth alone; without it they go to review."""
    named = config.get("unmatched", "")
    if not isinstance(named, str):
        msg = f"{path}: 'unmatched' must be the name of a category"
        raise ConfigError(msg)
    return categories.key(named)


def _migrate_files(args: argparse.Namespace, out: TextIO) -> int:
    """Write the rules files, categories.yaml and the config again with today's names."""
    config = _config(args.config)
    owner = config.get("owner")
    people = [*_people(config, args.config), *([str(owner)] if owner else [])]
    found = migratefiles.files(_rules(args, config), dict.fromkeys(people), args.config)
    return migratefiles.run(found, out, apply=args.write)


def _setting(args: argparse.Namespace, config: dict[str, object], flag: str, key: str) -> str:
    value = getattr(args, flag) or config.get(key)
    if not value and not Path(args.config).exists():
        msg = (
            f"no {key}: there is no {args.config}: make a setup with 'scrooge init', "
            f"or pass --{flag}"
        )
        raise ConfigError(msg)
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
    reach = _reach(config, args.config)
    if reach.read_only and args.command not in _SHOWING:
        msg = (
            f"{args.config}: serve: read_only: this machine only shows a copy of the database, "
            f"so '{args.command}' is refused here: run it where the data is"
        )
        raise ConfigError(msg)
    owner = "" if args.command in _READ_BOTH else _setting(args, config, "owner", "owner")
    path = Path(_setting(args, config, "db", "database")).expanduser()
    origin = "--db" if args.db else f"'database' in {args.config}"
    people = _people(config, args.config)
    if reach.read_only and args.command != "serve":
        _check_copy(path)  # a copy is read where it is, never opened to write
        return _show(args, config, (path, owner, people), console, now)
    if args.command == "doctor":  # never migrates: whether it would is one of its answers
        _refuse_missing(path, "database", origin, _ONLY_INIT)
        return _doctor(path, args, config, out, now)
    if args.command == "serve":  # the pages open the database themselves, read-only
        if reach.read_only:
            _check_copy(path)
        else:
            _open(path, origin, now, out).close()
        # What the import page reads, as `scrooge import` would here; a copy has none.
        rules = _rules(args, config, present=not reach.read_only)
        labels = _labels(config, args.config)
        imports = importing.Setup(
            rules, _instruments(args, config), people, labels, _unmatched(config, args.config)
        )
        pages = _Pages(
            webapp.Rules(rules[0], people),
            imports,
            _currency(config, args.config),
            _currencies(config, args.config),
            _frozen(config, args.config),
            _benchmarks(config, args.config),
        )
        return _serve(path, args.port, pages, reach, out)
    conn = _open(path, origin, now, out)
    try:
        about_data: dict[str, Callable[[], int]] = {
            "publish": lambda: _publish(
                conn, path, args.folder or config.get("publish"), args.config, out
            ),
            "query": lambda: _query(conn, args, out),
            "keep": lambda: _keep(conn, path, _files(args.paths, path), out),
            "logo": lambda: _logo(conn, args, now, out),
        }
        if args.command in about_data:
            return about_data[args.command]()
        if args.command == "prices":
            return _prices(conn, args, config, console, now)
        labels = _labels(config, args.config)
        session = _Session(conn, path, owner, people, labels, console, now)
        return _for_owner(session, args, config)
    finally:
        conn.close()


def _show(
    args: argparse.Namespace,
    config: dict[str, object],
    whose: tuple[Path, str, People],
    console: _Console,
    now: datetime,
) -> int:
    """`query` or `statements` on a copy, through a connection that cannot change it."""
    path, owner, people = whose
    with db.reading(path) as conn:
        if args.command == "query":
            return _query(conn, args, console.out)
        labels = _labels(config, args.config)
        return _statements(_Session(conn, path, owner, people, labels, console, now))


def _open(path: Path, origin: str, now: datetime, out: TextIO) -> sqlite3.Connection:
    """The database, with its schema brought up to date. One that is not there is refused
    (`origin` says who named it): only `scrooge init` makes one.

    A database at an older schema is copied to `backups/` next to it first, and the copy is
    said. A migration that fails is refused with its reason: the migrations applied before it
    stay (each is one transaction), and the copy is dropped when none was.
    """
    _refuse_missing(path, "database", origin, _ONLY_INIT)
    conn = db.connect(path)
    try:
        found, latest = db.version(conn), db.latest()
        copy = staging.copy_before(conn, path, now) if 0 < found < latest else None
        try:
            db.migrate(conn, now)
        except sqlite3.Error as exc:
            reached = db.version(conn)
            if copy and reached == found:
                copy.unlink()
                copy = None
            kept = f"; copy from before: {copy}" if copy else ""
            msg = (
                f"{path}: schema version {latest} could not be reached ({exc}): the database "
                f"is at version {reached}{kept}"
            )
            raise MigrationError(msg) from None
    except BaseException:
        conn.close()
        raise
    if copy:
        out.write(
            f"{path}: schema version {found} brought up to {latest}. Copy from before: {copy}\n"
        )
    return conn


def _doctor(
    path: Path, args: argparse.Namespace, config: dict[str, object], out: TextIO, now: datetime
) -> int:
    """Every check of `doctor`, each with its problems; exit 1 if there is any."""
    setup = importing.Setup(
        _rules(args, config, present=False),  # a missing one is one of the problems
        _instruments(args, config),
        _people(config, args.config),
        _labels(config, args.config),
        _unmatched(config, args.config),
    )
    checks = doctor.examine(path, setup, now.date(), networth.CURRENCIES)
    for check in checks:
        out.write(f"{check.name}: {check.said}\n")
        out.writelines(f"  - {problem}\n" for problem in check.problems)
        if check.problems and check.advice:
            out.write(f"  {check.advice}\n")
    found = doctor.problems(checks)
    if not found:
        out.write(f"{path}: nothing wrong found.\n")
        return OK
    out.write(f"{path}: {found} problem{'' if found == 1 else 's'} found.\n")
    return FAILED


def _create(path: Path, now: datetime, out: TextIO) -> int:
    """`scrooge init --db`: an empty database at `path`, for a config written by hand."""
    try:
        db.create(path, now)
    except FileExistsError:
        msg = f"{path.absolute()}: there is a database there already"
        raise ConfigError(msg) from None
    out.write(f"{path.absolute()}: a new, empty database, at schema version {db.latest()}.\n")
    return OK


def _check_copy(path: Path) -> None:
    """A copy is shown as it was published or not at all: nothing here writes to it or next
    to it, so its schema must already be the one this code reads."""
    again = "publish it again, with the same version of scrooge on both machines"
    if not path.is_file():
        msg = f"{path}: no copy of the database to show: run 'scrooge publish' where the data is"
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


def _for_owner(session: _Session, args: argparse.Namespace, config: dict[str, object]) -> int:
    """The commands about one person's statements."""
    conn, owner, out = session.conn, session.owner, session.console.out
    if args.command == "import":
        return _import(session, args, config)
    if args.command == "commit":
        return _commit(session)
    if args.command == "statements":
        return _statements(session)
    if args.command == "uncommit":
        taken = staging.uncommit(conn, owner=owner, file_id=args.id)
        out.write(f"{taken.filename} is staged again for {owner}.\n")
        return OK
    if args.command == "discard":
        gone = staging.discard(conn, owner=owner, file_id=args.id)
        out.write(f"{gone.filename} and what it staged are removed; its original is kept.\n")
        return OK
    return _typed(session, args, config)


def _typed(session: _Session, args: argparse.Namespace, config: dict[str, object]) -> int:
    """`balance` and `position`: what an account held on a day, where no statement says it.

    A day that is not over yet is refused: what was held at its end is not known.
    """
    conn, owner, out = session.conn, session.owner, session.console.out
    figure = args.quantity if args.command == "position" else args.amount
    if args.remove:
        if figure is not None:
            msg = f"`{args.command} --remove` takes no figure: it removes the one typed"
            raise StagingError(msg)
        symbol = (
            _known_as(args.symbol.upper(), _instruments(args, config))
            if args.command == "position"
            else ""
        )
        staging.remove_typed(conn, owner=owner, account=args.account, day=args.day, symbol=symbol)
        what = f"{symbol} of " if symbol else ""
        out.write(f"Removed what was typed of {what}{args.account} of {owner} for {args.day}.\n")
        return OK
    if figure is None or args.currency is None:
        needed = "quantity" if args.command == "position" else "amount"
        msg = f"`{args.command}` needs the {needed} and the currency"
        raise StagingError(msg)
    currency = _currency_code(args.currency)
    today = session.now.date()
    if args.day > today:
        msg = f"{args.day} is after today ({today}): type what was held on a day that has begun"
        raise StagingError(msg)
    if args.command == "position":
        symbol = _known_as(args.symbol.upper(), _instruments(args, config))
        typed = TypedHolding(symbol, args.quantity, currency, args.tab)
        staging.type_holding(conn, owner=owner, account=args.account, day=args.day, holding=typed)
        out.write(f"{args.account} of {owner}: {args.quantity} {symbol} on {args.day}.\n")
        return OK
    staging.type_balance(
        conn,
        owner=owner,
        account=args.account,
        day=args.day,
        amount=args.amount,
        currency=currency,
    )
    out.write(f"{args.account} of {owner}: {args.amount} {currency} on {args.day}.\n")
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
            "run 'scrooge password' and put its line under 'serve'"
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
    currency: str  # what the figures are shown in until another is chosen
    currencies: tuple[str, ...]  # the columns of the month-end table
    frozen: tuple[networth.FrozenAccount, ...]  # the accounts whose money cannot be reached
    benchmarks: tuple[str, ...]  # the symbols of what the investments are compared with


def _serve(database: Path, port: int, pages: _Pages, reach: _Reach, out: TextIO) -> int:
    """Serve the pages until interrupted, where `serve` in the config says: there is no flag
    to listen anywhere else (D18)."""
    shown = f"[{reach.host}]" if ":" in reach.host else reach.host
    out.write(f"{database} is at http://{shown}:{port} (Ctrl-C to stop)\n")
    if reach.names:
        out.write(f"  for other devices, with the password: {', '.join(reach.names)}\n")
    if reach.read_only:
        out.write("  a copy, shown as 'scrooge publish' left it: nothing here changes it\n")
    app = webapp.create_app(
        database,
        rules=pages.rules,
        imports=pages.imports,
        names=reach.names,
        password=reach.password,
        read_only=reach.read_only,
        maturities=prices.TreasuryDirect(),
        currency=pages.currency,
        currencies=pages.currencies,
        frozen=pages.frozen,
        benchmarks=pages.benchmarks,
        spa=webapp.SPA,
    )
    uvicorn.run(app, host=reach.host, port=port, log_level="warning")
    return OK


def _publish(
    conn: sqlite3.Connection, database: Path, folder: object, config: Path, out: TextIO
) -> int:
    """Copy the database to `folder`, and say what went: all of it, staged rows included."""
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
    if done.staged_files:
        out.write(
            f"  with it: {done.staged} transactions of {done.staged_files} staged "
            f"statement{'' if done.staged_files == 1 else 's'}, which the pages do not show\n"
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
    out.write(f"Put this under 'serve' in scrooge.yaml:\n  password: {auth.digest(password)}\n")
    return OK


def _prices(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    config: dict[str, object],
    console: _Console,
    now: datetime,
) -> int:
    """Fetch what is missing; what could not be fetched is listed and nothing is guessed.

    Up to yesterday at most: today's close is not final while a market is open, and what is
    stored is never fetched again.
    """
    yesterday = now.date() - timedelta(days=1)
    typed = classification.load_instruments(_instruments(args, config))
    refreshed = marketdata.refresh(
        conn,
        console.source or prices.Yahoo(),
        through=min(args.through or yesterday, yesterday),
        now=now,
        symbols={symbol: entry.price_symbol for symbol, entry in typed.items()},
        currencies=networth.CURRENCIES,
        benchmarks=_benchmarks(config, args.config),
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
        _print_rows(result.rows, out, store.category_labels(conn), booking=args.booking)
    out.write(f"{len(result.rows)} transaction{'' if len(result.rows) == 1 else 's'}\n")
    _print_sums("Total", result.totals, out)
    for title, sums in (("By year", result.by_year), ("By month", result.by_month)):
        if len({item.period for item in sums}) > 1:
            _print_sums(title, sums, out)
    return OK


def _print_rows(
    rows: Sequence[query.Row], out: TextIO, label: categories.Labels, *, booking: bool
) -> None:
    """One line per transaction, in columns as wide as their longest value, each category by
    its label."""
    lines = [
        (
            (row.booking_date if booking else row.date).isoformat(),
            row.owner,
            row.account,
            f"{row.amount:,.2f}",
            row.currency,
            label(row.category),
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


def _import(session: _Session, args: argparse.Namespace, config: dict[str, object]) -> int:
    out = session.console.out
    shared = _rules(args, config)
    setup = importing.Setup(
        shared,
        _instruments(args, config),
        session.people,
        session.labels,
        _unmatched(config, args.config),
    )
    files = _files(args.paths, session.path)
    # The files of the zips among them last until they are staged and kept.
    with tempfile.TemporaryDirectory() as unpacked:
        found = importing.read(session.owner, files, setup, Path(unpacked))
        if args.review and found.month.uncategorized:
            everyone = [pattern for patterns in session.people.values() for pattern in patterns]
            personal = (setup.own(session.owner), everyone)
            ask = session.console.ask
            review.ask_categories(found.month.uncategorized, shared[0], out, ask, personal)
            found = importing.again(found, setup)
        found = importing.stage(session.conn, session.path, found, setup, session.now)
    staged = found.staged or staging.Staged((), 0, 0)
    for file in found.files:
        out.write(f"{_outcome(file)}\n")
    _print_certificates(found.certificates, session.owner, out)
    out.writelines(
        f"  note: {account} includes rows with no balance to check against\n"
        for account in found.month.unreconciled
    )
    out.write(f"{staged.transactions} transactions staged for {session.owner}.\n")
    if staged.later:
        out.write(f"  {staged.later} booked today or later wait for the next import\n")
    out.writelines(f"  missing: {gap}\n" for gap in staged.gaps)
    if staged.already:
        out.write(f"  {staged.already} rows are committed already: left as they are\n")
    out.writelines(f"  differs: {line}\n" for line in staged.differences)
    if staged.differences:
        out.write(
            "  nothing committed was changed: take the statement back (scrooge uncommit) "
            "to import it again\n"
        )
    if staged.uncategorized:
        out.write(
            f"{staged.uncategorized} transactions need a category: "
            "import again with --review, or add rules by hand.\n"
        )
    if found.refused:
        return FAILED
    if staged.uncategorized:
        return NEEDS_REVIEW
    if staged.files and not staged.gaps:
        out.write("Next: scrooge commit\n")
    return OK


def _outcome(file: importing.File) -> str:
    """One line per file: what it is and what became of it."""
    if file.problem:
        return f"  refused {file.name} ({file.label}): {file.problem}"
    if not file.recognised:
        return f"  skipped {file.name} (not a statement)"
    if file.nothing_new:
        return f"  {file.name} ({file.label}): committed already, nothing new"
    return f"  staged {file.name} ({file.label})"


def _statements(session: _Session) -> int:
    """The person's statements, those staged first, then the committed ones, oldest first."""
    owner_id = store.owner_id(session.conn, session.owner)
    files = [] if owner_id is None else store.statement_files(session.conn, owner_id)
    out = session.console.out
    for staged in (True, False):
        for file in (f for f in files if (f.committed_at is None) == staged):
            first, last = file.period
            state = "staged" if staged else "committed"
            out.write(
                f"{file.id:>5}  {state:<9}  {first} to {last}  "
                f"{router.LABELS.get(file.source, file.source)}: {file.filename}\n"
            )
    if not files:
        out.write(f"No statements for {session.owner}.\n")
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


def _logo(conn: sqlite3.Connection, args: argparse.Namespace, now: datetime, out: TextIO) -> int:
    """Keep the image `args.file` as the logo of `args.symbol`, or drop it with `--remove`."""
    if args.remove:
        logos.remove(conn, args.symbol)
        out.write(f"{args.symbol}: logo removed.\n")
        return OK
    if args.file is None:
        msg = "logo: name the file, or --remove"
        raise ConfigError(msg)
    try:
        image = args.file.read_bytes()
    except OSError as exc:
        msg = f"{args.file}: {exc.strerror}"
        raise ConfigError(msg) from None
    logo = logos.add(conn, args.symbol, image, args.source or args.file.name, now)
    out.write(f"{args.symbol}: {logo.media_type} logo kept ({len(image)} bytes).\n")
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


def _known_as(symbol: str, instruments_file: Path) -> str:
    """The symbol `instruments.yaml` knows an instrument by, for a symbol typed: the one whose
    `price_symbol` it is (NESN for NESN.SW), or the new name of a ticker renamed. So a
    position typed is the same instrument a statement books, not a second one."""
    instruments = classification.load_instruments(instruments_file)
    for name, entry in instruments.items():
        if entry.price_symbol.upper() == symbol:
            return name
    renamed = instruments.get(symbol)
    return renamed.ticker if renamed is not None and renamed.ticker else symbol


def _files(paths: Sequence[Path], database: Path) -> list[Path]:
    """The files named, and every file inside the folders named and their subfolders, each
    once. Hidden files and folders are left out (`.DS_Store`, `.git`), so is what a Mac adds
    to a zip, and so are the database, its originals and its copies."""
    files: list[Path] = []
    own = [
        database.resolve(),
        originals.folder_of(database).resolve(),
        (database.parent / staging.BACKUPS).resolve(),
    ]
    for path in paths:
        if path.is_dir():
            files += sorted(
                p
                for p in path.rglob("*")
                if p.is_file()
                and not _hidden(p.relative_to(path))
                and not any(p.resolve().is_relative_to(kept) for kept in own)
            )
        elif path.is_file():
            files.append(path)
        else:
            msg = f"{path}: no such file or folder"
            raise ConfigError(msg)
    unique: dict[Path, Path] = {}
    for file in files:
        unique.setdefault(file.resolve(), file)
    return list(unique.values())


def _hidden(path: Path) -> bool:
    return any(part.startswith(".") or part == _MAC_ZIP for part in path.parts)


_MAC_ZIP = "__MACOSX"  # the folder of resource forks a zip made on a Mac holds


def _commit(session: _Session) -> int:
    """Commit after saving a copy of the database; the copy is dropped if nothing changed."""
    path, owner, now, out = session.path, session.owner, session.now, session.console.out
    done, copy = staging.commit_with_copy(session.conn, path, owner=owner, now=now)
    if not done:
        out.write(f"Nothing is staged for {owner}.\n")
        return OK
    for file in done:
        first, last = file.period
        out.write(f"  committed {file.filename} ({first} to {last})\n")
    many = f"{len(done)} statement{'' if len(done) == 1 else 's'}"
    out.write(f"{many} committed for {owner}. Copy from before: {copy}\n")
    return OK
