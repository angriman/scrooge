"""The rules and the categories as the settings page of the app shows and changes them.

YAML stays where they are kept: `rules.yaml` and each person's `rules.<owner>.yaml`, which
every import reads again, and `categories.yaml`, which adds to the built-in taxonomy. A rule
is changed in its file in place (`rulesfile`); the next import classifies with it, as nothing
keeps the rules in memory. A rules file written here is read back as an import reads it, and
put back as it was when it would not load.

A category renamed, merged or moved changes the rows that carry it too, in one database
transaction, after the copy of the database that a commit also makes. When anything fails, the
database and every file that changed are put back as they were, byte for byte; when a file
cannot be, the others still are, the copy is kept and the error says where. Every change holds
the lock of the writers of these files (`yamlfiles`) from its first read to its last write,
and each file is written whole or not at all.

Patterns are run on the committed rows in a process of their own (`patterns`), killed when its
time is up: a pattern that would take for ever never holds the server. How many committed rows
a rule matches is counted with its pattern, account and direction alone, whatever rule before
it takes them first: what it would match on its own.

A category is asked for by its key or its label (`categories.key`), and shown and written into
the files by its label (`categories.Labels`).
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from scrooge import categories, classification, patterns, query, rulesfile, staging, yamlfiles
from scrooge.errors import ConfigError
from scrooge.storage import db, store, taxonomy

SHOWN = 50  # rows a test of a pattern lists; how many match is counted all the same
LONGEST = 200  # characters of a pattern tried or saved here
BUDGET = 2.0  # seconds a pattern is tried for, newest rows first
COUNTING = 5.0  # seconds the rules of the page are counted for, in their order
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
    category: str  # its key
    direction: str
    account: str
    # Committed rows it would match on its own; None when its pattern is none, or when time
    # ran out before it was counted (`counted`).
    matches: int | None
    version: str  # what a change of it sends back (`rulesfile.Entry.version`)
    fixed: str  # why it cannot be changed here at all; "" when it can
    counted: bool = True


@dataclass(frozen=True, slots=True)
class ListedCategory:
    key: str
    name: str  # its label
    side: str  # expenses | income | transfer
    parent: str | None  # the label of the top-level category of an expense
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


def _text(row: query.Row) -> patterns.Text:
    """What a rule reads of a committed row."""
    return (row.account, row.amount > 0, row.description, row.counterparty)


def _compiled(match: str) -> re.Pattern[str]:
    if not match.strip():
        msg = "a rule needs a pattern"
        raise SettingsError(msg)
    if len(match) > LONGEST:
        msg = f"a pattern is at most {LONGEST} characters long"
        raise SettingsError(msg)
    try:
        return re.compile(match, re.IGNORECASE)
    except re.error as exc:
        msg = f"{match!r} is no pattern: {exc}"
        raise SettingsError(msg) from None


def _direction(direction: str) -> str:
    if direction not in DIRECTIONS:
        msg = f"the direction is 'in' or 'out', not {direction!r}"
        raise SettingsError(msg)
    return direction


def rules(
    conn: sqlite3.Connection,
    files: Sequence[RulesFile],
    owner: str | None = None,
    budget: float = COUNTING,
) -> tuple[list[ListedRule], list[str]]:
    """Every rule of `files`, in the order they are tried, with the committed rows of `owner`
    (of everyone without one) each one would match on its own; and what could not be read, a
    sentence for each file. The rules are counted in their order, away from the server, for
    `budget` seconds at most: those time did not reach are listed uncounted, and a sentence
    says so."""
    texts = Counter(
        (row.owner, _text(row)) for row in query.run(conn, query.Filter(owner=owner)).rows
    )
    found: list[tuple[RulesFile, int, rulesfile.Entry]] = []
    problems: list[str] = []
    for file in files:
        try:
            _, entries = rulesfile.read(file.path)
        except ConfigError as exc:
            problems.append(str(exc))
            continue
        found.extend((file, position, entry) for position, entry in enumerate(entries, start=1))
    specs = [(entry.match, entry.account, entry.direction, file.owner) for file, _, entry in found]
    counts: dict[int, int | None] = {}
    if specs:
        rows = [(whose, text, count) for (whose, text), count in texts.items()]
        got, complete = patterns.bounded(patterns.counting, (specs, rows), budget)
        counts = dict(got)  # type: ignore[arg-type]  # (index, rows) pairs, as `counting` puts them
        if not complete:
            problems.append(
                f"counting the rules took too long: {len(found) - len(counts)} of them, from "
                "the first left out, say no number of rows"
            )
    listed = [
        ListedRule(
            file,
            position,
            entry.match,
            entry.category,
            entry.direction,
            entry.account,
            counts.get(n),
            entry.version,
            entry.refusal(editing=False),
            n in counts,
        )
        for n, (file, position, entry) in enumerate(found)
    ]
    return listed, problems


@dataclass(frozen=True, slots=True)
class Tried:
    """What a pattern matches among the committed rows."""

    count: int
    rows: list[query.Row]  # the newest `SHOWN` of them
    tried: int  # rows it was tried on, newest first
    complete: bool  # False when time ran out before the oldest


def try_pattern(
    conn: sqlite3.Connection,
    match: str,
    *,
    direction: str = "",
    owner: str | None = None,
    budget: float = BUDGET,
) -> Tried:
    """The committed rows of `owner` (of everyone without one) that the pattern `match`
    matches in `direction`, newest first. Nothing is saved. The pattern runs away from the
    server for `budget` seconds at most; when time is up the newest rows it decided are
    those counted (`Tried.tried`)."""
    _compiled(match)
    _direction(direction)
    rows = query.run(conn, query.Filter(owner=owner)).rows[::-1]
    order: dict[patterns.Text, int] = {}  # each text once, in the order of its newest row
    for row in rows:
        order.setdefault(_text(row), len(order))
    got, _ = patterns.bounded(patterns.matching, (match, direction, list(order)), budget)
    batches: list[tuple[int, list[int]]] = got  # type: ignore[assignment]  # as `matching` puts them
    decided = max((upto for upto, _ in batches), default=0)
    matched = {n for _, found in batches for n in found}
    reached = next((n for n, row in enumerate(rows) if order[_text(row)] >= decided), len(rows))
    hits = [row for row in rows[:reached] if order[_text(row)] in matched]
    return Tried(len(hits), hits[:SHOWN], reached, reached == len(rows))


class _UnrestoredError(SettingsError):
    """A change failed, and a file it had written could not be put back."""


@contextmanager
def _kept(paths: Iterable[Path]) -> Iterator[None]:
    """Whatever raises inside, those of `paths` that changed are put back as they were, byte
    for byte (gone, if they were). When one cannot be, the others still are, and
    `_UnrestoredError` says which, after what went wrong first. The caller holds the lock."""
    before = {path: yamlfiles.snapshot(path) for path in paths}
    try:
        yield
    except BaseException as exc:
        failed = yamlfiles.put_back(before)
        if failed:
            msg = f"{exc}; and {', '.join(failed)} could not be put back as it was"
            raise _UnrestoredError(msg) from exc
        raise


def _extras(categories_file: Path | None) -> tuple[categories.Extra, ...]:
    return categories.load(categories_file) if categories_file else ()


def _known(categories_file: Path | None) -> tuple[str, ...]:
    """The key of every category a rule may give."""
    return categories.names(_extras(categories_file))


def _write_rules(path: Path, text: str, known: Sequence[str]) -> None:
    """Write `text` to the rules file `path`, unless it would not load as an import reads it."""
    with _kept([path]):
        yamlfiles.write(path, text)
        try:
            classification.load_rules([path], known)
        except ConfigError as exc:
            msg = f"the rules file was left as it was: {exc}"
            raise SettingsError(msg) from None


@dataclass(frozen=True, slots=True)
class Seen:
    """A rule as the page showed it: where, and the digest of its lines then."""

    file: Path
    position: int
    version: str


Change = Callable[[str, tuple[rulesfile.Entry, ...], rulesfile.Entry], str]


def _change_rule(
    seen: Seen, categories_file: Path | None, change: Change, *, editing: bool = False
) -> None:
    """Make `change` of the text of the rule `seen`'s file, read once, under the lock."""
    with yamlfiles.LOCK:
        try:
            text, found, entry = rulesfile.find(seen.file, seen.position, seen.version)
        except ConfigError as exc:
            raise SettingsError(str(exc)) from None
        refusal = entry.refusal(editing=editing)
        if refusal:
            msg = f"rule {seen.position} of {seen.file.name} cannot be changed here: {refusal}"
            raise SettingsError(msg)
        _write_rules(seen.file, change(text, found, entry), _known(categories_file))


def edit_rule(
    seen: Seen, *, match: str, category: str, direction: str, categories_file: Path | None
) -> None:
    """Give the rule `seen` another pattern, category or direction ("" for both)."""
    _compiled(match)
    _direction(direction)
    if categories.key(category) not in _known(categories_file):
        msg = f"there is no category {category!r}"
        raise SettingsError(msg)
    written = categories.Labels.of(_extras(categories_file)).written(categories.key(category))
    changes = {"match": match, "category": written, "direction": direction}
    _change_rule(
        seen,
        categories_file,
        lambda text, _, entry: rulesfile.rewritten(text, entry, changes),
        editing=True,
    )


def remove_rule(seen: Seen, categories_file: Path | None) -> None:
    """Take the rule `seen` out of its file."""
    _change_rule(seen, categories_file, lambda text, _, entry: rulesfile.removed(text, entry))


def move_rule(seen: Seen, by: int, beside: str, categories_file: Path | None) -> None:
    """Move the rule `seen` one place up (`by` -1) or down (1) in its file, past the rule
    there, which must still be as the page showed it too (`beside`, its digest): of two rules
    that both match a row, the one tried first gives the category. A rule stays in its file."""
    if by not in {-1, 1}:
        msg = "a rule moves one place up or down"
        raise SettingsError(msg)

    def swap(text: str, found: tuple[rulesfile.Entry, ...], entry: rulesfile.Entry) -> str:
        at = seen.position - 1 + by
        if not 0 <= at < len(found):
            where = "first" if by < 0 else "last"
            msg = f"rule {seen.position} is the {where} of {seen.file.name}"
            raise SettingsError(msg)
        if found[at].version != beside:
            msg = (
                f"rule {at + 1} of {seen.file.name} changed since the page was shown: load it again"
            )
            raise SettingsError(msg)
        if found[at].refusal(editing=False):
            msg = f"rule {at + 1} of {seen.file.name}, next to it, cannot be moved here"
            raise SettingsError(msg)
        upper, lower = (found[at], entry) if by < 0 else (entry, found[at])
        return rulesfile.swapped(text, upper, lower)

    _change_rule(seen, categories_file, swap)


def _taxonomy(extras: Sequence[categories.Extra]) -> list[tuple[str, str, str | None, bool]]:
    """Key, side, the key of its parent and whether it is built in, of every leaf category."""
    expenses = categories.EXPENSE_GROUPS.items()
    built = [
        *((leaf, "expenses", group, True) for group, leaves in expenses for leaf in leaves),
        *((e.key, "expenses", e.group_key, False) for e in extras if e.side == "expenses"),
        *((name, "income", None, True) for name in categories.INCOME_LEAVES),
        *((e.key, "income", None, False) for e in extras if e.side == "income"),
        *((name, "transfer", None, True) for name in categories.TRANSFERS),
    ]
    return list(dict.fromkeys(built))


def listed_categories(
    conn: sqlite3.Connection, categories_file: Path | None
) -> list[ListedCategory]:
    """The taxonomy, then what `categories.yaml` adds, with the committed rows of each."""
    extras = _extras(categories_file)
    label = categories.Labels.of(extras)
    rows = Counter((r.category, r.category_parent) for r in query.run(conn, query.Filter()).rows)
    return [
        ListedCategory(
            name, label(name), side, parent and label(parent), built_in, rows[name, parent]
        )
        for name, side, parent, built_in in _taxonomy(extras)
    ]


@dataclass(frozen=True, slots=True)
class Places:
    """Where a change of the categories is written: the database and the YAML files."""

    database: Path
    categories_file: Path
    rule_files: Sequence[Path]
    unmatched: str = ""  # the category the config gives rows no rule matches


def _label(places: Places) -> categories.Labels:
    return categories.Labels.of(categories.load(places.categories_file))


def _added(places: Places, name: str) -> categories.Extra:
    """The added category `name` (its key or its name); the taxonomy's own cannot be changed in
    place."""
    extras = categories.load(places.categories_file)
    wanted = categories.key(name)
    if wanted in categories.ALL:
        shown = _label(places)(wanted)
        msg = f"{shown} is built in: it cannot be renamed, moved or removed, only merged"
        raise SettingsError(msg)
    found = next((extra for extra in extras if extra.key == wanted), None)
    if found is None:
        msg = f"there is no category {name!r}"
        raise SettingsError(msg)
    return found


def _not_unmatched(places: Places, name: str) -> None:
    """Refuse to take away the category the config gives what no rule matches: every import
    would then fail."""
    if name == categories.key(places.unmatched):
        msg = (
            f"{_label(places)(name)} is what scrooge.yaml gives rows no rule matches "
            "('unmatched'): change it there first"
        )
        raise SettingsError(msg)


def _recategorize(places: Places, old: str, new: str) -> int:
    """Have every rule that gives the key `old` give `new`, as written. Returns how many there
    were."""
    changed = 0
    for path in places.rule_files:
        try:
            text, found = rulesfile.read(path)
        except ConfigError as exc:
            msg = f"the rules could not be changed: {exc}"
            raise SettingsError(msg) from None
        text, count = rulesfile.recategorized(text, found, old, new)
        if count:
            yamlfiles.write(path, text)
            changed += count
    return changed


def _with_copy(
    conn: sqlite3.Connection, places: Places, now: datetime, change: Callable[[], int]
) -> int:
    """Make `change` in one transaction, after a copy of the database, with every file put
    back when anything raises and the lock held throughout. The copy is dropped when the change
    fails and everything was put back; it is kept, and named, when a file could not be. After a
    change that succeeded the older copies beyond the newest `staging.COPIES_KEPT` are removed."""
    with yamlfiles.LOCK:
        copy = staging.copy_before(conn, places.database, now)
        try:
            with _kept([places.categories_file, *places.rule_files]), db.transaction(conn):
                store.seed_categories(conn, categories.load(places.categories_file))
                changed = change()
        except _UnrestoredError as exc:
            msg = f"{exc}; the database as it was before is kept in {copy}"
            raise SettingsError(msg) from exc
        except BaseException:
            copy.unlink()
            raise
        staging.prune_copies(places.database, keep=copy)
        return changed


def _fresh_name(places: Places, name: str) -> str:
    name = " ".join(name.split())
    if not categories.key(name):
        msg = "a category needs a name, with a letter or a digit"
        raise SettingsError(msg)
    if categories.key(name) in _known(places.categories_file):
        shown = _label(places)(categories.key(name))
        msg = f"there is already a category {shown!r}: merge into it instead"
        raise SettingsError(msg)
    return name


def rename_category(
    conn: sqlite3.Connection, places: Places, *, name: str, to: str, now: datetime
) -> int:
    """Call the added category `name` `to`, in `categories.yaml`, in every rule that gives it
    and on every row that carries it: its key becomes that of `to`. Returns how many rows do."""
    with yamlfiles.LOCK:
        name = _added(places, name).key
        _not_unmatched(places, name)
        new = _fresh_name(places, to)

        def act() -> int:
            rows = taxonomy.rename(conn, name, new)
            categories.rename(places.categories_file, name, new)
            _recategorize(places, name, new)
            return rows

        return _with_copy(conn, places, now, act)


def _sides(places: Places, name: str) -> set[str]:
    """The sides the category of key `name` is on."""
    extras = categories.load(places.categories_file)
    return {side for each, side, _, _ in _taxonomy(extras) if each == name}


def merge_category(
    conn: sqlite3.Connection, places: Places, *, name: str, into: str, now: datetime
) -> int:
    """Give the rows and the rules of `name` the category `into`, and take `name` out when it
    was added (one of the taxonomy stays, with nothing in it). `into` is on every side `name`
    is on: income is not merged into an expense, nor money between accounts into either.
    Returns how many rows moved."""
    with yamlfiles.LOCK:
        asked = {name: categories.key(name), into: categories.key(into)}
        source, target = _sides(places, asked[name]), _sides(places, asked[into])
        for each, sides in ((name, source), (into, target)):
            if not sides:
                msg = f"there is no category {each!r}"
                raise SettingsError(msg)
        label = _label(places)
        name, into = asked[name], asked[into]
        if name == into:
            msg = "a category is merged into another one"
            raise SettingsError(msg)
        if not source <= target:
            words = {"expenses": "an expense", "income": "income", "transfer": "a transfer"}
            missing = ", ".join(words[side] for side in sorted(source - target))
            msg = f"{label(name)} is {missing} and {label(into)} is not: they cannot be merged"
            raise SettingsError(msg)
        _not_unmatched(places, name)
        added = name not in categories.ALL

        def act() -> int:
            moved = taxonomy.merge(conn, name, into)
            _recategorize(places, name, label.written(into))
            if added:
                taxonomy.drop(conn, name)
                categories.drop(places.categories_file, name)
            return moved

        return _with_copy(conn, places, now, act)


def move_category(
    conn: sqlite3.Connection, places: Places, *, name: str, under: str, now: datetime
) -> int:
    """Put the added expense category `name` under the top-level category `under`, one there
    is or a new one. Its rows and rules keep it. Returns how many rows carry it."""
    with yamlfiles.LOCK:
        extra = _added(places, name)
        name = extra.key
        if extra.side != "expenses":
            msg = f"{extra.name} is income: only an expense sits under a top-level category"
            raise SettingsError(msg)
        group = " ".join(under.split())
        if not categories.key(group):
            msg = "say which top-level category it goes under"
            raise SettingsError(msg)
        if categories.key(group) == extra.group_key:
            msg = f"{extra.name} is under {_label(places)(extra.group_key)} already"
            raise SettingsError(msg)
        if categories.key(group) != name and categories.key(group) in _known(
            places.categories_file
        ):
            msg = f"{group} is a category of its own, not one that others go under"
            raise SettingsError(msg)
        built = categories.key(group) in categories.GROUPS
        group = _label(places)(categories.key(group)) if built else group

        def act() -> int:
            rows = taxonomy.move(conn, name, group)
            categories.move(places.categories_file, name, group)
            return rows

        return _with_copy(conn, places, now, act)


def remove_category(conn: sqlite3.Connection, places: Places, *, name: str) -> None:
    """Take out the added category `name`, which no row and no rule gives."""
    with yamlfiles.LOCK:
        name = _added(places, name).key
        _not_unmatched(places, name)
        rules_giving = sum(
            1
            for path in places.rule_files
            for entry in rulesfile.read(path)[1]
            if entry.category == name
        )
        with _kept([places.categories_file]), db.transaction(conn):
            rows = taxonomy.carried(conn, name)
            shown = _label(places)(name)
            if rows:
                msg = f"{rows} rows carry {shown}: merge it into another category instead"
                raise SettingsError(msg)
            if rules_giving:
                msg = f"{rules_giving} rules give {shown}: change them first, or merge it"
                raise SettingsError(msg)
            taxonomy.drop(conn, name)
            categories.drop(places.categories_file, name)
