"""The SQL behind the month-close service: small typed functions over one connection.

Nothing here begins or commits a transaction, and nothing decides anything: callers do both.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finanze import categories
from finanze.models import SecurityTrade, Transaction
from finanze.storage.money import to_nano, to_text

_CURRENCY_DECIMALS = 2
_INSTRUMENT_TYPES = {"ETFs": "etf", "Stocks": "stock", "Bonds": "bill", "Funds": "fund"}
_SIDES = ("uscite", "entrate")


class UnknownCategoryError(Exception):
    """A transaction names a category that is not in the taxonomy."""


class AccountMismatchError(Exception):
    """An account already exists with another currency, or is fed another way, under this name."""


@dataclass(frozen=True, slots=True)
class Anchor:
    """A statement balance: what an account held at the end of `day`."""

    account_id: int
    account: str
    currency: str
    day: date
    amount_nano: int
    opening: bool  # only known as what the next statement started from: nothing covers `day`


def ensure_owner(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO owners (name) VALUES (?)", (name,))
    return int(conn.execute("SELECT id FROM owners WHERE name = ?", (name,)).fetchone()[0])


def owner_id(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute("SELECT id FROM owners WHERE name = ?", (name,)).fetchone()
    return None if row is None else int(row[0])


def ensure_account(  # noqa: PLR0913  # one argument per column
    conn: sqlite3.Connection,
    owner: int,
    *,
    name: str,
    kind: str,
    currency: str,
    source: str | None,
) -> int:
    """The account's id, creating it on first sight. `source` is None for a typed account."""
    conn.execute(
        "INSERT OR IGNORE INTO currencies (code, decimals) VALUES (?, ?)",
        (currency, _CURRENCY_DECIMALS),
    )
    conn.execute(
        "INSERT OR IGNORE INTO accounts (owner_id, name, kind, currency, source) "
        "VALUES (?, ?, ?, ?, ?)",
        (owner, name, kind, currency, source),
    )
    row = conn.execute(
        "SELECT id, currency, source FROM accounts WHERE owner_id = ? AND name = ?", (owner, name)
    ).fetchone()
    if row["currency"] != currency:
        msg = f"account {name!r} is in {row['currency']}, not {currency}"
        raise AccountMismatchError(msg)
    if (row["source"] is None) != (source is None):
        msg = f"account {name!r} cannot have both statements and typed balances"
        raise AccountMismatchError(msg)
    return int(row["id"])


def seed_categories(conn: sqlite3.Connection, extras: Sequence[categories.Extra] = ()) -> None:
    """Make sure the agreed taxonomy (D6) and what `categories.yaml` adds are in the database.

    Existing rows are left alone.
    """
    insert = (
        "INSERT OR IGNORE INTO categories (name, parent_id, side, sort_order) VALUES (?, ?, ?, ?)"
    )
    uscite = {parent: list(leaves) for parent, leaves in categories.USCITE.items()}
    for extra in extras:
        if extra.side == "uscite":
            uscite.setdefault(extra.group, []).append(extra.name)
    order = 0
    for parent, leaves in uscite.items():
        conn.execute(insert, (parent, None, "uscite", order))
        parent_id = conn.execute(
            "SELECT id FROM categories WHERE side = 'uscite' AND parent_id IS NULL AND name = ?",
            (parent,),
        ).fetchone()[0]
        for leaf in leaves:
            order += 1
            conn.execute(insert, (leaf, parent_id, "uscite", order))
        order += 1
    entrate = (*categories.ENTRATE, *(e.name for e in extras if e.side == "entrate"))
    for side, names in (("entrate", entrate), ("transfer", categories.TRANSFERS)):
        for name in names:
            order += 1
            conn.execute(insert, (name, None, side, order))


def leaf_categories(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """(side, name) -> id of every category a transaction can carry."""
    rows = conn.execute(
        "SELECT id, side, name FROM categories WHERE side <> 'uscite' OR parent_id IS NOT NULL"
    ).fetchall()
    return {(row["side"], row["name"]): int(row["id"]) for row in rows}


def category_for(leaves: dict[tuple[str, str], int], name: str, amount: Decimal) -> int | None:
    """The category `name` on the side the sign points to, or on the other one.

    Several names exist on both sides (Trading, Commissioni, Investimenti): the sign decides,
    as in D1. A refund keeps its expense category although it is money coming in.
    """
    if not name:
        return None
    if ("transfer", name) in leaves:
        return leaves["transfer", name]
    for side in _SIDES if amount < 0 else _SIDES[::-1]:
        if (side, name) in leaves:
            return leaves[side, name]
    msg = f"unknown category {name!r}"
    raise UnknownCategoryError(msg)


def month_status(conn: sqlite3.Connection, owner: int, month: date) -> str | None:
    row = conn.execute(
        "SELECT status FROM months WHERE owner_id = ? AND month = ?", (owner, month.isoformat())
    ).fetchone()
    return None if row is None else str(row[0])


def set_month(
    conn: sqlite3.Connection, owner: int, month: date, status: str, committed_at: str | None
) -> None:
    conn.execute(
        "INSERT INTO months (owner_id, month, status, committed_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (owner_id, month) DO UPDATE SET status = excluded.status, "
        "committed_at = excluded.committed_at",
        (owner, month.isoformat(), status, committed_at),
    )


def upsert_statement_file(  # noqa: PLR0913  # one argument per column
    conn: sqlite3.Connection,
    owner: int,
    *,
    source: str,
    filename: str,
    sha256: str,
    period: tuple[date, date],
    reconciled: str,
    imported_at: str,
) -> int:
    """The row of this exact file; importing the same bytes again keeps its id and timestamp."""
    row = conn.execute(
        "INSERT INTO statement_files "
        "(owner_id, source, filename, sha256, period_start, period_end, reconciled, imported_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (owner_id, sha256) DO UPDATE SET source = excluded.source, "
        "filename = excluded.filename, period_start = excluded.period_start, "
        "period_end = excluded.period_end, reconciled = excluded.reconciled "
        "RETURNING id",
        (
            owner,
            source,
            filename,
            sha256,
            period[0].isoformat(),
            period[1].isoformat(),
            reconciled,
            imported_at,
        ),
    ).fetchone()
    return int(row[0])


def delete_statement_rows(
    conn: sqlite3.Connection, source: str, accounts: Collection[int], period: tuple[date, date]
) -> set[int]:
    """Remove what earlier imports of `source` stored for these accounts in this period.

    Transactions go by booking date, which always lies inside the statement period; balances
    go with the earlier file of the same period. Returns the files those rows came from.
    """
    marks = ", ".join("?" * len(accounts))
    first, last = period[0].isoformat(), period[1].isoformat()
    transactions = (
        f"FROM transactions WHERE account_id IN ({marks}) AND booking_date BETWEEN ? AND ? "  # noqa: S608  # only "?" marks are interpolated
        "AND statement_file_id IN (SELECT id FROM statement_files WHERE source = ?)"
    )
    balances = (
        f"FROM balances WHERE account_id IN ({marks}) AND statement_file_id IN "  # noqa: S608  # only "?" marks are interpolated
        "(SELECT id FROM statement_files WHERE source = ? AND period_start = ? AND period_end = ?)"
    )
    touched: set[int] = set()
    for rows, args in (
        (transactions, (*accounts, first, last, source)),
        (balances, (*accounts, source, first, last)),
    ):
        found = conn.execute(f"SELECT DISTINCT statement_file_id {rows}", args).fetchall()
        touched |= {int(row[0]) for row in found}
        conn.execute(f"DELETE {rows}", args)
    return touched


def drop_unused_files(conn: sqlite3.Connection, files: Iterable[int]) -> None:
    """Forget statement files that nothing points to any more (a re-export replaced them)."""
    for file_id in files:
        conn.execute(
            "DELETE FROM statement_files WHERE id = ? "
            "AND NOT EXISTS (SELECT 1 FROM transactions WHERE statement_file_id = ?1) "
            "AND NOT EXISTS (SELECT 1 FROM balances WHERE statement_file_id = ?1) "
            "AND NOT EXISTS (SELECT 1 FROM positions WHERE statement_file_id = ?1)",
            (file_id,),
        )


def insert_transaction(  # noqa: PLR0913  # the row plus what only the caller knows
    conn: sqlite3.Connection,
    txn_id: str,
    txn: Transaction,
    *,
    account: int,
    category: int | None,
    counterparty_owner: int | None,
    statement_file: int | None,
) -> None:
    conn.execute(
        "INSERT INTO transactions (id, account_id, date, booking_date, amount_nano, currency, "
        "description, counterparty, kind, category_id, category_source, counterparty_owner_id, "
        "statement_file_id, source_ref, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            txn_id,
            account,
            txn.date.isoformat(),
            txn.booking_date.isoformat(),
            to_nano(txn.amount),
            txn.currency,
            txn.description,
            txn.counterparty,
            txn.kind.value,
            category,
            None if category is None else "rule",
            counterparty_owner,
            statement_file,
            txn.source_ref,
            to_text(txn.confidence),
        ),
    )
    if isinstance(txn, SecurityTrade):
        _insert_trade(conn, txn_id, txn)


def _insert_trade(conn: sqlite3.Connection, txn_id: str, trade: SecurityTrade) -> None:
    maturity = None if trade.maturity is None else trade.maturity.isoformat()
    instrument = conn.execute(
        "INSERT INTO instruments (symbol, type, currency, maturity) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (symbol, currency) DO UPDATE SET type = excluded.type, "
        "maturity = coalesce(excluded.maturity, maturity) RETURNING id",
        (trade.ticker, _INSTRUMENT_TYPES[trade.asset_tab], trade.currency, maturity),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trades (transaction_id, instrument_id, quantity, price, cost_nano) "
        "VALUES (?, ?, ?, ?, ?)",
        (txn_id, instrument, to_text(trade.quantity), to_text(trade.price), to_nano(trade.cost)),
    )


def apply_overrides(conn: sqlite3.Connection) -> None:
    """Put every manual category and kind back on its transaction (after a re-import)."""
    conn.execute(
        "UPDATE transactions SET category_id = o.category_id, category_source = 'manual' "
        "FROM overrides o WHERE o.transaction_id = transactions.id AND o.category_id IS NOT NULL"
    )
    conn.execute(
        "UPDATE transactions SET kind = o.kind "
        "FROM overrides o WHERE o.transaction_id = transactions.id AND o.kind IS NOT NULL"
    )


def set_closing_balance(
    conn: sqlite3.Connection, account: int, day: date, amount: Decimal, statement_file: int
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO balances (account_id, date, amount_nano, source, "
        "statement_file_id) VALUES (?, ?, ?, 'statement', ?)",
        (account, day.isoformat(), to_nano(amount), statement_file),
    )


def set_opening_balance(
    conn: sqlite3.Connection, account: int, day: date, amount: Decimal, statement_file: int
) -> None:
    """The balance a statement started from, unless an earlier statement already closed on it."""
    conn.execute(
        "INSERT OR IGNORE INTO balances (account_id, date, amount_nano, source, "
        "statement_file_id) VALUES (?, ?, ?, 'statement', ?)",
        (account, day.isoformat(), to_nano(amount), statement_file),
    )


def set_typed_balance(conn: sqlite3.Connection, account: int, day: date, amount: Decimal) -> None:
    """What a typed account held at the end of `day`, replacing an earlier figure for that day."""
    conn.execute(
        "INSERT OR REPLACE INTO balances (account_id, date, amount_nano, source) "
        "VALUES (?, ?, ?, 'manual')",
        (account, day.isoformat(), to_nano(amount)),
    )


def anchors(conn: sqlite3.Connection, owner: int) -> list[Anchor]:
    """Every statement balance of the owner's accounts, by account and then by day."""
    rows = conn.execute(
        "SELECT b.account_id, a.name, a.currency, b.date, b.amount_nano, f.period_start > b.date "
        "FROM balances b JOIN accounts a ON a.id = b.account_id "
        "JOIN statement_files f ON f.id = b.statement_file_id "
        "WHERE a.owner_id = ? AND b.source = 'statement' ORDER BY b.account_id, b.date",
        (owner,),
    ).fetchall()
    return [
        Anchor(int(r[0]), str(r[1]), str(r[2]), date.fromisoformat(r[3]), int(r[4]), bool(r[5]))
        for r in rows
    ]


def own_flow(conn: sqlite3.Connection, account: int, after: date, until: date) -> int:
    """What the account's own statements moved after `after`, up to and including `until`.

    Rows another source books on the account are left out: they stand for rows the account's
    own statement already has (D3: the card purchases behind the card debit on the bank).
    """
    row = conn.execute(
        "SELECT coalesce(sum(t.amount_nano), 0) FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id "
        "JOIN statement_files f ON f.id = t.statement_file_id AND f.source = a.source "
        "WHERE t.account_id = ? AND t.booking_date > ? AND t.booking_date <= ?",
        (account, after.isoformat(), until.isoformat()),
    ).fetchone()
    return int(row[0])


def uncategorized(conn: sqlite3.Connection, owner: int, period: tuple[date, date]) -> int:
    """How many of the owner's rows booked in the period still need a category."""
    row = conn.execute(
        "SELECT count(*) FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE a.owner_id = ? AND t.booking_date BETWEEN ? AND ? "
        "AND t.category_id IS NULL AND t.kind <> 'internal'",
        (owner, period[0].isoformat(), period[1].isoformat()),
    ).fetchone()
    return int(row[0])
