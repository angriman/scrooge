"""Month close: stage reconciled statements in the database, then commit their months (D16, D17).

`stage` stores what the pipeline produced for one person, replacing whatever an earlier import
stored for the same accounts and days. Each statement covers its own period, of one month or
many. The months they cover stay `open`, and the dashboards do not see them, until `commit`.
A pension certificate is a statement of the one day it is dated: it belongs to the month of
that day, which must be open like any other, and gives its account a balance and no rows.
The balance a yearly certificate says its year began with is stored like the one a bank
statement opens with: for a day before its own, and only where no certificate has that day.
Both run in one database transaction: any failure leaves the database as it was.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

from finanze import categories, classification
from finanze.categories import Extra
from finanze.classification import Patterns, names
from finanze.extractors.common import month_end, month_start, months_between
from finanze.models import Kind, Transaction
from finanze.pipeline import Certificate, Month, Statement
from finanze.storage import bills, db, holdings, pairs, store
from finanze.storage.ids import transaction_ids
from finanze.storage.money import from_nano
from finanze.validation import (
    CertificatesDisagreeError,
    ChainMismatchError,
    MissingStatementError,
    ReconciliationError,
)

# Sources whose rows are booked on an account that another source's statement reconciles:
# savings interest on the Revolut account (D8).
_BOOKED_ON = {"revolut_savings": "revolut"}
_KINDS = {"ibkr": "broker_cash", "tastytrade": "broker_cash", "finpension": "broker_cash"}
_KINDS |= {"swisscard": "card"}
# Only what is income or expense for one person can be internal to the family (D14).
_BETWEEN_OWNERS = frozenset({Kind.EXPENSE, Kind.INCOME})
_NOT_WAITING = "are no longer waiting for a category"
# An account that only certificates feed: carried flat from each balance to the next, as a
# typed account is, and outside the chain of balances (`store.anchors`).
_CARRIED_FLAT = "manual"
_RATES_IN = "CHF"

People = Mapping[str, Patterns]


class MonthCloseError(Exception):
    """The month cannot be staged, committed or reopened as asked."""


class MonthCommittedError(MonthCloseError):
    """The month is committed: it must be reopened before anything in it changes."""


class NothingStagedError(MonthCloseError):
    """There is no such month for this person in the database."""


class UncategorizedRowsError(MonthCloseError):
    """Rows of the month still have no category: the month cannot be committed."""


class DuplicateStatementError(MonthCloseError):
    """Two statements of one source cover the same days of an account: counted twice."""


@dataclass(frozen=True, slots=True)
class Batch:
    """One person's month, as the pipeline produced it.

    `month` is the pipeline's result for exactly these `statements`: reconciled and classified.
    `people` maps each person to the patterns that recognise them as a counterparty (D14).
    """

    owner: str
    statements: Sequence[Statement]
    month: Month
    people: People
    categories: Sequence[Extra] = ()  # what `categories.yaml` adds to the taxonomy
    # Pension certificates: not part of `month`, which may then be empty.
    certificates: Sequence[Certificate] = ()


@dataclass(frozen=True, slots=True)
class Staged:
    """What `stage` stored."""

    month: date  # the first month, when the statements cover several
    transactions: int
    uncategorized: int
    # What an earlier statement would settle: it blocks the commit, not the import.
    gaps: tuple[str, ...] = ()


def stage(conn: sqlite3.Connection, batch: Batch, now: datetime) -> Staged:
    """Store the month for its owner, replacing an earlier import of the same statements."""
    owner, statements, month, people = batch.owner, batch.statements, batch.month, batch.people
    with db.transaction(conn):
        owner_id = store.ensure_owner(conn, owner)
        months = {m for s in statements for m in months_between(*s.extraction.period)}
        months |= {month_start(c.stated.as_of) for c in batch.certificates}
        for covered in sorted(months):
            if store.month_status(conn, owner_id, covered) == "committed":
                msg = f"{covered:%Y-%m} is committed for {owner}: reopen it first"
                raise MonthCommittedError(msg)
            store.set_month(conn, owner_id, covered, "open", None)
        store.seed_categories(conn, batch.categories)
        others = {
            store.ensure_owner(conn, name): patterns
            for name, patterns in people.items()
            if name != owner
        }

        by_file: dict[str, list[Transaction]] = {s.filename: [] for s in statements}
        for txn in month.transactions:
            by_file[txn.source_file].append(txn)
        accounts = {
            s.filename: _accounts(conn, owner_id, s, by_file[s.filename]) for s in statements
        }
        _refuse_duplicates(statements, accounts)

        replaced: set[int] = set()
        for statement in statements:
            ids = accounts[statement.filename].values()
            period = statement.extraction.period
            replaced |= store.delete_statement_rows(conn, statement.source, ids, period)
        files = {
            s.filename: store.upsert_statement_file(
                conn,
                owner_id,
                source=s.source,
                filename=s.filename,
                sha256=s.sha256,
                period=s.extraction.period,
                reconciled="yes" if _has_figures(s) else "nothing_to_check",
                imported_at=db.timestamp(now),
                holdings=s.extraction.holdings is not None,
            )
            for s in statements
        }

        leaves = store.leaf_categories(conn)
        rows = [(s.source, txn) for s in statements for txn in by_file[s.filename]]
        for txn_id, (_, txn) in zip(transaction_ids(owner, rows), rows, strict=True):
            store.insert_transaction(
                conn,
                txn_id,
                txn,
                account=accounts[txn.source_file][txn.account],
                category=store.category_for(leaves, txn.category, txn.amount),
                counterparty_owner=_counterparty_owner(txn, others),
                statement_file=files[txn.source_file],
            )
        for statement in statements:
            first, last = statement.extraction.period
            for balance in statement.extraction.balances:
                account = accounts[statement.filename][balance.account]
                file_id = files[statement.filename]
                before = first - timedelta(days=1)
                store.set_closing_balance(conn, account, last, balance.closing, file_id)
                store.set_opening_balance(conn, account, before, balance.opening, file_id)
            _store_valuation(conn, statement, accounts[statement.filename], files)
        stored, dropped = _store_certificates(conn, owner_id, batch.certificates, now)
        replaced |= dropped

        store.drop_unused_files(conn, replaced - set(files.values()) - stored)
        gaps = tuple(str(gap) for gap in _unsettled(conn, owner_id))
        store.apply_overrides(conn)
        pairs.mark(conn)
        pending = store.uncategorized(conn, owner_id, month.period)
        return Staged(month.month, len(rows), pending, gaps)


def _store_certificates(
    conn: sqlite3.Connection, owner_id: int, certificates: Sequence[Certificate], now: datetime
) -> tuple[set[int], set[int]]:
    """Give each certificate's account the balance it states for its day.

    A certificate read again, or a new scan of it, replaces what it stored; one of another
    day adds a balance and leaves the others. The balance its year began with goes on that
    earlier day unless the certificate of that day is stored, which keeps it; two that state
    a day differently are refused. Returns the files now stored and the files whose balances
    were replaced.
    """
    seen: dict[tuple[str, date], str] = {}
    stored: set[int] = set()
    replaced: set[int] = set()
    for certificate in certificates:
        stated = certificate.stated
        other = seen.setdefault((certificate.account, stated.as_of), certificate.filename)
        if other != certificate.filename:
            msg = (
                f"{other} and {certificate.filename} are both certificates of "
                f"{certificate.account} for {stated.as_of}"
            )
            raise DuplicateStatementError(msg)
        account = store.ensure_account(
            conn,
            owner_id,
            name=certificate.account,
            kind=_CARRIED_FLAT,
            currency=stated.currency,
            source=stated.source,
        )
        day = (stated.as_of, stated.as_of)
        replaced |= store.delete_statement_rows(conn, stated.source, [account], day)
        file_id = store.upsert_statement_file(
            conn,
            owner_id,
            source=stated.source,
            filename=certificate.filename,
            sha256=certificate.sha256,
            period=day,
            reconciled="yes" if certificate.adds_up else "nothing_to_check",
            imported_at=db.timestamp(now),
        )
        days = [(stated.as_of, stated.balance), *([stated.opened] if stated.opened else [])]
        for on, balance in days:  # what is left on a day now is another certificate's
            standing = store.balance_on(conn, account, on)
            if standing is not None and standing != balance:
                raise CertificatesDisagreeError(
                    certificate.account, stated.currency, on, balance, standing
                )
        store.set_closing_balance(conn, account, stated.as_of, stated.balance, file_id)
        if stated.opened:
            store.set_opening_balance(conn, account, *stated.opened, file_id)
        stored.add(file_id)
    return stored, replaced


def _store_valuation(
    conn: sqlite3.Connection,
    statement: Statement,
    accounts: Mapping[str, int],
    files: Mapping[str, int],
) -> None:
    """What the statement says its accounts held and were worth at the end of its period."""
    extraction, file_id = statement.extraction, files[statement.filename]
    last = extraction.period[1]
    store.set_statement_values(conn, file_id, extraction.values)
    for rate in extraction.rates:
        if rate.base == _RATES_IN:  # the only rates the database keeps
            store.set_rate(conn, last, rate.currency, rate.rate, source="statement")
    for holding in extraction.holdings or ():
        store.set_holding(conn, accounts[holding.account], last, holding, file_id)


def commit(conn: sqlite3.Connection, *, owner: str, month: date, now: datetime) -> bool:
    """Make the month visible to the dashboards. Returns False if it already was."""
    with db.transaction(conn):
        owner_id, status = _month(conn, owner, month)
        if status == "committed":
            return False
        pending = store.uncategorized(conn, owner_id, (month, month_end(month)))
        if pending:
            msg = f"{pending} transactions of {month:%Y-%m} still need a category"
            raise UncategorizedRowsError(msg)
        for gap in _unsettled(conn, owner_id):
            raise gap
        store.set_month(conn, owner_id, month, "committed", db.timestamp(now))
        return True


@contextmanager
def _rules_kept() -> Iterator[dict[Path, str | None]]:
    """Yields a place for what each rules file held before it was touched (None: no file).

    Whatever raises inside, those files are put back: a rule is never saved for a choice
    the database did not keep.
    """
    before: dict[Path, str | None] = {}
    try:
        yield before
    except BaseException:
        for path, text in before.items():
            if text is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(text, encoding="utf-8")
        raise


def copy_before(conn: sqlite3.Connection, database: Path, now: datetime) -> Path:
    """Save a copy of the database in `backups/` next to it, under a name no copy has yet.

    Two commits in one second get two files: dropping the copy of the one that changed
    nothing never takes away the copy the other one kept.
    """
    folder = database.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{database.stem}-{now:%Y%m%dT%H%M%S}"
    taken = 0
    while True:
        copy = folder / (f"{stem}-{taken + 1}.db" if taken else f"{stem}.db")
        try:
            copy.open("xb").close()  # claimed here: a commit in another thread takes the next
        except FileExistsError:
            taken += 1
        else:
            break
    db.backup(conn, copy)
    return copy


def commit_with_copy(
    conn: sqlite3.Connection, database: Path, *, owner: str, month: date, now: datetime
) -> Path | None:
    """Commit one month after saving a copy of the database next to it.

    Returns the copy, or None when the month was already committed. The copy is dropped
    when nothing changed, including when the commit is refused.
    """
    copy = copy_before(conn, database, now)
    try:
        done = commit(conn, owner=owner, month=month, now=now)
    except BaseException:
        copy.unlink()
        raise
    if not done:
        copy.unlink()
        return None
    return copy


@dataclass(frozen=True, slots=True)
class Remember:
    """Where a choice of category is saved as a rule, as `--review` does (plan 5.4)."""

    shared: Path  # the rules both people share
    own: Path  # the owner's rules, for rows that name a person
    people: Patterns  # what recognises a person, anybody
    # What recognises the people other than the owner: on import a rule giving a transfer
    # category does not apply to a row naming one of them (`classification.between_people`).
    others: Patterns = ()


def categorize(  # noqa: PLR0913  # whose rows, which, what for, and how it is remembered
    conn: sqlite3.Connection,
    *,
    owner: str,
    month: date,
    ids: Collection[str],
    category: str,
    now: datetime,
    remember: Remember | None,
    change: bool = False,
) -> int:
    """Give `category` to rows of an open month that have none. Returns how many.

    With `remember` the choice also becomes a rule for each counterparty and direction among
    the rows, so the next import makes it again. Without, it is kept as an override of these
    rows alone. A transfer category makes an income or expense a transfer, as a rule would.
    Rule files are left as they were when anything fails.

    With `change` the rows are instead ones that have a category, which was wrong: money in
    or out, nothing a statement categorizes itself. The choice is always kept as an override
    of these rows, and a rule to remember it goes before the rules already there, the wrong
    one among them. A transfer given another kind of category is income or an expense again.
    """
    with _rules_kept() as before, db.transaction(conn):
        owner_id, status = _month(conn, owner, month)
        if status == "committed":
            msg = f"{month:%Y-%m} is committed for {owner}: reopen it first"
            raise MonthCommittedError(msg)
        rows = store.waiting(conn, owner_id, (month, month_end(month)), ids, change=change)
        if not rows or len(rows) != len(set(ids)):
            state = "cannot be given another category" if change else _NOT_WAITING
            msg = f"those rows {state}: load the page again"
            raise MonthCloseError(msg)
        leaves = store.leaf_categories(conn)
        flows = {kind.value for kind in _BETWEEN_OWNERS}
        rules: dict[tuple[str, str], Path] = {}
        for row in rows:
            chosen = store.category_for(leaves, category, from_nano(row.amount_nano))
            if chosen is None:
                msg = "no category was chosen"
                raise MonthCloseError(msg)
            transfer = ("transfer", category) in leaves
            kind = row.kind
            if transfer and row.kind in flows:
                kind = Kind.TRANSFER.value
            elif not transfer and row.kind == Kind.TRANSFER.value:
                kind = (Kind.INCOME if row.amount_nano > 0 else Kind.EXPENSE).value
            kept = remember is None or change  # as an override of this row
            store.set_category(conn, row.id, chosen, "manual" if kept else "rule", kind)
            if kept:
                changed = kind if kind != row.kind else None
                store.set_override(conn, row.id, chosen, changed, db.timestamp(now))
            if remember is None:
                continue
            name = (row.counterparty or row.description).strip()
            if not name:
                msg = "a row without any text cannot become a rule: choose 'just these rows'"
                raise MonthCloseError(msg)
            text = f"{row.description}\n{row.counterparty}"
            if transfer and any(pattern.search(text) for pattern in remember.others):
                msg = (
                    f"a rule cannot make money to or from another person a transfer ({name}): "
                    "choose 'just these rows'"
                )
                raise MonthCloseError(msg)
            named = any(pattern.search(text) for pattern in remember.people)
            flow = "in" if row.amount_nano > 0 else "out"
            rules[name, flow] = remember.own if named else remember.shared
        for (name, flow), where in rules.items():
            if where not in before:
                before[where] = where.read_text(encoding="utf-8") if where.exists() else None
            classification.append_rule(where, name, flow, category, first=change)
        return len(rows)


def add_category(
    conn: sqlite3.Connection, file: Path, *, name: str, side: str, group: str
) -> Extra:
    """Add a category to the taxonomy: in `file` (`categories.yaml`), which every import
    reads, and in the database. Returns it.

    `side` is `uscite` or `entrate`. An expense goes under `group`, one of the top-level
    categories there are, or under a new one named like itself when no group is given.
    """
    name = " ".join(name.split())
    if not name:
        msg = "a category needs a name"
        raise MonthCloseError(msg)
    if side not in categories.SIDES:
        msg = "a category is added to the expenses or to the income"
        raise MonthCloseError(msg)
    with _rules_kept() as before, db.transaction(conn):
        extras = categories.load(file)
        store.seed_categories(conn, extras)
        leaves = store.leaf_categories(conn)
        if (side, name) in leaves or ("transfer", name) in leaves:
            msg = f"there is already a category {name!r}"
            raise MonthCloseError(msg)
        if side == "uscite" and group and group not in categories.groups(extras):
            msg = f"no group of categories named {group!r}"
            raise MonthCloseError(msg)
        extra = Extra(name, side, (group or name) if side == "uscite" else "")
        before[file] = file.read_text(encoding="utf-8") if file.exists() else None
        categories.add(file, extra)
        store.seed_categories(conn, categories.load(file))
        return extra


def type_balance(  # noqa: PLR0913  # one fact: who, which account, when, how much
    conn: sqlite3.Connection, *, owner: str, account: str, day: date, amount: Decimal, currency: str
) -> None:
    """Record what an account without statements held at the end of `day`.

    For accounts that are only ever typed (a closed bank): the account is created on first
    use, and can never also be fed from statements or from certificates.
    """
    with db.transaction(conn):
        owner_id = store.ensure_owner(conn, owner)
        account_id = store.ensure_account(
            conn, owner_id, name=account, kind="manual", currency=currency, source=None
        )
        store.set_typed_balance(conn, account_id, day, amount)


def reopen(conn: sqlite3.Connection, *, owner: str, month: date) -> None:
    """Take a committed month back to `open`, so that it can be imported again."""
    with db.transaction(conn):
        owner_id, _ = _month(conn, owner, month)
        store.set_month(conn, owner_id, month, "open", None)


def would_refuse(conn: sqlite3.Connection, owner_id: int) -> list[ReconciliationError]:
    """What a commit of the owner's months would refuse, without changing anything."""
    return [
        *check_chain(conn, owner_id),
        *bills.missing(conn, owner_id),
        *holdings.check(conn, owner_id),
    ]


def _unsettled(conn: sqlite3.Connection, owner_id: int) -> list[ReconciliationError]:
    """Bring the derived rows up to date; returns what only an earlier statement can settle."""
    return [
        *check_chain(conn, owner_id),
        *bills.split(conn, owner_id),
        *holdings.check(conn, owner_id),
    ]


def check_chain(conn: sqlite3.Connection, owner_id: int) -> list[MissingStatementError]:
    """Every statement balance must follow from the one before it plus what was booked between.

    This is what ties months together. Two statements that disagree raise at once. A balance
    that changed over days no statement covers is a missing statement: those are returned,
    since the statement may simply not be imported yet, and it is the commit that refuses them.
    """
    gaps: list[MissingStatementError] = []
    for earlier, later in pairwise(store.anchors(conn, owner_id)):
        if earlier.account_id != later.account_id:
            continue
        span = (earlier.day, later.day)
        reached = earlier.amount_nano + store.own_flow(conn, later.account_id, *span)
        if reached == later.amount_nano:
            continue
        expected, actual = from_nano(later.amount_nano), from_nano(reached)
        if not later.opening:
            raise ChainMismatchError(later.account, later.currency, span, expected, actual)
        gaps.append(MissingStatementError(later.account, later.currency, span, (actual, expected)))
    return gaps


def _has_figures(statement: Statement) -> bool:
    return bool(statement.extraction.balances or statement.extraction.controls)


def _month(conn: sqlite3.Connection, owner: str, month: date) -> tuple[int, str]:
    owner_id = store.owner_id(conn, owner)
    status = None if owner_id is None else store.month_status(conn, owner_id, month)
    if owner_id is None or status is None:
        msg = f"nothing is staged for {owner} in {month:%Y-%m}"
        raise NothingStagedError(msg)
    return owner_id, status


def _accounts(
    conn: sqlite3.Connection, owner_id: int, statement: Statement, rows: Sequence[Transaction]
) -> dict[str, int]:
    """name -> id of every account the statement touches, created on first sight."""
    extraction = statement.extraction
    named = {(txn.account, txn.currency) for txn in rows}
    named |= {(b.account, b.currency) for b in extraction.balances}
    named |= {(c.account, c.currency) for c in extraction.controls}
    named |= {(h.account, h.currency) for h in extraction.holdings or ()}
    source = _BOOKED_ON.get(statement.source, statement.source)
    kind = _KINDS.get(source, "bank")
    return {
        name: store.ensure_account(
            conn, owner_id, name=name, kind=kind, currency=currency, source=source
        )
        for name, currency in sorted(named)
    }


def _refuse_duplicates(
    statements: Sequence[Statement], accounts: Mapping[str, Mapping[str, int]]
) -> None:
    seen: dict[tuple[str, str], list[Statement]] = {}
    for statement in statements:
        first, last = statement.extraction.period
        for account in accounts[statement.filename]:
            others = seen.setdefault((statement.source, account), [])
            for other in others:
                start, end = other.extraction.period
                if first <= end and start <= last:
                    both = f"{other.filename} and {statement.filename}"
                    msg = f"{both} are both {statement.source} statements of {account}"
                    raise DuplicateStatementError(msg)
            others.append(statement)


def _counterparty_owner(txn: Transaction, others: Mapping[int, Patterns]) -> int | None:
    if txn.kind not in _BETWEEN_OWNERS:
        return None
    return next((other for other, patterns in others.items() if names(txn, patterns)), None)
