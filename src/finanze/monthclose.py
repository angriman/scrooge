"""Month close: stage a reconciled month in the database, then commit it (D16, D17).

`stage` stores what the pipeline produced for one person and one month, replacing whatever an
earlier import of the same statements stored. The month stays `open`, and the dashboards do
not see it, until `commit`. Both run in one database transaction: any failure leaves the
database as it was.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import pairwise

from finanze.extractors.common import month_end
from finanze.models import Kind, Transaction
from finanze.pipeline import Month, Statement
from finanze.storage import bills, db, store
from finanze.storage.ids import transaction_ids
from finanze.storage.money import from_micro
from finanze.validation import ChainMismatchError, MissingStatementError, ReconciliationError

# Sources whose rows are booked on an account that another source's statement reconciles:
# card purchases on the bank account (D3), savings interest on the Revolut account (D8).
_BOOKED_ON = {"swisscard": "migros", "revolut_savings": "revolut"}
_BROKERS = frozenset({"ibkr", "tastytrade"})
# Only what is income or expense for one person can be internal to the family (D14).
_BETWEEN_OWNERS = frozenset({Kind.EXPENSE, Kind.INCOME})

Patterns = Sequence[re.Pattern[str]]
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
    """Two statements of one source cover the same account: one would be counted twice."""


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


@dataclass(frozen=True, slots=True)
class Staged:
    """What `stage` stored."""

    month: date
    transactions: int
    uncategorized: int
    # What an earlier statement would settle: it blocks the commit, not the import.
    gaps: tuple[str, ...] = ()


def stage(conn: sqlite3.Connection, batch: Batch, now: datetime) -> Staged:
    """Store the month for its owner, replacing an earlier import of the same statements."""
    owner, statements, month, people = batch.owner, batch.statements, batch.month, batch.people
    period = (month.month, month_end(month.month))
    with db.transaction(conn):
        owner_id = store.ensure_owner(conn, owner)
        if store.month_status(conn, owner_id, month.month) == "committed":
            msg = f"{month.month:%Y-%m} is committed for {owner}: reopen it first"
            raise MonthCommittedError(msg)
        store.set_month(conn, owner_id, month.month, "open", None)
        store.seed_categories(conn)
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
            replaced |= store.delete_statement_rows(conn, statement.source, ids, period)
        files = {
            s.filename: store.upsert_statement_file(
                conn,
                owner_id,
                source=s.source,
                filename=s.filename,
                sha256=s.sha256,
                period=period,
                reconciled="yes" if _has_figures(s) else "nothing_to_check",
                imported_at=db.timestamp(now),
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
            for balance in statement.extraction.balances:
                account = accounts[statement.filename][balance.account]
                file_id = files[statement.filename]
                before = period[0] - timedelta(days=1)
                store.set_closing_balance(conn, account, period[1], balance.closing, file_id)
                store.set_opening_balance(conn, account, before, balance.opening, file_id)

        store.drop_unused_files(conn, replaced - set(files.values()))
        gaps = tuple(str(gap) for gap in _unsettled(conn, owner_id))
        store.apply_overrides(conn)
        pending = store.uncategorized(conn, owner_id, period)
        return Staged(month.month, len(rows), pending, gaps)


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


def reopen(conn: sqlite3.Connection, *, owner: str, month: date) -> None:
    """Take a committed month back to `open`, so that it can be imported again."""
    with db.transaction(conn):
        owner_id, _ = _month(conn, owner, month)
        store.set_month(conn, owner_id, month, "open", None)


def _unsettled(conn: sqlite3.Connection, owner_id: int) -> list[ReconciliationError]:
    """Bring the derived rows up to date; returns what only an earlier statement can settle."""
    return [*check_chain(conn, owner_id), *bills.split(conn, owner_id)]


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
        reached = earlier.amount_micro + store.own_flow(conn, later.account_id, *span)
        if reached == later.amount_micro:
            continue
        expected, actual = from_micro(later.amount_micro), from_micro(reached)
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
    source = _BOOKED_ON.get(statement.source, statement.source)
    kind = "broker_cash" if source in _BROKERS else "bank"
    return {
        name: store.ensure_account(
            conn, owner_id, name=name, kind=kind, currency=currency, source=source
        )
        for name, currency in sorted(named)
    }


def _refuse_duplicates(
    statements: Sequence[Statement], accounts: Mapping[str, Mapping[str, int]]
) -> None:
    seen: dict[tuple[str, str], str] = {}
    for statement in statements:
        for account in accounts[statement.filename]:
            first = seen.setdefault((statement.source, account), statement.filename)
            if first != statement.filename:
                both = f"{first} and {statement.filename}"
                msg = f"{both} are both {statement.source} statements of {account}"
                raise DuplicateStatementError(msg)


def _counterparty_owner(txn: Transaction, others: Mapping[int, Patterns]) -> int | None:
    if txn.kind not in _BETWEEN_OWNERS:
        return None
    text = f"{txn.description}\n{txn.counterparty}"
    for other, patterns in others.items():
        if any(pattern.search(text) for pattern in patterns):
            return other
    return None
