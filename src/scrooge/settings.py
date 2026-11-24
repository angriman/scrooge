"""The rules and the categories as the settings page of the app shows and changes them.

YAML stays where they are kept: `rules.yaml` and each person's `rules.<owner>.yaml`, which
every import reads again, and `categories.yaml`, which adds to the built-in taxonomy. A rule
is changed in its file in place (`rulesfile`); the next import classifies with it, as nothing
keeps the rules in memory. A category renamed or merged changes the rows that carry it too, in
one database transaction, after the copy of the database that a commit also makes; when
anything fails, the database and every file are left as they were.

How many committed rows a rule matches is counted with its pattern, account and direction
alone, whatever rule before it takes them first: what it would match on its own.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from scrooge import categories, classification, query, rulesfile, staging
from scrooge.classification import Rule
from scrooge.errors import ConfigError
from scrooge.models import Transaction
from scrooge.storage import db, store, taxonomy

SHOWN = 50  # rows a test of a pattern lists; how many match is counted all the same
DIRECTIONS = ("", "in", "out")


class SettingsError(Exception):
    """What was asked cannot be done; the message says why, as a sentence for the page."""


@dataclass(frozen=True, slots=True)
class RulesFile:
    """A file of rules, as the page names it."""

    key: str  # its name: what a change sent names it by
    path: Path
    owner: str | None  # whose own rules; None for those everybody shares


@dataclass(frozen=True, slots=True)
class ListedRule:
    file: RulesFile
    position: int  # from 1, in its file
    match: str
    category: str
    direction: str
    account: str
    matches: int | None  # committed rows it matches; None when its pattern is no pattern


@dataclass(frozen=True, slots=True)
class ListedCategory:
    name: str
    side: str  # uscite | entrate | transfer
    parent: str | None  # the top-level category of an expense
    built_in: bool
    rows: int  # committed rows that carry it


def rule_files(shared: Sequence[Path], people: Iterable[str], owner: str | None) -> list[RulesFile]:
    """The files of rules in the order an import of `owner` tries them: their own, then the
    shared ones. Without an owner every person's own file comes first, then the shared ones."""
    listed = [
        RulesFile(path.name, path, person)
        for person in people
        if owner in (None, person)
        for path in [classification.personal_file(shared[0], person)]
    ]
    for path in shared:
        named = {kept.key for kept in listed}
        listed.append(RulesFile(path.name if path.name not in named else str(path), path, None))
    return listed


def _transaction(row: query.Row) -> Transaction:
    """A committed row as a rule reads it."""
    return Transaction(
        date=row.date,
        account=row.account,
        amount=row.amount,
        currency=row.currency,
        description=row.description,
        kind=row.kind,
        source_file="",
        source_ref=row.id,
        counterparty=row.counterparty,
    )


def _texts(rows: Iterable[query.Row]) -> Counter[tuple[str, Transaction]]:
    """Owner and what a rule reads of a row -> how many rows read the same: shops come back,
    so each text is matched once."""
    return Counter(
        (
            row.owner,
            Transaction(
                date=row.date.min,
                account=row.account,
                amount=Decimal(1 if row.amount > 0 else -1),
                currency="",
                description=row.description,
                kind=row.kind,
                source_file="",
                source_ref="",
                counterparty=row.counterparty,
            ),
        )
        for row in rows
    )


def _compiled(match: str) -> re.Pattern[str]:
    try:
        return re.compile(match, re.IGNORECASE)
    except re.error as exc:
        msg = f"{match!r} is no pattern: {exc}"
        raise SettingsError(msg) from None


def rules(
    conn: sqlite3.Connection, files: Sequence[RulesFile], owner: str | None = None
) -> tuple[list[ListedRule], list[str]]:
    """Every rule of `files`, in the order they are tried, with the committed rows of `owner`
    (of everyone without one) each one matches; and what could not be read, a sentence for
    each file."""
    texts = _texts(query.run(conn, query.Filter(owner=owner)).rows)
    listed: list[ListedRule] = []
    problems: list[str] = []
    for file in files:
        try:
            _, found = rulesfile.read(file.path)
        except ConfigError as exc:
            problems.append(str(exc))
            continue
        for position, entry in enumerate(found, start=1):
            try:
                pattern = _compiled(entry.match)
                rule = Rule(pattern, entry.category, None, entry.account, entry.direction)
                matches: int | None = sum(
                    count
                    for (owner, txn), count in texts.items()
                    if file.owner in (None, owner) and rule.matches(txn)
                )
            except SettingsError:
                matches = None
            listed.append(
                ListedRule(
                    file,
                    position,
                    entry.match,
                    entry.category,
                    entry.direction,
                    entry.account,
                    matches,
                )
            )
    return listed, problems


def try_pattern(
    conn: sqlite3.Connection, match: str, *, direction: str = "", owner: str | None = None
) -> tuple[int, list[query.Row]]:
    """How many committed rows of `owner` (of everyone without one) the pattern `match`
    matches in `direction`, and the newest `SHOWN` of them. Nothing is saved."""
    if not match.strip():
        msg = "write a pattern to look for"
        raise SettingsError(msg)
    if direction not in DIRECTIONS:
        msg = f"the direction is 'in' or 'out', not {direction!r}"
        raise SettingsError(msg)
    rule = Rule(_compiled(match), "", direction=direction)
    rows = query.run(conn, query.Filter(owner=owner)).rows
    found = [row for row in reversed(rows) if rule.matches(_transaction(row))]
    return len(found), found[:SHOWN]


@contextmanager
def _kept(paths: Iterable[Path]) -> Iterator[None]:
    """Whatever raises inside, `paths` are put back as they were (gone, if they were)."""
    before = {path: path.read_text(encoding="utf-8") if path.exists() else None for path in paths}
    try:
        yield
    except BaseException:
        for path, text in before.items():
            if text is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(text, encoding="utf-8")
        raise


def _known(categories_file: Path | None) -> tuple[str, ...]:
    extras = categories.load(categories_file) if categories_file else ()
    return categories.names(extras)


def _write_rules(path: Path, text: str, known: Sequence[str]) -> None:
    """Write `text` to the rules file `path`, unless it would not load as it is."""
    with _kept([path]):
        path.write_text(text, encoding="utf-8")
        try:
            classification.load_rules([path], known)
        except ConfigError as exc:
            msg = f"the rule was not saved: {exc}"
            raise SettingsError(msg) from None


@dataclass(frozen=True, slots=True)
class Seen:
    """A rule as the page showed it: where, and what it said then."""

    file: Path
    position: int
    match: str
    category: str


def _found(seen: Seen) -> tuple[str, rulesfile.Entry]:
    try:
        return rulesfile.find(seen.file, seen.position, seen.match, seen.category)
    except ConfigError as exc:
        raise SettingsError(str(exc)) from None


def edit_rule(
    seen: Seen, *, match: str, category: str, direction: str, categories_file: Path | None
) -> None:
    """Give the rule `seen` another pattern, category or direction ("" for both)."""
    known = _known(categories_file)
    if not match.strip():
        msg = "a rule needs a pattern"
        raise SettingsError(msg)
    _compiled(match)
    if category not in known:
        msg = f"there is no category {category!r}"
        raise SettingsError(msg)
    if direction not in DIRECTIONS:
        msg = f"the direction is 'in' or 'out', not {direction!r}"
        raise SettingsError(msg)
    text, entry = _found(seen)
    changes = {"match": match, "category": category, "direction": direction}
    _write_rules(seen.file, rulesfile.rewritten(text, entry, changes), known)


def remove_rule(seen: Seen) -> None:
    """Take the rule `seen` out of its file."""
    text, entry = _found(seen)
    seen.file.write_text(rulesfile.removed(text, entry), encoding="utf-8")


def move_rule(seen: Seen, by: int) -> None:
    """Move the rule `seen` one place up (`by` -1) or down (1) in its file: of two rules that
    both match a row, the one tried first gives the category."""
    if by not in {-1, 1}:
        msg = "a rule moves one place up or down"
        raise SettingsError(msg)
    text, entry = _found(seen)
    _, found = rulesfile.read(seen.file)
    other = seen.position - 1 + by
    if not 0 <= other < len(found):
        msg = f"rule {seen.position} is the {'first' if by < 0 else 'last'} of {seen.file.name}"
        raise SettingsError(msg)
    upper, lower = (found[other], entry) if by < 0 else (entry, found[other])
    seen.file.write_text(rulesfile.swapped(text, upper, lower), encoding="utf-8")


def listed_categories(
    conn: sqlite3.Connection, categories_file: Path | None
) -> list[ListedCategory]:
    """The taxonomy, then what `categories.yaml` adds, with the committed rows of each."""
    extras = categories.load(categories_file) if categories_file else ()
    rows = Counter((r.category, r.category_parent) for r in query.run(conn, query.Filter()).rows)
    built: list[tuple[str, str, str | None, bool]] = [
        *(
            (leaf, "uscite", group, True)
            for group, leaves in categories.USCITE.items()
            for leaf in leaves
        ),
        *((e.name, "uscite", e.group, False) for e in extras if e.side == "uscite"),
        *((name, "entrate", None, True) for name in categories.ENTRATE),
        *((e.name, "entrate", None, False) for e in extras if e.side == "entrate"),
        *((name, "transfer", None, True) for name in categories.TRANSFERS),
    ]
    return [
        ListedCategory(name, side, parent, built_in, rows[name, parent])
        for name, side, parent, built_in in dict.fromkeys(built)
    ]


@dataclass(frozen=True, slots=True)
class Places:
    """Where a change of the categories is written: the database and the YAML files."""

    database: Path
    categories_file: Path
    rule_files: Sequence[Path]


def _added(places: Places, name: str) -> categories.Extra:
    """The added category `name`; the taxonomy's own cannot be changed in place."""
    extras = categories.load(places.categories_file)
    if name in categories.ALL:
        msg = f"{name} is built in: it cannot be renamed or removed, only merged into another"
        raise SettingsError(msg)
    found = next((extra for extra in extras if extra.name == name), None)
    if found is None:
        msg = f"there is no category {name!r}"
        raise SettingsError(msg)
    return found


def _recategorize(places: Places, old: str, new: str) -> int:
    """Have every rule that gives `old` give `new`. Returns how many there were."""
    changed = 0
    for path in places.rule_files:
        try:
            text, found = rulesfile.read(path)
        except ConfigError as exc:
            msg = f"the rules could not be changed: {exc}"
            raise SettingsError(msg) from None
        text, count = rulesfile.recategorized(text, found, old, new)
        if count:
            path.write_text(text, encoding="utf-8")
            changed += count
    return changed


def _with_copy(
    conn: sqlite3.Connection, places: Places, now: datetime, change: Callable[[], int]
) -> int:
    """Make `change` in one transaction, after a copy of the database (dropped when it fails),
    and with every file put back when anything raises."""
    copy = staging.copy_before(conn, places.database, now)
    try:
        with _kept([places.categories_file, *places.rule_files]), db.transaction(conn):
            store.seed_categories(conn, categories.load(places.categories_file))
            return change()
    except BaseException:
        copy.unlink()
        raise


def _fresh_name(places: Places, name: str) -> str:
    name = " ".join(name.split())
    if not name:
        msg = "a category needs a name"
        raise SettingsError(msg)
    if name in _known(places.categories_file):
        msg = f"there is already a category {name!r}: merge into it instead"
        raise SettingsError(msg)
    return name


def rename_category(
    conn: sqlite3.Connection, places: Places, *, name: str, to: str, now: datetime
) -> int:
    """Call the added category `name` `to`, in `categories.yaml`, in every rule that gives it
    and on every row that carries it. Returns how many rows do."""
    _added(places, name)
    new = _fresh_name(places, to)

    def act() -> int:
        rows = taxonomy.rename(conn, name, new)
        categories.rename(places.categories_file, name, new)
        _recategorize(places, name, new)
        return rows

    return _with_copy(conn, places, now, act)


def merge_category(
    conn: sqlite3.Connection, places: Places, *, name: str, into: str, now: datetime
) -> int:
    """Give the rows and the rules of `name` the category `into`, and take `name` out when it
    was added (one of the taxonomy stays, with nothing in it). Returns how many rows moved."""
    known = _known(places.categories_file)
    for each in (name, into):
        if each not in known:
            msg = f"there is no category {each!r}"
            raise SettingsError(msg)
    if name == into:
        msg = "a category is merged into another one"
        raise SettingsError(msg)
    if (name in categories.TRANSFERS) != (into in categories.TRANSFERS):
        msg = f"{name} and {into} cannot be merged: one is money between accounts, one is not"
        raise SettingsError(msg)
    added = name not in categories.ALL

    def act() -> int:
        moved = taxonomy.merge(conn, name, into)
        _recategorize(places, name, into)
        if added:
            taxonomy.drop(conn, name)
            categories.drop(places.categories_file, name)
        return moved

    return _with_copy(conn, places, now, act)


def remove_category(conn: sqlite3.Connection, places: Places, *, name: str) -> None:
    """Take out the added category `name`, which no row and no rule gives."""
    _added(places, name)
    rules_giving = sum(
        1
        for path in places.rule_files
        for entry in rulesfile.read(path)[1]
        if entry.category == name
    )
    with _kept([places.categories_file]), db.transaction(conn):
        rows = taxonomy.carried(conn, name)
        if rows:
            msg = f"{rows} rows carry {name}: merge it into another category instead"
            raise SettingsError(msg)
        if rules_giving:
            msg = f"{rules_giving} rules give {name}: change them first, or merge it into another"
            raise SettingsError(msg)
        taxonomy.drop(conn, name)
        categories.drop(places.categories_file, name)
