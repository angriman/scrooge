"""Import statement files for one person: the one service behind `scrooge import` and the
import page.

`read` extracts, reconciles and classifies each file, and stores nothing; `stage` puts what
was read in the database, staged until it is committed, and keeps each statement's file next
to it. Between the two the terminal may ask for the categories of the rows no rule matched
(`--review`) and `again` classifies with what was answered.

Every file stands on its own: one that is not recognised, does not reconcile or is another
person's is refused and said so (`File.problem`), and the others are staged all the same
(decided 2026-10-16: an IBKR statement need not wait for the bank's).
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

from scrooge import archives, categories, classification, pipeline, router, staging
from scrooge.classification import Instrument
from scrooge.errors import ConfigError, ExtractionError
from scrooge.staging import Batch, People, Staged
from scrooge.storage import originals, store
from scrooge.validation import CardBillMismatchError, ReconciliationError, check_balances

# What refuses one file and leaves the others to be staged.
_REFUSED = (
    ExtractionError,
    ReconciliationError,
    staging.StagingError,
    store.AccountMismatchError,
)
SWISSCARD = "swisscard"


@dataclass(frozen=True, slots=True)
class Setup:
    """What an import reads besides the statements."""

    rules: Sequence[Path]  # the rules both people share, first wins
    instruments: Path
    people: People  # what recognises each person as a counterparty
    labels: Mapping[str, str]  # a further account at a broker, named by its number
    # The category of the expenses and income no rule matches; "": they go to review.
    unmatched: str = ""

    @property
    def categories_file(self) -> Path:
        return self.rules[0].with_name(categories.FILE)

    def own(self, owner: str) -> Path:
        """The rules of `owner` alone, read before the shared ones."""
        return classification.personal_file(self.rules[0], owner)


@dataclass(frozen=True, slots=True)
class File:
    """One file of an import, and what became of it."""

    path: Path
    name: str  # as the statement and its rows call it
    source: str = ""  # the extractor that read it; "" when none did
    problem: str = ""  # why it was refused; "" when it was not
    staged: bool = False  # something of it is staged now
    nothing_new: bool = False  # read, and all of it is committed already

    @property
    def label(self) -> str:
        """What kind of statement it is, for people."""
        if self.source:
            return router.LABELS.get(self.source, self.source)
        return "not a statement" if not self.problem else "not read"

    @property
    def recognised(self) -> bool:
        return bool(self.source)


@dataclass(frozen=True, slots=True)
class Read:
    """The files of one import, reconciled and classified; nothing is stored yet."""

    owner: str
    files: tuple[File, ...]
    statements: tuple[pipeline.Statement, ...]
    certificates: tuple[pipeline.Certificate, ...]
    month: pipeline.Month
    staged: Staged | None = None  # after `stage`: what the database holds now

    @property
    def skipped(self) -> tuple[Path, ...]:
        """The files that are no statements."""
        return tuple(f.path for f in self.files if not f.source and not f.problem)

    @property
    def refused(self) -> tuple[File, ...]:
        return tuple(f for f in self.files if f.problem)


def read(owner: str, files: Sequence[Path], setup: Setup, unpack_into: Path | None = None) -> Read:
    """Extract and reconcile each of `files` as a statement of `owner`.

    A file that cannot be read or does not reconcile is refused on its own; a card invoice
    whose payments the bank statements of the same import contradict too.

    Each file of a zip is read as if it had been given on its own (`archives`), once
    `unpack_into` names a folder for them that lasts until they are staged. Without one, a
    zip is not a statement.
    """
    instruments = classification.load_instruments(setup.instruments)
    outcomes: list[File] = []
    statements: list[pipeline.Statement] = []
    certificates: list[pipeline.Certificate] = []
    named = pipeline.names(files)
    if unpack_into is not None:
        named, refused = _unpacked(named, unpack_into)
        outcomes += refused
    for path, name in sorted(named.items(), key=lambda item: item[1]):
        try:
            found = _read_one(path, name, setup.labels, instruments)
        except _REFUSED as exc:
            outcomes.append(File(path, name, problem=str(exc)))
            continue
        if isinstance(found, pipeline.Statement):
            statements.append(found)
            outcomes.append(File(path, name, found.source))
        elif isinstance(found, pipeline.Certificate):
            certificates.append(found)
            outcomes.append(File(path, name, found.stated.source))
        else:
            outcomes.append(File(path, name))
    try:
        month = _classified(owner, statements, setup)
    except CardBillMismatchError as exc:
        # The card's payments against the bank's debits: the invoice is the one set aside.
        cards = {s.filename for s in statements if s.source == SWISSCARD}
        outcomes = [replace(f, problem=str(exc)) if f.name in cards else f for f in outcomes]
        statements = [s for s in statements if s.filename not in cards]
        month = _classified(owner, statements, setup)
    outcomes.sort(key=lambda f: f.name)
    return Read(owner, tuple(outcomes), tuple(statements), tuple(certificates), month)


def _unpacked(named: Mapping[Path, str], folder: Path) -> tuple[dict[Path, str], list[File]]:
    """`named` with each zip replaced by its files, and the zips and files of zips refused."""
    files, refused = archives.unpack_all(named, folder)
    return files, [File(entry.path, entry.name, problem=entry.problem) for entry in refused]


def _read_one(
    path: Path, name: str, labels: Mapping[str, str], instruments: Mapping[str, Instrument]
) -> pipeline.Statement | pipeline.Certificate | None:
    scanned = router.load_certificate(path)
    if scanned is not None:
        account, adds_up, stated = scanned
        return pipeline.Certificate(name, _digest(path), account, adds_up, stated)
    loaded = router.load(path, name)
    if loaded is None:
        return None
    statement = pipeline.Statement(loaded[0], name, _digest(path), loaded[1])
    check_balances(statement.extraction)
    (statement,) = pipeline.relabel([statement], labels)
    (statement,) = pipeline.resolve_holdings([statement], instruments)
    return statement


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def again(found: Read, setup: Setup) -> Read:
    """`found` classified with the rules and categories as they are now."""
    return replace(found, month=_classified(found.owner, found.statements, setup))


def _classified(
    owner: str, statements: Sequence[pipeline.Statement], setup: Setup
) -> pipeline.Month:
    if not statements:  # certificates alone, or nothing that could be read: no row
        return pipeline.Month(date.min, (), (), ())
    others = [
        pattern for name, patterns in setup.people.items() if name != owner for pattern in patterns
    ]
    known = categories.names(categories.load(setup.categories_file))
    rule_files = [setup.own(owner), *setup.rules]
    rules = classification.between_people(classification.load_rules(rule_files, known), others)
    extractions = [(s.source, s.extraction) for s in statements]
    instruments = classification.load_instruments(setup.instruments)
    month = pipeline.process(extractions, rules, instruments)
    if not setup.unmatched:
        return month
    if setup.unmatched not in known:
        msg = f"unmatched: {setup.unmatched!r} is no category (see {setup.categories_file})"
        raise ConfigError(msg)
    return replace(
        month, transactions=classification.unmatched(month.transactions, setup.unmatched)
    )


def stage(
    conn: sqlite3.Connection, database: Path, found: Read, setup: Setup, now: datetime
) -> Read:
    """Stage what was read for its owner, then keep the statements' files. Returns `found`
    with what became of each file, and `Read.staged` what the database holds now.

    All the files go in one database transaction. When that is refused, each goes in one of
    its own, so that the refusal is said of the file it is about and the others are staged.
    """
    added = categories.load(setup.categories_file)

    def batch(
        statements: Sequence[pipeline.Statement], certificates: Sequence[pipeline.Certificate]
    ) -> Batch:
        names = {s.filename for s in statements}
        rows = tuple(t for t in found.month.transactions if t.source_file in names)
        month = replace(found.month, transactions=rows)
        return Batch(found.owner, statements, month, setup.people, added, certificates)

    results: list[Staged] = []
    problems: dict[str, str] = {}
    try:
        results.append(staging.stage(conn, batch(found.statements, found.certificates), now))
    except _REFUSED:
        # Two files of one account and the same days: staged one by one the second would
        # replace the first, and which it is would depend on their names. Both are refused.
        problems.update(staging.twins(found.statements, found.month.transactions))
        # In date order: a statement that does not follow from the one before is the one
        # refused, not the earlier one staged after it.
        for statement in staging.in_date_order(found.statements):
            if statement.filename in problems:
                continue
            try:
                results.append(staging.stage(conn, batch([statement], ()), now))
            except _REFUSED as exc:
                problems[statement.filename] = str(exc)
        for certificate in sorted(found.certificates, key=lambda c: (c.stated.as_of, c.filename)):
            try:
                results.append(staging.stage(conn, batch((), [certificate]), now))
            except _REFUSED as exc:
                problems[certificate.filename] = str(exc)
    # After the rows are in: a file is kept because the database points to it. `scrooge keep`
    # makes up for a copy that failed here.
    kept = [f.path for f in found.files if f.source and f.name not in problems]
    originals.keep(originals.folder_of(database), kept, store.statement_names(conn))
    unchanged = {name for result in results for name in result.nothing_new}
    files = tuple(
        f
        if not f.source or f.problem
        else replace(f, problem=problems[f.name])
        if f.name in problems
        else replace(f, staged=f.name not in unchanged, nothing_new=f.name in unchanged)
        for f in found.files
    )
    return replace(found, files=files, staged=_merged(results))


def _merged(results: Sequence[Staged]) -> Staged:
    if not results:
        return Staged((), 0, 0)
    last = results[-1]  # what is owed for the owner as a whole, after the last of them
    return Staged(
        files=tuple(sorted({f for r in results for f in r.files})),
        transactions=sum(r.transactions for r in results),
        uncategorized=last.uncategorized,
        gaps=last.gaps,
        already=sum(r.already for r in results),
        differences=tuple(line for r in results for line in r.differences),
        later=sum(r.later for r in results),
        nothing_new=tuple(name for r in results for name in r.nothing_new),
    )
