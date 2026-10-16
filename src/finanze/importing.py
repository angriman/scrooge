"""Import statement files for one person: the one service behind `finanze import` and the
import page.

`read` extracts, reconciles and classifies, and stores nothing; `stage` puts what was read in
the database, in open months, and keeps each statement's file next to it. Between the two the
terminal may ask for the categories of the rows no rule matched (`--review`) and `again`
classifies with what was answered.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from finanze import categories, classification, monthclose, pipeline
from finanze.extractors.common import month_start
from finanze.monthclose import Batch, People, Staged
from finanze.storage import originals, store


@dataclass(frozen=True, slots=True)
class Setup:
    """What an import reads besides the statements."""

    rules: Sequence[Path]  # the rules both people share, first wins
    instruments: Path
    people: People  # what recognises each person as a counterparty
    labels: Mapping[str, str]  # a further account at a broker, named by its number

    @property
    def categories_file(self) -> Path:
        return self.rules[0].with_name(categories.FILE)

    def own(self, owner: str) -> Path:
        """The rules of `owner` alone, read before the shared ones."""
        return classification.personal_file(self.rules[0], owner)


@dataclass(frozen=True, slots=True)
class Read:
    """The files of one import, reconciled and classified; nothing is stored yet."""

    owner: str
    files: tuple[Path, ...]
    statements: tuple[pipeline.Statement, ...]
    certificates: tuple[pipeline.Certificate, ...]
    skipped: tuple[Path, ...]  # files that are no statements
    month: pipeline.Month


def read(owner: str, files: Sequence[Path], setup: Setup) -> Read:
    """Extract and reconcile `files` as statements of `owner`. Any failure raises."""
    certificates, others = pipeline.load_certificates(files)
    loaded, skipped = pipeline.load_files(others)
    instruments = classification.load_instruments(setup.instruments)
    statements = pipeline.resolve_holdings(pipeline.relabel(loaded, setup.labels), instruments)
    return Read(
        owner,
        tuple(files),
        tuple(statements),
        tuple(certificates),
        tuple(skipped),
        _classified(owner, statements, certificates, skipped, setup),
    )


def again(found: Read, setup: Setup) -> Read:
    """`found` classified with the rules and categories as they are now."""
    month = _classified(found.owner, found.statements, found.certificates, found.skipped, setup)
    return replace(found, month=month)


def _classified(
    owner: str,
    statements: Sequence[pipeline.Statement],
    certificates: Sequence[pipeline.Certificate],
    skipped: Sequence[Path],
    setup: Setup,
) -> pipeline.Month:
    if certificates and not statements:
        # Certificates alone: no statement, no row, the month of the earliest one.
        earliest = min(certificate.stated.as_of for certificate in certificates)
        return pipeline.Month(month_start(earliest), (), (), tuple(skipped))
    others = [
        pattern for name, patterns in setup.people.items() if name != owner for pattern in patterns
    ]
    known = categories.names(categories.load(setup.categories_file))
    rule_files = [setup.own(owner), *setup.rules]
    rules = classification.between_people(classification.load_rules(rule_files, known), others)
    extractions = [(s.source, s.extraction) for s in statements]
    instruments = classification.load_instruments(setup.instruments)
    return pipeline.process(extractions, rules, instruments, skipped)


def stage(
    conn: sqlite3.Connection, database: Path, found: Read, setup: Setup, now: datetime
) -> Staged:
    """Store what was read in open months of its owner, then keep the statements' files."""
    added = categories.load(setup.categories_file)
    batch = Batch(
        found.owner, found.statements, found.month, setup.people, added, found.certificates
    )
    staged = monthclose.stage(conn, batch, now)
    # After the rows are in: a file is kept because the database points to it. `finanze keep`
    # makes up for a copy that failed here.
    originals.keep(originals.folder_of(database), found.files, store.statement_names(conn))
    return staged
