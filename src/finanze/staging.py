"""Stage reconciled statements in the database, then commit them (D16, decided 2026-10-16).

There is no month to close: a person imports statements whenever they have them, of any
period, and commits what was staged. `stage` stores what the pipeline produced for one person.
Of each account a statement covers, what statements of its source have committed already is
left as it is: the rows the statement books there are only compared with what is stored, and
the rest is staged from the day after. A staged statement replaces what an earlier staged
import of the same source stored for the same accounts and days. The dashboards see nothing
of it until `commit`. A day is staged only once it is over: what a statement books on the day
of the import or later waits for the next import.

A pension certificate is a statement of the one day it is dated and gives its account a
balance and no rows. The balance a yearly certificate says its year began with is stored like
the one a bank statement opens with: for a day before its own, and only where no certificate
has that day. Each function runs in one database transaction: any failure leaves the database
as it was.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

from finanze import categories, classification
from finanze.categories import Extra
from finanze.classification import Patterns, names
from finanze.models import CashBalance, Kind, Transaction, TypedHolding
from finanze.pipeline import Certificate, Month, Statement
from finanze.storage import bills, db, holdings, pairs, store
from finanze.storage.ids import transaction_ids
from finanze.storage.money import from_nano, to_nano
from finanze.validation import (
    CertificatesDisagreeError,
    ChainMismatchError,
    MissingStatementError,
    ReconciliationError,
)

# Sources whose rows are booked on an account that another source's statement reconciles:
# savings interest on the Revolut account (D8).
_BOOKED_ON = {"revolut_savings": "revolut"}
_BROKERS = ("ibkr", "tastytrade", "finpension", "morganstanley", "schwab")
_KINDS = dict.fromkeys(_BROKERS, "broker_cash")
_KINDS |= {"swisscard": "card"}
# Only what is income or expense for one person can be internal to the family (D14).
_BETWEEN_OWNERS = frozenset({Kind.EXPENSE, Kind.INCOME})
_NOT_WAITING = "are no longer waiting for a category"
# An account that only certificates feed: carried flat from each balance to the next, as a
# typed account is, and outside the chain of balances (`store.anchors`).
_CARRIED_FLAT = "manual"
_RATES_IN = "CHF"
# How many days apart an older import may have booked a row it shares with a newer one.
_MOVED = timedelta(days=7)

People = Mapping[str, Patterns]


class StagingError(Exception):
    """The statements cannot be staged, committed or taken back as asked."""


class CommittedError(StagingError):
    """What was asked would change what is committed."""


class NothingStagedError(StagingError):
    """There is no such staged statement for this person in the database."""


class UncategorizedRowsError(StagingError):
    """Staged rows still have no category: they cannot be committed."""


class DuplicateStatementError(StagingError):
    """Two statements of one source cover the same days of an account: counted twice."""


class OtherOwnerError(StagingError):
    """The statement is of an account whose statements were imported for another person."""


@dataclass(frozen=True, slots=True)
class Batch:
    """Statements of one person, as the pipeline produced them.

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
    """What an import left in the database."""

    files: tuple[int, ...]  # the statement files it staged
    transactions: int  # rows it staged
    uncategorized: int  # of the owner's staged rows, from this import or an earlier one
    # What an earlier statement would settle: it blocks the commit, not the import.
    gaps: tuple[str, ...] = ()
    # Rows of the statements on days already committed: found there and left alone.
    already: int = 0
    # Where the statements and what is committed disagree. Nothing was changed there.
    differences: tuple[str, ...] = ()
    later: int = 0  # rows booked on the day of the import or after: for the next import
    # Statements with nothing new: every day of them is committed (or not over yet).
    nothing_new: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Part:
    """What of one statement can be staged."""

    statement: Statement
    accounts: Mapping[str, int]  # name -> id of every account it touches
    period: tuple[date, date]  # up to the last day that is over
    balances: tuple[CashBalance, ...]  # their closing on that day
    # account name -> the first day to stage; None: every day of it is committed
    starts: Mapping[str, date | None]

    @property
    def staged(self) -> bool:
        return any(start is not None for start in self.starts.values())

    @property
    def first(self) -> date:
        return min(start for start in self.starts.values() if start is not None)


def stage(conn: sqlite3.Connection, batch: Batch, now: datetime) -> Staged:
    """Store the statements for their owner, replacing an earlier staged import of the same.

    A statement may reach back into days that are committed, as an export of the year so far
    does: those days are left as they are, its rows booked in them are only compared with
    what is stored (`Staged.differences`), and the rest is staged from the day after the
    last one committed. One that starts before what is committed of an account and reaches
    into it is refused, as is a certificate of a day another committed certificate states.
    """
    owner, statements, people = batch.owner, batch.statements, batch.people
    over = now.date() - timedelta(days=1)  # the last day that is over
    with db.transaction(conn):
        owner_id = store.ensure_owner(conn, owner)
        _refuse_other_owners(conn, owner, statements, people)
        store.seed_categories(conn, batch.categories)
        others = {
            store.ensure_owner(conn, name): patterns
            for name, patterns in people.items()
            if name != owner
        }

        by_file: dict[str, list[Transaction]] = {s.filename: [] for s in statements}
        for txn in batch.month.transactions:
            by_file[txn.source_file].append(txn)
        accounts = {
            s.filename: _accounts(conn, owner_id, s, by_file[s.filename]) for s in statements
        }
        _refuse_duplicates(statements, accounts)
        _refuse_shared_accounts(conn, statements, accounts)
        parts = {
            s.filename: _part(conn, s, accounts[s.filename], by_file[s.filename], over)
            for s in statements
        }
        rows = [(s.source, txn) for s in statements for txn in by_file[s.filename]]
        # Ids come from all the rows: the counter among identical ones must not depend on
        # which days are committed or over.
        numbered = list(zip(transaction_ids(owner, rows), (txn for _, txn in rows), strict=True))
        new: list[tuple[str, Transaction]] = []
        old: dict[tuple[str, str], list[tuple[str, Transaction]]] = defaultdict(list)
        later = 0
        for txn_id, txn in numbered:
            part = parts[txn.source_file]
            start = part.starts[txn.account]
            if txn.booking_date > part.period[1]:
                later += 1
            elif start is not None and txn.booking_date >= start:
                new.append((txn_id, txn))
            else:
                old[txn.source_file, txn.account].append((txn_id, txn))
        already = sum(len(found) for found in old.values())
        differences: list[str] = []
        for (filename, account), found in old.items():
            part = parts[filename]
            unknown, absent = _compare(conn, part, account, found)
            already -= len(unknown)
            differences += [*unknown, *absent]

        staged = [part for part in parts.values() if part.staged]
        replaced: set[int] = set()
        for part in staged:
            for name, account_id in part.accounts.items():
                if (start := part.starts[name]) is not None:
                    span = (start, part.period[1])
                    replaced |= store.delete_statement_rows(
                        conn, part.statement.source, [account_id], span
                    )
        files = {part.statement.filename: _store_file(conn, owner_id, part, now) for part in staged}

        leaves = store.leaf_categories(conn)
        for txn_id, txn in new:
            store.insert_transaction(
                conn,
                txn_id,
                txn,
                account=accounts[txn.source_file][txn.account],
                category=store.category_for(leaves, txn.category, txn.amount),
                counterparty_owner=_counterparty_owner(txn, others),
                statement_file=files[txn.source_file],
            )
        for part in staged:
            _store_balances(conn, part, files[part.statement.filename])
            _store_valuation(conn, part, files[part.statement.filename])
        stored, dropped, unchanged = _store_certificates(conn, owner_id, batch.certificates, now)
        replaced |= dropped

        store.drop_unused_files(conn, replaced - set(files.values()) - stored)
        gaps = tuple(str(gap) for gap in _unsettled(conn, owner_id))
        store.apply_overrides(conn)
        pairs.mark(conn)
        nothing = [part.statement.filename for part in parts.values() if not part.staged]
        return Staged(
            files=tuple(sorted({*files.values(), *stored})),
            transactions=len(new),
            uncategorized=store.uncategorized(conn, owner_id),
            gaps=gaps,
            already=already,
            differences=tuple(differences),
            later=later,
            nothing_new=tuple(sorted([*nothing, *unchanged])),
        )


def _refuse_other_owners(
    conn: sqlite3.Connection, owner: str, statements: Sequence[Statement], people: People
) -> None:
    """A statement whose account number was imported for another person, or that the
    patterns of another person recognise (an IBAN in `owners` of the config), is theirs."""
    for statement in statements:
        number = statement.extraction.account_id
        if not number:
            continue
        other = store.owner_of_number(conn, number)
        if other is None and not any(p.search(number) for p in people.get(owner, ())):
            other = next(
                (
                    name
                    for name, patterns in people.items()
                    if name != owner and any(p.search(number) for p in patterns)
                ),
                None,
            )
        if other is not None and other != owner:
            msg = f"{statement.filename} is of {other}'s account {number}, not of {owner}'s"
            raise OtherOwnerError(msg)


def _part(
    conn: sqlite3.Connection,
    statement: Statement,
    accounts: Mapping[str, int],
    rows: Sequence[Transaction],
    over: date,
) -> _Part:
    """What of the statement is new: each account from the day after what is committed of it,
    up to the last day that is over."""
    extraction = statement.extraction
    first, last = extraction.period
    balances = extraction.balances
    if extraction.balances_on_export and last <= over:
        msg = (
            f"{statement.filename} has no bookings and runs to {last}: its balance is the one when "
            f"it was exported, which may have changed after {last}. Export it up to today"
        )
        raise StagingError(msg)
    if last > over:
        if extraction.holdings is not None or extraction.values:
            msg = (
                f"{statement.filename} runs to {last}, which is not over yet, and says what was "
                f"held on that day: export it up to {over}"
            )
            raise StagingError(msg)
        moved: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for txn in rows:
            if txn.booking_date > over:
                moved[txn.account, txn.currency] += txn.amount
        balances = tuple(
            replace(b, closing=b.closing - moved[b.account, b.currency]) for b in balances
        )
        last = over
    starts = {
        name: _start(conn, statement, name, account, (first, last)) if first <= last else None
        for name, account in accounts.items()
    }
    return _Part(statement, accounts, (first, last), balances, starts)


def _start(
    conn: sqlite3.Connection,
    statement: Statement,
    name: str,
    account: int,
    period: tuple[date, date],
) -> date | None:
    """The first day of the account to stage from the statement; None when all are committed."""
    first, last = period
    span = store.committed_span(conn, statement.source, account)
    if span is None or last < span[0] or first > span[1]:
        return first
    low, high = span
    if first < low:
        msg = (
            f"{statement.filename} covers {name} from {first}, before what is committed of it "
            f"({low} to {high}), and reaches into it: export it up to {low - timedelta(days=1)}"
        )
        raise CommittedError(msg)
    return None if last <= high else high + timedelta(days=1)


def _store_file(conn: sqlite3.Connection, owner_id: int, part: _Part, now: datetime) -> int:
    statement = part.statement
    if store.is_committed(conn, owner_id, statement.sha256):
        # The same bytes, committed when their last days were not over: the days staged
        # now would be counted as committed under the file committed then.
        msg = (
            f"{statement.filename} is committed up to the day before it was imported: "
            "download it again to import the days after"
        )
        raise CommittedError(msg)
    file_id = store.upsert_statement_file(
        conn,
        owner_id,
        source=statement.source,
        filename=statement.filename,
        sha256=statement.sha256,
        period=(part.first, part.period[1]),
        reconciled="yes" if _has_figures(statement) else "nothing_to_check",
        imported_at=db.timestamp(now),
        holdings=statement.extraction.holdings is not None,
        account_number=statement.extraction.account_id,
    )
    staged = [part.accounts[name] for name, start in part.starts.items() if start is not None]
    store.link_accounts(conn, file_id, staged)
    return file_id


def _store_balances(conn: sqlite3.Connection, part: _Part, file_id: int) -> None:
    """The balances the statement closes on and, for an account it covers from its first day,
    the one it started from. An account it covers from a later day starts from what is
    committed: the chain from there to its closing balance is what is checked."""
    first, last = part.period
    for balance in part.balances:
        start = part.starts[balance.account]
        if start is None:
            continue
        account = part.accounts[balance.account]
        store.set_closing_balance(conn, account, last, balance.closing, file_id)
        if start == first:
            before = first - timedelta(days=1)
            store.set_opening_balance(conn, account, before, balance.opening, file_id)


def _compare(
    conn: sqlite3.Connection, part: _Part, account: str, old: Sequence[tuple[str, Transaction]]
) -> tuple[list[str], list[str]]:
    """How the statement's rows booked on committed days of the account differ from what is
    stored there: the rows of the statement that are not stored, and the stored rows it does
    not have.

    A row is the same row when its id is (D15), or when it happened on the same day with the
    same amount and text: an older import may have booked it on another day (before
    2026-10-16 a Migros row was booked on its value date and a Revolut row on the day it
    started). What is stored is looked at from the day of the statement's first row: an
    export may begin in the middle of what is committed.
    """
    statement = part.statement
    start = part.starts[account]
    first = min(txn.booking_date for _, txn in old)
    last = max(txn.booking_date for _, txn in old) if start is None else start - timedelta(days=1)
    window = (first - _MOVED, last + _MOVED)
    stored = store.statement_rows(conn, statement.source, [part.accounts[account]], window)
    found = {txn_id for txn_id, _ in old}
    unmatched = [(txn_id, txn) for txn_id, txn in old if txn_id not in stored]
    absent = {
        txn_id: row
        for txn_id, row in stored.items()
        if txn_id not in found and first <= row.booked <= last
    }
    loose = Counter(row.same for txn_id, row in stored.items() if txn_id not in found)
    unknown: list[str] = []
    for _, txn in unmatched:
        same = (txn.date.isoformat(), txn.currency, to_nano(txn.amount), txn.description)
        if loose[same]:
            loose[same] -= 1
            continue
        unknown.append(
            f"{txn.account} {txn.booking_date}: {txn.description} ({txn.amount} "
            f"{txn.currency}) is in {statement.filename} and is not among the committed rows"
        )
    moved = Counter(
        (txn.date.isoformat(), txn.currency, to_nano(txn.amount), txn.description)
        for _, txn in unmatched
    )
    missing: list[str] = []
    for row in absent.values():
        if moved[row.same]:
            moved[row.same] -= 1
            continue
        missing.append(f"{row.text} is committed and is not in {statement.filename}")
    return unknown, missing


def _store_certificates(
    conn: sqlite3.Connection, owner_id: int, certificates: Sequence[Certificate], now: datetime
) -> tuple[set[int], set[int], list[str]]:
    """Give each certificate's account the balance it states for its day.

    A certificate read again, or a new scan of it, replaces what it stored while it is
    staged; once committed, the same file read again changes nothing and another one of that
    day is refused. One of another day adds a balance and leaves the others. The balance its
    year began with goes on that earlier day unless the certificate of that day is stored,
    which keeps it; two that state a day differently are refused. Returns the files now
    stored, the files whose balances were replaced and the certificates already committed.
    """
    seen: dict[tuple[str, date], str] = {}
    stored: set[int] = set()
    replaced: set[int] = set()
    unchanged: list[str] = []
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
        committed = store.committed_file(conn, stated.source, account, stated.as_of)
        if committed == certificate.sha256:
            unchanged.append(certificate.filename)
            continue
        if committed is not None:
            msg = (
                f"a certificate of {certificate.account} for {stated.as_of} is committed: "
                f"take it back before {certificate.filename} replaces it"
            )
            raise CommittedError(msg)
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
        store.link_accounts(conn, file_id, [account])
        days = [(stated.as_of, stated.balance), *([stated.opened] if stated.opened else [])]
        for on, balance in days:  # what is left on a day now is another certificate's
            if store.typed_on(conn, account, on):
                # Never replaced: a discarded certificate would take the typed figure with it.
                msg = (
                    f"{certificate.filename} states {certificate.account} on {on}, for which a "
                    f"figure was typed: remove it with `finanze balance --remove "
                    f'"{certificate.account}" {on}`, then import it again'
                )
                raise CommittedError(msg)
            standing = store.balance_on(conn, account, on, stated=True)
            if standing is not None and standing != balance:
                raise CertificatesDisagreeError(
                    certificate.account, stated.currency, on, balance, standing
                )
        store.set_closing_balance(conn, account, stated.as_of, stated.balance, file_id)
        if stated.opened:
            store.set_opening_balance(conn, account, *stated.opened, file_id)
        stored.add(file_id)
    return stored, replaced, unchanged


def _store_valuation(conn: sqlite3.Connection, part: _Part, file_id: int) -> None:
    """What the statement says its accounts held and were worth at the end of its period.

    A statement that says so is never cut short (`_part`), so its period ends where it says.
    """
    extraction, last = part.statement.extraction, part.period[1]
    store.set_statement_values(conn, file_id, extraction.values)
    for rate in extraction.rates:
        if rate.base == _RATES_IN:  # the only rates the database keeps
            store.set_rate(conn, last, rate.currency, rate.rate, source="statement")
    for holding in extraction.holdings or ():
        if part.starts[holding.account] is not None:
            store.set_holding(conn, part.accounts[holding.account], last, holding, file_id)


def commit(conn: sqlite3.Connection, *, owner: str, now: datetime) -> list[store.StatementFile]:
    """Make every statement staged for `owner` visible to the dashboards; returns them.

    Refused while a staged row has no category, or while a statement balance does not follow
    from the one before it (a missing statement), or holdings do not.
    """
    with db.transaction(conn):
        owner_id = store.owner_id(conn, owner)
        files = [] if owner_id is None else store.statement_files(conn, owner_id, staged=True)
        if owner_id is None or not files:
            return []
        pending = store.uncategorized(conn, owner_id)
        if pending:
            msg = f"{pending} staged transactions still need a category"
            raise UncategorizedRowsError(msg)
        for gap in _unsettled(conn, owner_id):
            raise gap
        store.commit_files(conn, [f.id for f in files], db.timestamp(now))
        return files


def uncommit(conn: sqlite3.Connection, *, owner: str, file_id: int) -> store.StatementFile:
    """Take a committed statement back: it is staged again, to be removed or committed.

    Only one at either end of what is committed of its accounts: one in between would leave
    a hole in what the dashboards see.
    """
    with db.transaction(conn):
        found = _file_of(conn, owner, file_id)
        if found.committed_at is None:
            msg = f"{found.filename} is not committed"
            raise StagingError(msg)
        around = store.later_committed(conn, file_id)
        if around:
            msg = (
                f"{found.filename} is between other committed statements ({', '.join(around)}): "
                "take back the later ones first"
            )
            raise CommittedError(msg)
        store.uncommit_file(conn, file_id)
        return found


def discard(conn: sqlite3.Connection, *, owner: str, file_id: int) -> store.StatementFile:
    """Remove a staged statement and everything it staged. Its original file is kept."""
    with db.transaction(conn):
        found = _file_of(conn, owner, file_id)
        if found.committed_at is not None:
            msg = f"{found.filename} is committed: take it back first"
            raise CommittedError(msg)
        owner_id = store.ensure_owner(conn, owner)
        store.delete_file(conn, file_id)
        _unsettled(conn, owner_id)  # the derived rows, without what it gave
        pairs.mark(conn)
        return found


def _file_of(conn: sqlite3.Connection, owner: str, file_id: int) -> store.StatementFile:
    owner_id = store.owner_id(conn, owner)
    files = [] if owner_id is None else store.statement_files(conn, owner_id)
    found = next((f for f in files if f.id == file_id), None)
    if found is None:
        msg = f"{owner} has no statement {file_id}"
        raise NothingStagedError(msg)
    return found


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
    conn: sqlite3.Connection, database: Path, *, owner: str, now: datetime
) -> tuple[list[store.StatementFile], Path | None]:
    """Commit what is staged for `owner` after saving a copy of the database next to it.

    Returns the statements committed and the copy, None when nothing was staged. The copy is
    dropped when nothing changed, including when the commit is refused.
    """
    copy = copy_before(conn, database, now)
    try:
        done = commit(conn, owner=owner, now=now)
    except BaseException:
        copy.unlink()
        raise
    if not done:
        copy.unlink()
        return done, None
    return done, copy


@dataclass(frozen=True, slots=True)
class Remember:
    """Where a choice of category is saved as a rule, as `--review` does (plan 5.4)."""

    shared: Path  # the rules both people share
    own: Path  # the owner's rules, for rows that name a person
    people: Patterns  # what recognises a person, anybody
    # What recognises the people other than the owner: on import a rule giving a transfer
    # category does not apply to a row naming one of them (`classification.between_people`).
    others: Patterns = ()


@dataclass(frozen=True, slots=True)
class Choice:
    """One category for some rows, as `categorize` gives it."""

    ids: Collection[str]
    category: str
    rule: bool = False  # remembered as a rule, where there is a place for rules
    change: bool = False  # the rows have a category, which was wrong


def categorize(  # noqa: PLR0913  # whose rows, which, what for, and how it is remembered
    conn: sqlite3.Connection,
    *,
    owner: str,
    ids: Collection[str],
    category: str,
    now: datetime,
    remember: Remember | None,
    change: bool = False,
) -> int:
    """Give `category` to staged rows of `owner` that have none. Returns how many.

    With `remember` the choice also becomes a rule for each counterparty and direction among
    the rows, so the next import makes it again. Without, it is kept as an override of these
    rows alone. A transfer category makes an income or expense a transfer, as a rule would.
    Rule files are left as they were when anything fails.

    With `change` the rows are instead ones that have a category, which was wrong, staged or
    committed (the dashboards follow at once): money in or out, nothing a statement
    categorizes itself. The choice is always kept as an override
    of these rows, and a rule to remember it goes before the rules already there, the wrong
    one among them. A transfer given another kind of category is income or an expense again.
    """
    choice = Choice(ids, category, rule=remember is not None, change=change)
    return categorize_all(conn, owner=owner, choices=[choice], now=now, remember=remember)


def categorize_all(
    conn: sqlite3.Connection,
    *,
    owner: str,
    choices: Sequence[Choice],
    now: datetime,
    remember: Remember | None,
) -> int:
    """Make each choice as `categorize` does, all together: when one cannot be made, none is,
    and the rule files are left as they were. A choice to remember is remembered in
    `remember`, and kept for its rows alone without it. Returns how many rows were given one.
    """
    with _rules_kept() as before, db.transaction(conn):
        return sum(
            _categorize(
                conn,
                before,
                owner=owner,
                choice=choice,
                now=now,
                remember=remember if choice.rule else None,
            )
            for choice in choices
        )


def _categorize(  # noqa: PLR0913  # categorize_all's arguments, with the rule files' state
    conn: sqlite3.Connection,
    before: dict[Path, str | None],
    *,
    owner: str,
    choice: Choice,
    now: datetime,
    remember: Remember | None,
) -> int:
    ids, category, change = choice.ids, choice.category, choice.change
    owner_id = store.owner_id(conn, owner)
    rows = [] if owner_id is None else store.waiting(conn, owner_id, ids, change=change)
    if not rows or len(rows) != len(set(ids)):
        state = "cannot be given another category" if change else _NOT_WAITING
        msg = f"those rows {state}: load the page again"
        raise StagingError(msg)
    leaves = store.leaf_categories(conn)
    flows = {kind.value for kind in _BETWEEN_OWNERS}
    rules: dict[tuple[str, str], Path] = {}
    for row in rows:
        chosen = store.category_for(leaves, category, from_nano(row.amount_nano))
        if chosen is None:
            msg = "no category was chosen"
            raise StagingError(msg)
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
            raise StagingError(msg)
        text = f"{row.description}\n{row.counterparty}"
        if transfer and any(pattern.search(text) for pattern in remember.others):
            msg = (
                f"a rule cannot make money to or from another person a transfer ({name}): "
                "choose 'just these rows'"
            )
            raise StagingError(msg)
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
        raise StagingError(msg)
    if side not in categories.SIDES:
        msg = "a category is added to the expenses or to the income"
        raise StagingError(msg)
    with _rules_kept() as before, db.transaction(conn):
        extras = categories.load(file)
        store.seed_categories(conn, extras)
        leaves = store.leaf_categories(conn)
        if (side, name) in leaves or ("transfer", name) in leaves:
            msg = f"there is already a category {name!r}"
            raise StagingError(msg)
        if side == "uscite" and group and group not in categories.groups(extras):
            msg = f"no group of categories named {group!r}"
            raise StagingError(msg)
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
    use, and can never also be fed from statements. A pension fund's account, fed by its
    certificates, takes typed balances too (decided 2026-11-21): what the fund shows online
    between two certificates, from its first certificate's day on. A day a certificate states
    cannot be typed; and a certificate of a day typed is refused until that figure is removed
    (`remove_typed`), so that discarding it never takes the figure with it.
    """
    with db.transaction(conn):
        owner_id = store.ensure_owner(conn, owner)
        found = store.account_of(conn, owner_id, account)
        source = found.source if found is not None and found.kind == _CARRIED_FLAT else None
        account_id = store.ensure_account(
            conn, owner_id, name=account, kind=_CARRIED_FLAT, currency=currency, source=source
        )
        if source is not None:
            _refuse_certified_day(conn, account, account_id, day, currency)
        store.set_typed_balance(conn, account_id, day, amount)


def _refuse_certified_day(
    conn: sqlite3.Connection, name: str, account: int, day: date, currency: str
) -> None:
    """A pension fund's figure is typed only between its certificates' days, from the first."""
    stated = store.balance_on(conn, account, day, stated=True)
    if stated is not None:
        msg = (
            f"{name} has a certificate for {day}, which says {stated:,.2f} {currency}: type "
            "the figures of other days"
        )
        raise store.AccountMismatchError(msg)
    first = store.first_stated_day(conn, account)
    if first is not None and day < first:
        msg = f"{name} counts from its first certificate's day, {first}: type a later day"
        raise store.AccountMismatchError(msg)


def remove_typed(
    conn: sqlite3.Connection, *, owner: str, account: str, day: date, symbol: str = ""
) -> None:
    """Remove the balance typed for `account` on `day`, or what was typed of `symbol` then.

    An account only ever typed that is left with nothing is removed too, so that its name can
    be fed by statements or certificates after all.
    """
    with db.transaction(conn):
        owner_id = store.owner_id(conn, owner)
        found = None if owner_id is None else store.account_of(conn, owner_id, account)
        removed = found is not None and (
            store.remove_typed_holding(conn, found.id, symbol, day)
            if symbol
            else store.remove_typed_balance(conn, found.id, day)
        )
        if not removed or found is None:
            what = f"{symbol} " if symbol else ""
            msg = f"nothing {what}was typed for {account} of {owner} on {day}"
            raise NothingStagedError(msg)
        if found.source is None:
            store.drop_if_empty(conn, found.id)


def type_holding(
    conn: sqlite3.Connection,
    *,
    owner: str,
    account: str,
    day: date,
    holding: TypedHolding,
) -> None:
    """Record how many of one instrument an account without statements held at the end of
    `day`: shares held at a broker that has no statement this reads (decided 2026-11-21).

    The account is a typed one, as `type_balance` makes, and never has statements. Made by
    this, it is in the instrument's currency; it may hold instruments in other currencies
    too. What it holds of the instrument counts from the first day typed, at each day's close,
    and stays until another quantity is typed: the other instruments are not touched.
    """
    if not holding.quantity.is_finite() or holding.quantity < 0:
        msg = f"{holding.quantity} is no number of shares held"
        raise StagingError(msg)
    with db.transaction(conn):
        owner_id = store.ensure_owner(conn, owner)
        found = store.account_of(conn, owner_id, account)
        if found is not None and found.source is None and found.kind == "manual":
            account_id = found.id
        else:
            account_id = store.ensure_account(
                conn, owner_id, name=account, kind="manual", currency=holding.currency, source=None
            )
        store.set_typed_holding(conn, account_id, day, holding)


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

    This is what ties statements together. Two statements that disagree raise at once. A balance
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


# Sources whose account number names one account for good (not a card's, renewed).
_NUMBERED = frozenset({"ibkr", "migros"})


def _has_figures(statement: Statement) -> bool:
    """Whether the statement prints figures its rows were checked against. The balance of an
    export without bookings is stored, but nothing in it was checked."""
    extraction = statement.extraction
    return bool(extraction.balances or extraction.controls) and not extraction.balances_on_export


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


def _refuse_shared_accounts(
    conn: sqlite3.Connection,
    statements: Sequence[Statement],
    accounts: Mapping[str, Mapping[str, int]],
) -> None:
    """Statements of two account numbers of one bank that land on the same account (a savings
    account named like the private one) would mix their bookings and balances: refused, among
    the statements of the import and against the committed ones. Only sources
    whose number names one account for good are checked: a card's number changes when it is
    renewed."""
    printed: dict[int, set[str]] = {}
    for statement in statements:
        number = statement.extraction.account_id
        if statement.source not in _NUMBERED or not number:
            continue
        for name, account in accounts[statement.filename].items():
            if account not in printed:
                printed[account] = store.numbers_of(conn, account)
            known = printed[account]
            if known - {number}:
                msg = (
                    f"{statement.filename} is of account number {number}, and {name} already "
                    f"holds the statements of another: give the number a name of its own in "
                    f"account_labels of finanze.yaml"
                )
                raise DuplicateStatementError(msg)
            known.add(number)


def _counterparty_owner(txn: Transaction, others: Mapping[int, Patterns]) -> int | None:
    if txn.kind not in _BETWEEN_OWNERS:
        return None
    return next((other for other, patterns in others.items() if names(txn, patterns)), None)
