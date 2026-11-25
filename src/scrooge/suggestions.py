"""A rule offered for a category changed by hand (plan decision 56).

A row that had a category and is given another on its own keeps that choice for itself alone
(`staging.categorize`, an override). The next import would give the same merchant's rows the
old category again, so the import page offers a rule: it looks for the row's merchant as the
cash flow names it (`query.merchant_words`, `classification.merchant_match`: its words in
order, whatever digits or signs between them, up to a comma), for money going the row's way,
and gives the category chosen. It is only offered: nothing is written until it is added.

Where it goes keeps the first match winning: first in the owner's own rules when the row
names a person (as `--review` and the import page save such a choice) or when a rule there
is what gives it its category now; otherwise first in the first shared file. So once added,
it is the rule that gives the row its category.

None is offered when the rules as they are already give the row the category it has (the
first that matches, or the category of the rows none matches), when the row has no letters
to name a merchant by, or when it would make money to or from the other person a transfer,
which no rule may do (D14). All of it is read from the files and the database, offline (D9).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from scrooge import categories, classification, query, yamlfiles
from scrooge.classification import Patterns, Rule
from scrooge.models import Kind, Transaction
from scrooge.storage import store
from scrooge.storage.money import from_nano

_FLOWS = frozenset({Kind.INCOME.value, Kind.EXPENSE.value})


class SuggestionError(Exception):
    """A rule asked for is not one the page would offer now."""


@dataclass(frozen=True, slots=True)
class Places:
    """Where the rules of an import are read from, and what it gives the rows none matches."""

    shared: Sequence[Path]  # the rules both people share, first wins; a rule goes in the first
    people: Mapping[str, Patterns]  # what recognises each person as a counterparty
    unmatched: str = ""  # the category of the income and expenses no rule matches; "": none

    def own(self, owner: str) -> Path:
        return classification.personal_file(self.shared[0], owner)


@dataclass(frozen=True, slots=True)
class Suggestion:
    row: str  # the id of a row it was offered for: adding it names that row
    merchant: str  # the words it looks for, in order
    match: str  # the pattern written
    direction: str  # in | out
    category: str  # the key
    file: Path  # where it goes, first


@dataclass(frozen=True, slots=True)
class _Rules:
    own: tuple[Rule, ...]  # the owner's own file, tried first
    shared: tuple[Rule, ...]
    everyone: Patterns  # what recognises any person
    others: Patterns  # what recognises the people other than the owner
    unmatched: str  # the key of the category the rows no rule matches get; "": none


def suggest(
    conn: sqlite3.Connection, places: Places, owner: str, ids: Collection[str]
) -> list[Suggestion]:
    """The rules to offer for the rows `ids` of `owner` whose category was changed by hand,
    one per rule, in the order of the rows. Rows that are not the owner's, or whose category
    cannot be changed, are left out. Raises what reading the rules files raises."""
    owner_id = store.owner_id(conn, owner)
    rows = [] if owner_id is None or not ids else store.categorized(conn, owner_id, ids)
    if not rows:
        return []
    rules = _rules(conn, places, owner)
    found: dict[tuple[Path, str, str, str], Suggestion] = {}
    for row in rows:
        offered = _offer(row, rules, places, owner)
        if offered is not None:
            # One rule for one merchant: rules do not tell upper and lower case apart.
            key = (offered.file, offered.merchant.lower(), offered.direction, offered.category)
            found.setdefault(key, offered)
    return list(found.values())


def add(conn: sqlite3.Connection, places: Places, owner: str, row: str) -> Suggestion:
    """Write the rule offered for the row `row` first in its file, and return it. Refused
    when none is offered for it now: the rules or the row have changed since."""
    with yamlfiles.LOCK:  # what is checked is what is written: no other writer in between
        offered = suggest(conn, places, owner, [row])
        if not offered:
            msg = "that rule is not needed any more, or the row is gone: load the page again"
            raise SuggestionError(msg)
        (rule,) = offered
        written = store.category_labels(conn).written(rule.category)
        classification.append_match(rule.file, rule.match, rule.direction, written, first=True)
    return rule


def _rules(conn: sqlite3.Connection, places: Places, owner: str) -> _Rules:
    """The rules an import for `owner` applies, as `importing` reads them."""
    known = categories.names(categories.load(places.shared[0].with_name(categories.FILE)))
    everyone = [pattern for patterns in places.people.values() for pattern in patterns]
    others = [p for name, patterns in places.people.items() if name != owner for p in patterns]

    def read(paths: Sequence[Path]) -> tuple[Rule, ...]:
        return classification.between_people(classification.load_rules(paths, known), others)

    unmatched = store.category_key(conn, places.unmatched) if places.unmatched else ""
    return _Rules(read([places.own(owner)]), read(places.shared), everyone, others, unmatched)


def _offer(row: store.Categorized, rules: _Rules, places: Places, owner: str) -> Suggestion | None:
    words = query.merchant_words((row.counterparty or row.description).strip())
    if not words:
        return None
    txn = Transaction(
        date=date.min,
        account=row.account,
        amount=from_nano(row.amount_nano),
        currency="",
        description=row.description,
        kind=Kind(row.kind),
        source_file="",
        source_ref="",
        counterparty=row.counterparty,
    )
    flow = classification.direction(txn)
    match = classification.merchant_match(words)
    if len(match) > classification.LONGEST:
        return None
    if not Rule(re.compile(match, re.IGNORECASE), row.category, direction=flow).matches(txn):
        return None  # a text the pattern cannot find again (a line break inside a name)
    if row.side == "transfer" and classification.names(txn, rules.others):
        return None  # money between two people is never a transfer (D14)
    own = next((rule for rule in rules.own if rule.matches(txn)), None)
    winner = own or next((rule for rule in rules.shared if rule.matches(txn)), None)
    given = winner.category if winner else (rules.unmatched if row.kind in _FLOWS else "")
    if given == row.category:
        return None
    personal = own is not None or classification.names(txn, rules.everyone)
    file = places.own(owner) if personal else places.shared[0]
    return Suggestion(row.id, " ".join(words), match, flow, row.category, file)
