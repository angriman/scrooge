"""The SQL behind the import and commit service: small typed functions over one connection.

Nothing here begins or commits a transaction, and nothing decides anything: callers do both.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge import categories
from scrooge.models import Holding, PrintedValue, SecurityTrade, Transaction, TypedHolding
from scrooge.storage.money import from_nano, to_nano, to_text

_CURRENCY_DECIMALS = 2
_INSTRUMENT_TYPES = {"ETFs": "etf", "Stocks": "stock", "Bonds": "bill", "Funds": "fund"}
_SIDES = ("expenses", "income")


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


def marks(count: int) -> str:
    """`?, ?, ?`: one parameter mark per value, for `IN (...)`; the values go as parameters."""
    return ", ".join("?" * count)


def ensure_owner(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO owners (name) VALUES (?)", (name,))
    return int(conn.execute("SELECT id FROM owners WHERE name = ?", (name,)).fetchone()[0])


def owner_id(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute("SELECT id FROM owners WHERE name = ?", (name,)).fetchone()
    return None if row is None else int(row[0])


def ensure_currency(conn: sqlite3.Connection, code: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO currencies (code, decimals) VALUES (?, ?)",
        (code, _CURRENCY_DECIMALS),
    )


def ensure_account(  # noqa: PLR0913  # one argument per column
    conn: sqlite3.Connection,
    owner: int,
    *,
    name: str,
    kind: str,
    currency: str,
    source: str | None,
) -> int:
    """The account's id, creating it on first sight. `source` is None for a typed account.

    Kind `manual` is an account carried flat between the days a balance is known for: typed,
    or with a `source`, stated by that source's certificates.
    """
    ensure_currency(conn, currency)
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
        msg = (
            f"account {name!r} cannot have both statements and typed balances (what was typed "
            "is removed with `scrooge balance --remove` and `scrooge position --remove`)"
        )
        raise AccountMismatchError(msg)
    return int(row["id"])


@dataclass(frozen=True, slots=True)
class Account:
    """An account as it is stored: `source` None for one only ever typed."""

    id: int
    kind: str
    currency: str
    source: str | None


def account_of(conn: sqlite3.Connection, owner: int, name: str) -> Account | None:
    """The owner's account `name`, if there is one."""
    row = conn.execute(
        "SELECT id, kind, currency, source FROM accounts WHERE owner_id = ? AND name = ?",
        (owner, name),
    ).fetchone()
    return None if row is None else Account(int(row[0]), row[1], row[2], row[3])


def seed_categories(conn: sqlite3.Connection, extras: Sequence[categories.Extra] = ()) -> None:
    """Make sure the agreed taxonomy (D6) and what `categories.yaml` adds are in the database,
    each by its key: a built-in category under its English label, which is never read (what is
    shown is the label of its key), an added one under the name it was given, which is.

    Existing rows are left alone.
    """
    insert = (
        "INSERT OR IGNORE INTO categories (key, name, parent_id, side, sort_order) "
        "VALUES (?, ?, ?, ?, ?)"
    )
    names = {**categories.LABELS_EN}
    expenses = {parent: list(leaves) for parent, leaves in categories.EXPENSE_GROUPS.items()}
    for extra in extras:
        names.setdefault(extra.key, extra.name)
        if extra.side == "expenses":
            names.setdefault(extra.group_key, extra.group)
            expenses.setdefault(extra.group_key, []).append(extra.key)
    order = 0
    for parent, leaves in expenses.items():
        conn.execute(insert, (parent, names[parent], None, "expenses", order))
        parent_id = conn.execute(
            "SELECT id FROM categories WHERE side = 'expenses' AND parent_id IS NULL AND key = ?",
            (parent,),
        ).fetchone()[0]
        for leaf in leaves:
            order += 1
            conn.execute(insert, (leaf, names[leaf], parent_id, "expenses", order))
        order += 1
    income = (*categories.INCOME_LEAVES, *(e.key for e in extras if e.side == "income"))
    for side, keys in (("income", income), ("transfer", categories.TRANSFERS)):
        for key in keys:
            order += 1
            conn.execute(insert, (key, names[key], None, side, order))


def category_key(conn: sqlite3.Connection, name: str) -> str:
    """The key of the category `name` means: a key the database holds, else the key of a
    label (`categories.key`), else that of an added category named so; `name` itself when
    none is."""
    for each in (name, categories.key(name)):
        if conn.execute("SELECT 1 FROM categories WHERE key = ?", (each,)).fetchone():
            return each
    found = conn.execute("SELECT key FROM categories WHERE name = ?", (name.strip(),)).fetchone()
    return str(found[0]) if found else name


def category_labels(conn: sqlite3.Connection) -> categories.Labels:
    """The label of every category the database holds: a built-in one's from its key, an added
    one's the name it was given."""
    rows = conn.execute("SELECT key, name FROM categories ORDER BY id").fetchall()
    return categories.Labels({row["key"]: row["name"] for row in rows})


# The taxonomy's own order, never the names (which change with the language): expenses, then
# income, then transfers; each top-level category by its sort_order, its leaves right after
# it by theirs; the id breaks a tie. For `categories c LEFT JOIN categories p` (the parent).
TAXONOMY_ORDER = (
    "CASE c.side WHEN 'expenses' THEN 0 WHEN 'income' THEN 1 ELSE 2 END, "
    "coalesce(p.sort_order, c.sort_order), coalesce(p.id, c.id), c.parent_id IS NOT NULL, "
    "c.sort_order, c.id"
)
_ALL_SIDES = (*_SIDES, "transfer")


@dataclass(frozen=True, slots=True)
class Ranks:
    """Where each category stands in `TAXONOMY_ORDER`: what lists of categories sort by."""

    tops: Mapping[tuple[str, str], int]  # (side, key) of each top-level category
    leaves: Mapping[tuple[str, str], int]  # (side, key) of each category a row can carry

    def top(self, name: str, *sides: str) -> int:
        """The place of the top-level category of key `name`, on the first of `sides` (of all
        sides, expenses first) that has one; after every category when none has."""
        return _rank(self.tops, name, sides)

    def leaf(self, name: str, *sides: str) -> int:
        """The place of the category of key `name` a row carries, as `top` finds it."""
        return _rank(self.leaves, name, sides)


def _rank(ranks: Mapping[tuple[str, str], int], name: str, sides: Sequence[str]) -> int:
    found = (ranks[side, name] for side in sides or _ALL_SIDES if (side, name) in ranks)
    return next(found, len(ranks))


def category_ranks(conn: sqlite3.Connection) -> Ranks:
    """The place in the taxonomy of every category the database holds."""
    tops: dict[tuple[str, str], int] = {}
    leaves: dict[tuple[str, str], int] = {}
    found = conn.execute(
        "SELECT c.side, c.key, c.parent_id IS NULL, c.side = 'expenses' AND c.parent_id IS NULL "  # noqa: S608  # only fixed text is interpolated
        f"FROM categories c LEFT JOIN categories p ON p.id = c.parent_id ORDER BY {TAXONOMY_ORDER}"
    )
    for side, name, top, group in found:
        if top:
            tops[side, name] = len(tops)
        if not group:
            leaves[side, name] = len(leaves)
    return Ranks(tops, leaves)


def leaf_categories(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """(side, key) -> id of every category a transaction can carry."""
    rows = conn.execute(
        "SELECT id, side, key FROM categories WHERE side <> 'expenses' OR parent_id IS NOT NULL"
    ).fetchall()
    return {(row["side"], row["key"]): int(row["id"]) for row in rows}


def category_for(
    leaves: dict[tuple[str, str], int], name: str, amount: Decimal, said: str = ""
) -> int | None:
    """The category of key `name` on the side the sign points to, or on the other one;
    `said` is how it was asked for, which an error repeats (the key without it).

    Several keys exist on both sides (Trading, Fees, Investments): the sign decides,
    as in D1. A refund keeps its expense category although it is money coming in.
    """
    if not name:
        return None
    if ("transfer", name) in leaves:
        return leaves["transfer", name]
    for side in _SIDES if amount < 0 else _SIDES[::-1]:
        if (side, name) in leaves:
            return leaves[side, name]
    msg = f"unknown category {said or name!r}"
    raise UnknownCategoryError(msg)


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
    holdings: bool = False,
    account_number: str = "",
) -> int:
    """The row of this exact file; importing the same bytes again keeps its id and timestamp.

    `holdings` says that the statement lists what its accounts held at the end of its period.
    A new file is staged; one staged or committed before stays so.
    """
    row = conn.execute(
        "INSERT INTO statement_files (owner_id, source, filename, sha256, period_start, "
        "period_end, reconciled, imported_at, holdings, account_number) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (owner_id, sha256) DO UPDATE SET source = excluded.source, "
        "filename = excluded.filename, period_start = excluded.period_start, "
        "period_end = excluded.period_end, reconciled = excluded.reconciled, "
        "holdings = excluded.holdings, account_number = excluded.account_number "
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
            "printed" if holdings else "not_printed",
            account_number,
        ),
    ).fetchone()
    return int(row[0])


def is_committed(conn: sqlite3.Connection, owner: int, sha256: str) -> bool:
    """Whether the file with these bytes is stored for the owner and committed."""
    row = conn.execute(
        "SELECT 1 FROM statement_files WHERE owner_id = ? AND sha256 = ? "
        "AND committed_at IS NOT NULL",
        (owner, sha256),
    ).fetchone()
    return row is not None


def link_accounts(conn: sqlite3.Connection, statement_file: int, accounts: Iterable[int]) -> None:
    """Record the accounts the statement covers."""
    conn.executemany(
        "INSERT OR IGNORE INTO statement_accounts (statement_file_id, account_id) VALUES (?, ?)",
        [(statement_file, account) for account in accounts],
    )


def committed_span(conn: sqlite3.Connection, source: str, account: int) -> tuple[date, date] | None:
    """The first and the last day committed statements of `source` cover of the account."""
    row = conn.execute(
        "SELECT min(f.period_start), max(f.period_end) FROM statement_files f "
        "JOIN statement_accounts s ON s.statement_file_id = f.id "
        "WHERE f.source = ? AND s.account_id = ? AND f.committed_at IS NOT NULL",
        (source, account),
    ).fetchone()
    return None if row[0] is None else (date.fromisoformat(row[0]), date.fromisoformat(row[1]))


def committed_file(conn: sqlite3.Connection, source: str, account: int, day: date) -> str | None:
    """The digest of a committed statement of `source` covering the account on `day`."""
    row = conn.execute(
        "SELECT f.sha256 FROM statement_files f "
        "JOIN statement_accounts s ON s.statement_file_id = f.id "
        "WHERE f.source = ? AND s.account_id = ? AND f.committed_at IS NOT NULL "
        "AND ? BETWEEN f.period_start AND f.period_end ORDER BY f.id",
        (source, account, day.isoformat()),
    ).fetchone()
    return None if row is None else str(row[0])


def owner_of_number(conn: sqlite3.Connection, account_number: str) -> str | None:
    """Whose statements print this account number, if any were imported."""
    row = conn.execute(
        "SELECT o.name FROM statement_files f JOIN owners o ON o.id = f.owner_id "
        "WHERE f.account_number = ? ORDER BY f.id",
        (account_number,),
    ).fetchone()
    return None if row is None else str(row[0])


def numbers_of(conn: sqlite3.Connection, account_id: int) -> set[str]:
    """The account numbers printed by the committed statements of an account."""
    found = conn.execute(
        "SELECT DISTINCT f.account_number FROM statement_files f "
        "JOIN statement_accounts s ON s.statement_file_id = f.id "
        "WHERE s.account_id = ? AND f.account_number != '' AND f.committed_at IS NOT NULL",
        (account_id,),
    )
    return {str(number) for (number,) in found}


@dataclass(frozen=True, slots=True)
class StatementFile:
    """A statement file as stored."""

    id: int
    owner: str
    source: str
    filename: str
    period: tuple[date, date]
    imported_at: str
    committed_at: str | None  # None: staged


def statement_files(
    conn: sqlite3.Connection, owner: int | None = None, *, staged: bool | None = None
) -> list[StatementFile]:
    """The statement files of `owner` (or everyone's): only staged ones, only committed ones,
    or both when `staged` is None. Oldest period first."""
    rows = conn.execute(
        "SELECT f.id, o.name, f.source, f.filename, f.period_start, f.period_end, "
        "f.imported_at, f.committed_at FROM statement_files f "
        "JOIN owners o ON o.id = f.owner_id WHERE f.owner_id = coalesce(?1, f.owner_id) "
        "AND (?2 IS NULL OR (f.committed_at IS NULL) = ?2) "
        "ORDER BY f.period_start, f.period_end, f.source, f.id",
        (owner, staged),
    ).fetchall()
    return [
        StatementFile(
            int(r[0]),
            str(r[1]),
            str(r[2]),
            str(r[3]),
            (date.fromisoformat(r[4]), date.fromisoformat(r[5])),
            str(r[6]),
            r[7],
        )
        for r in rows
    ]


def commit_files(conn: sqlite3.Connection, files: Collection[int], committed_at: str) -> None:
    conn.executemany(
        "UPDATE statement_files SET committed_at = ? WHERE id = ? AND committed_at IS NULL",
        [(committed_at, file_id) for file_id in files],
    )


def uncommit_file(conn: sqlite3.Connection, file_id: int) -> None:
    conn.execute("UPDATE statement_files SET committed_at = NULL WHERE id = ?", (file_id,))


def later_committed(conn: sqlite3.Connection, file_id: int) -> list[str]:
    """Committed files of the same source and accounts that end after this one, and others
    that start before it: a file inside what is committed cannot be taken back alone."""
    rows = conn.execute(
        "SELECT DISTINCT o.filename, o.period_end > f.period_end FROM statement_files f "
        "JOIN statement_accounts s ON s.statement_file_id = f.id "
        "JOIN statement_accounts os ON os.account_id = s.account_id "
        "JOIN statement_files o ON o.id = os.statement_file_id AND o.id <> f.id "
        "AND o.source = f.source AND o.committed_at IS NOT NULL "
        "WHERE f.id = ? ORDER BY o.period_start",
        (file_id,),
    ).fetchall()
    after = [str(r[0]) for r in rows if r[1]]
    before = [str(r[0]) for r in rows if not r[1]]
    return after if after and before else []


def delete_file(conn: sqlite3.Connection, file_id: int) -> None:
    """Remove a statement file and everything it gave the accounts.

    A balance is stored once per account and day, under one file; where another file of the
    same source and account states that day too (it closes on it, or opens from it) the
    balance is kept and becomes that file's.
    """
    for table in ("transactions", "positions"):
        conn.execute(f"DELETE FROM {table} WHERE statement_file_id = ?", (file_id,))  # noqa: S608  # the table names are the literals above
    owned = conn.execute(
        "SELECT account_id, date FROM balances WHERE statement_file_id = ?", (file_id,)
    ).fetchall()
    for account, day in owned:
        heir = conn.execute(
            "SELECT o.id FROM statement_files o "
            "JOIN statement_accounts s ON s.statement_file_id = o.id AND s.account_id = ?1 "
            "WHERE o.id <> ?2 AND o.source = (SELECT source FROM statement_files WHERE id = ?2) "
            "AND (o.period_end = ?3 OR date(o.period_start, '-1 day') = ?3) ORDER BY o.id",
            (account, file_id, day),
        ).fetchone()
        if heir is not None:
            conn.execute(
                "UPDATE balances SET statement_file_id = ? WHERE account_id = ? AND date = ?",
                (heir[0], account, day),
            )
    conn.execute("DELETE FROM balances WHERE statement_file_id = ?", (file_id,))
    conn.execute("DELETE FROM statement_files WHERE id = ?", (file_id,))


def statement_names(conn: sqlite3.Connection) -> dict[str, str]:
    """The digest and the name of every statement file stored, whoever it belongs to."""
    found = conn.execute("SELECT sha256, filename FROM statement_files ORDER BY id").fetchall()
    return {str(row[0]): str(row[1]) for row in found}


def delete_statement_rows(
    conn: sqlite3.Connection, source: str, accounts: Collection[int], period: tuple[date, date]
) -> set[int]:
    """Remove what earlier imports of `source` stored for these accounts in this period.

    Transactions go by booking date, which always lies inside the statement period; balances
    and holdings go with the earlier file of the same period. Returns the files those rows
    came from.
    """
    in_accounts = marks(len(accounts))
    first, last = period[0].isoformat(), period[1].isoformat()
    transactions = (
        f"FROM transactions WHERE account_id IN ({in_accounts}) AND booking_date BETWEEN ? AND ? "  # noqa: S608  # only "?" marks are interpolated
        "AND statement_file_id IN (SELECT id FROM statement_files WHERE source = ?)"
    )
    balances = (
        f"FROM balances WHERE account_id IN ({in_accounts}) AND statement_file_id IN "  # noqa: S608  # only "?" marks are interpolated
        "(SELECT id FROM statement_files WHERE source = ? AND period_start = ? AND period_end = ?)"
    )
    touched: set[int] = set()
    for rows, args in (
        (transactions, (*accounts, first, last, source)),
        (balances, (*accounts, source, first, last)),
        (balances.replace("FROM balances", "FROM positions"), (*accounts, source, first, last)),
    ):
        found = conn.execute(f"SELECT DISTINCT statement_file_id {rows}", args).fetchall()
        touched |= {int(row[0]) for row in found}
        conn.execute(f"DELETE {rows}", args)
    return touched


SameRow = tuple[str, str, int, str]  # the day it happened, currency, amount and description


def same_row(day: str, currency: str, amount_nano: int, description: str) -> SameRow:
    """What makes two rows the same one when their ids differ: the day it happened (ISO),
    currency, amount (billionths) and description."""
    return (day, currency, amount_nano, description)


def transaction_same_row(txn: Transaction) -> SameRow:
    """`same_row` of a statement's row."""
    return same_row(txn.date.isoformat(), txn.currency, to_nano(txn.amount), txn.description)


@dataclass(frozen=True, slots=True)
class StoredRow:
    """A stored row as an import compares it with its own."""

    text: str  # what it is, to be read
    booked: date
    same: SameRow


def statement_rows(
    conn: sqlite3.Connection, source: str, accounts: Collection[int], period: tuple[date, date]
) -> dict[str, StoredRow]:
    """The rows statements of `source` gave these accounts, booked in the period, by id.
    Without the interest split off a bill, which no statement prints."""
    found = conn.execute(
        "SELECT t.id, a.name, t.booking_date, t.description, t.date, t.currency, t.amount_nano "  # noqa: S608  # only "?" marks are interpolated
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        f"WHERE t.account_id IN ({marks(len(accounts))}) AND t.booking_date BETWEEN ? AND ? "
        "AND t.split_of IS NULL "
        "AND t.statement_file_id IN (SELECT id FROM statement_files WHERE source = ?) "
        "ORDER BY t.booking_date, t.id",
        (*accounts, period[0].isoformat(), period[1].isoformat(), source),
    ).fetchall()
    return {
        str(r[0]): StoredRow(
            f"{r[1]} {r[2]}: {r[3]}",
            date.fromisoformat(r[2]),
            same_row(str(r[4]), str(r[5]), int(r[6]), str(r[3])),
        )
        for r in found
    }


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


def instrument_id(  # noqa: PLR0913  # which instrument, and what a statement says of it
    conn: sqlite3.Connection,
    symbol: str,
    tab: str,
    currency: str,
    maturity: date | None = None,
    *,
    name: str = "",
) -> int:
    """The instrument's id, creating it on first sight; what is said now replaces its type.

    `name` is kept only while the instrument has none: the first statement to name it wins.
    """
    row = conn.execute(
        "INSERT INTO instruments (symbol, name, type, currency, maturity) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (symbol, currency) DO UPDATE SET type = excluded.type, "
        "maturity = coalesce(excluded.maturity, maturity), "
        "name = CASE name WHEN '' THEN excluded.name ELSE name END RETURNING id",
        (symbol, name, _INSTRUMENT_TYPES[tab], currency, maturity and maturity.isoformat()),
    ).fetchone()
    return int(row[0])


def _insert_trade(conn: sqlite3.Connection, txn_id: str, trade: SecurityTrade) -> None:
    instrument = instrument_id(
        conn, trade.ticker, trade.asset_tab, trade.currency, trade.maturity, name=trade.name
    )
    conn.execute(
        "INSERT INTO trades (transaction_id, instrument_id, quantity, price, cost_nano) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            txn_id,
            instrument,
            to_text(trade.quantity),
            None if trade.price is None else to_text(trade.price),
            to_nano(trade.cost),
        ),
    )


def apply_overrides(conn: sqlite3.Connection) -> None:
    """Put every manual category and kind back on its transaction (after a re-import)."""
    conn.execute(
        "UPDATE transactions SET category_id = o.category_id, category_source = 'manual' "
        "FROM overrides o WHERE o.transaction_id = transactions.id AND o.category_id IS NOT NULL"
    )
    conn.execute(
        "UPDATE transactions SET kind = o.kind, "
        "counterparty_owner_id = CASE WHEN o.kind IN ('income', 'expense') "
        "THEN transactions.counterparty_owner_id END "  # a transfer is nobody else's money (D14)
        "FROM overrides o WHERE o.transaction_id = transactions.id AND o.kind IS NOT NULL"
    )


def balance_on(
    conn: sqlite3.Connection, account: int, day: date, *, stated: bool = False
) -> Decimal | None:
    """The balance stored for the account on `day`, if any; with `stated`, only one that a
    statement or a certificate gives, not one typed."""
    only = "AND source = 'statement'" if stated else ""
    row = conn.execute(
        f"SELECT amount_nano FROM balances WHERE account_id = ? AND date = ? {only}",  # noqa: S608  # only fixed text is joined
        (account, day.isoformat()),
    ).fetchone()
    return None if row is None else from_nano(int(row[0]))


def set_closing_balance(
    conn: sqlite3.Connection, account: int, day: date, amount: Decimal, statement_file: int
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO balances (account_id, date, amount_nano, source, "
        "statement_file_id) VALUES (?, ?, ?, 'statement', ?)",
        (account, day.isoformat(), to_nano(amount), statement_file),
    )


def is_committed_balance(conn: sqlite3.Connection, account: int, day: date) -> bool:
    """Whether the balance stored for the account on `day` is owned by a committed file."""
    found = conn.execute(
        "SELECT 1 FROM balances b JOIN statement_files f ON f.id = b.statement_file_id "
        "WHERE b.account_id = ? AND b.date = ? AND f.committed_at IS NOT NULL",
        (account, day.isoformat()),
    ).fetchone()
    return found is not None


def set_opening_balance(
    conn: sqlite3.Connection, account: int, day: date, amount: Decimal, statement_file: int
) -> None:
    """The balance a statement started from, unless an earlier statement already closed on it."""
    conn.execute(
        "INSERT OR IGNORE INTO balances (account_id, date, amount_nano, source, "
        "statement_file_id) VALUES (?, ?, ?, 'statement', ?)",
        (account, day.isoformat(), to_nano(amount), statement_file),
    )


def balances_of(conn: sqlite3.Connection, account: int) -> list[tuple[date, Decimal]]:
    """Every balance known of the account, stated or typed, oldest first."""
    rows = conn.execute(
        "SELECT date, amount_nano FROM balances WHERE account_id = ? ORDER BY date", (account,)
    )
    return [(date.fromisoformat(day), from_nano(int(amount))) for day, amount in rows]


def has_typed(conn: sqlite3.Connection, account: int) -> bool:
    """Whether a balance or a position was typed for the account."""
    found = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM balances WHERE account_id = ?1 AND source = 'manual') "
        "OR EXISTS (SELECT 1 FROM positions WHERE account_id = ?1 AND source = 'manual')",
        (account,),
    ).fetchone()
    return bool(found[0])


def typed_on(conn: sqlite3.Connection, account: int, day: date) -> bool:
    """Whether a balance was typed for the account on `day`."""
    found = conn.execute(
        "SELECT 1 FROM balances WHERE account_id = ? AND date = ? AND source = 'manual'",
        (account, day.isoformat()),
    ).fetchone()
    return found is not None


def first_stated_day(conn: sqlite3.Connection, account: int) -> date | None:
    """The first day a statement or a certificate gives the account a balance for."""
    first = conn.execute(
        "SELECT min(date) FROM balances WHERE account_id = ? AND source = 'statement'",
        (account,),
    ).fetchone()[0]
    return None if first is None else date.fromisoformat(first)


def remove_typed_balance(conn: sqlite3.Connection, account: int, day: date) -> bool:
    """Remove the balance typed for the account on `day`; False when there was none."""
    gone = conn.execute(
        "DELETE FROM balances WHERE account_id = ? AND date = ? AND source = 'manual'",
        (account, day.isoformat()),
    )
    return gone.rowcount > 0


def remove_typed_holding(conn: sqlite3.Connection, account: int, symbol: str, day: date) -> bool:
    """Remove what was typed of `symbol` for the account on `day`; False when nothing was."""
    gone = conn.execute(
        "DELETE FROM positions WHERE account_id = ? AND date = ? AND source = 'manual' "
        "AND instrument_id IN (SELECT id FROM instruments WHERE symbol = ?)",
        (account, day.isoformat(), symbol),
    )
    return gone.rowcount > 0


def drop_if_empty(conn: sqlite3.Connection, account: int) -> bool:
    """Remove an account that only ever was typed once nothing is left of it, so that its
    name can be fed by statements or certificates after all."""
    left = conn.execute(
        "SELECT (SELECT count(*) FROM balances WHERE account_id = :a) "
        "+ (SELECT count(*) FROM positions WHERE account_id = :a) "
        "+ (SELECT count(*) FROM transactions WHERE account_id = :a) "
        "+ (SELECT count(*) FROM statement_accounts WHERE account_id = :a)",
        {"a": account},
    ).fetchone()[0]
    if left:
        return False
    conn.execute("DELETE FROM accounts WHERE id = ? AND source IS NULL", (account,))
    return True


def set_typed_balance(conn: sqlite3.Connection, account: int, day: date, amount: Decimal) -> None:
    """What a typed account held at the end of `day`, replacing an earlier figure for that day."""
    conn.execute(
        "INSERT OR REPLACE INTO balances (account_id, date, amount_nano, source) "
        "VALUES (?, ?, ?, 'manual')",
        (account, day.isoformat(), to_nano(amount)),
    )


def set_typed_holding(
    conn: sqlite3.Connection, account: int, day: date, holding: TypedHolding
) -> None:
    """What a typed account held of one instrument at the end of `day`, replacing an earlier
    figure for that day. Valued at the closes `scrooge prices` fetches for its symbol.

    An instrument already known (from a statement, or typed before) is kept as it is: typing
    never changes its type.
    """
    known = conn.execute(
        "SELECT id FROM instruments WHERE symbol = ? AND currency = ?",
        (holding.symbol, holding.currency),
    ).fetchone()
    instrument = (
        int(known[0])
        if known is not None
        else instrument_id(conn, holding.symbol, holding.tab or "Stocks", holding.currency)
    )
    conn.execute(
        "INSERT OR REPLACE INTO positions (account_id, instrument_id, date, quantity, source) "
        "VALUES (?, ?, ?, ?, 'manual')",
        (account, instrument, day.isoformat(), to_text(holding.quantity)),
    )


def set_holding(
    conn: sqlite3.Connection, account: int, day: date, holding: Holding, statement_file: int
) -> None:
    """What the statement says the account held of one instrument at the end of `day`.

    The row's price is the share's close that day, as far as net worth is concerned, for as
    long as the statement is committed. A bill's is a percentage of its face, and no price of
    the bill: its worth is the value on the row.
    """
    instrument = instrument_id(conn, holding.ticker, holding.asset_tab, holding.currency)
    conn.execute(
        "INSERT OR REPLACE INTO positions (account_id, instrument_id, date, quantity, price, "
        "value_nano, source, statement_file_id, unvested) "
        "VALUES (?, ?, ?, ?, ?, ?, 'statement', ?, ?)",
        (
            account,
            instrument,
            day.isoformat(),
            to_text(holding.quantity),
            to_text(holding.price),
            to_nano(holding.value),
            statement_file,
            int(holding.unvested),
        ),
    )


def set_price(  # noqa: PLR0913  # one argument per column
    conn: sqlite3.Connection,
    instrument: int,
    day: date,
    close: Decimal,
    *,
    source: str,
    fetched_at: str | None = None,
) -> None:
    """The instrument's close on `day` as `source` gives it, as it traded that day."""
    conn.execute(
        "INSERT OR REPLACE INTO prices (instrument_id, date, close, source, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (instrument, day.isoformat(), to_text(close), source, fetched_at),
    )


def set_rate(  # noqa: PLR0913  # one argument per column
    conn: sqlite3.Connection,
    day: date,
    currency: str,
    rate: Decimal,
    *,
    source: str,
    fetched_at: str | None = None,
) -> None:
    """What one unit of `currency` was worth in francs at the close of `day`."""
    ensure_currency(conn, currency)
    conn.execute(
        "INSERT OR REPLACE INTO fx_rates (date, currency, rate_to_chf, source, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (day.isoformat(), currency, to_text(rate), source, fetched_at),
    )


@dataclass(frozen=True, slots=True)
class Instrument:
    id: int
    symbol: str
    type: str
    currency: str
    price_symbol: str | None  # where its prices are fetched; None until that is known


def instruments(conn: sqlite3.Connection) -> list[Instrument]:
    rows = conn.execute(
        "SELECT id, symbol, type, currency, price_symbol FROM instruments ORDER BY id"
    ).fetchall()
    return [Instrument(int(r[0]), r[1], r[2], r[3], r[4]) for r in rows]


def set_price_symbol(conn: sqlite3.Connection, instrument: int, price_symbol: str) -> None:
    conn.execute("UPDATE instruments SET price_symbol = ? WHERE id = ?", (price_symbol, instrument))


def price_days(conn: sqlite3.Connection, instrument: int, source: str) -> tuple[date, date] | None:
    """The first and the latest day `source` has given a close of the instrument for."""
    row = conn.execute(
        "SELECT min(date), max(date) FROM prices WHERE instrument_id = ? AND source = ?",
        (instrument, source),
    ).fetchone()
    return None if row[0] is None else (date.fromisoformat(row[0]), date.fromisoformat(row[1]))


def rate_days(conn: sqlite3.Connection, currency: str, source: str) -> tuple[date, date] | None:
    """The first and the latest day `source` has given a rate of the currency for."""
    row = conn.execute(
        "SELECT min(date), max(date) FROM fx_rates WHERE currency = ? AND source = ?",
        (currency, source),
    ).fetchone()
    return None if row[0] is None else (date.fromisoformat(row[0]), date.fromisoformat(row[1]))


def newest_price(conn: sqlite3.Connection, instrument: int) -> date | None:
    """The latest day a close of the instrument is stored for, from any source."""
    row = conn.execute("SELECT max(date) FROM prices WHERE instrument_id = ?", (instrument,))
    found = row.fetchone()[0]
    return None if found is None else date.fromisoformat(found)


def newest_rate(conn: sqlite3.Connection, currency: str) -> date | None:
    """The latest day a rate of the currency is stored for, from any source."""
    found = conn.execute("SELECT max(date) FROM fx_rates WHERE currency = ?", (currency,))
    day = found.fetchone()[0]
    return None if day is None else date.fromisoformat(day)


def asked_from(conn: sqlite3.Connection, series: str, source: str) -> date | None:
    """The earliest day a request to `source` for the series began on, if one was made."""
    row = conn.execute(
        "SELECT first_day FROM asked_from WHERE series = ? AND source = ?", (series, source)
    ).fetchone()
    return None if row is None else date.fromisoformat(row[0])


def set_asked_from(conn: sqlite3.Connection, series: str, source: str, day: date) -> None:
    """Remember that the series was asked for from `day`, unless it was from an earlier one."""
    conn.execute(
        "INSERT INTO asked_from (series, source, first_day) VALUES (?, ?, ?) "
        "ON CONFLICT (series, source) DO UPDATE SET first_day = min(first_day, excluded.first_day)",
        (series, source, day.isoformat()),
    )


def add_price(  # noqa: PLR0913  # one argument per column
    conn: sqlite3.Connection,
    instrument: int,
    day: date,
    close: Decimal,
    *,
    source: str,
    fetched_at: str,
) -> int:
    """Keep a fetched close, unless that source already gave one for the day: a close that
    is stored is never fetched again (D10). Returns how many rows were added."""
    return conn.execute(
        "INSERT OR IGNORE INTO prices (instrument_id, date, close, source, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (instrument, day.isoformat(), to_text(close), source, fetched_at),
    ).rowcount


def add_rate(  # noqa: PLR0913  # one argument per column
    conn: sqlite3.Connection,
    day: date,
    currency: str,
    rate: Decimal,
    *,
    source: str,
    fetched_at: str,
) -> int:
    """Keep a fetched rate, unless that source already gave one for the day."""
    ensure_currency(conn, currency)
    return conn.execute(
        "INSERT OR IGNORE INTO fx_rates (date, currency, rate_to_chf, source, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (day.isoformat(), currency, to_text(rate), source, fetched_at),
    ).rowcount


def add_split(
    conn: sqlite3.Connection, instrument: int, day: date, numerator: int, denominator: int
) -> int:
    """Keep a split, unless one is stored for that day already."""
    return conn.execute(
        "INSERT OR IGNORE INTO splits (instrument_id, date, numerator, denominator) "
        "VALUES (?, ?, ?, ?)",
        (instrument, day.isoformat(), numerator, denominator),
    ).rowcount


def currencies(conn: sqlite3.Connection) -> list[str]:
    return [str(row[0]) for row in conn.execute("SELECT code FROM currencies ORDER BY code")]


def first_day(conn: sqlite3.Connection) -> date | None:
    """The earliest day anything is known for: a transaction or a balance."""
    row = conn.execute(
        "SELECT min(day) FROM (SELECT min(booking_date) AS day FROM transactions "
        "UNION ALL SELECT min(date) FROM transactions UNION ALL SELECT min(date) FROM balances)"
    ).fetchone()
    return None if row[0] is None else date.fromisoformat(row[0])


def set_statement_values(
    conn: sqlite3.Connection, statement_file: int, values: Iterable[PrintedValue]
) -> None:
    """The statement's own valuation, replacing what an earlier import of the file stored."""
    conn.execute("DELETE FROM statement_values WHERE statement_file_id = ?", (statement_file,))
    for value in values:
        ensure_currency(conn, value.currency)
        conn.execute(
            "INSERT INTO statement_values (statement_file_id, label, currency, value_nano) "
            "VALUES (?, ?, ?, ?)",
            (statement_file, value.label, value.currency, to_nano(value.value)),
        )


def set_statement_rates(
    conn: sqlite3.Connection, statement_file: int, day: date, rates: Mapping[str, Decimal]
) -> None:
    """The rates the statement prints for `day` (currency to francs per unit); an import of
    the same file again replaces them. They go with the file."""
    for currency, rate in rates.items():
        ensure_currency(conn, currency)
        conn.execute(
            "INSERT OR REPLACE INTO statement_rates "
            "(statement_file_id, currency, date, rate_to_chf) VALUES (?, ?, ?, ?)",
            (statement_file, currency, day.isoformat(), to_text(rate)),
        )


def anchors(conn: sqlite3.Connection, owner: int) -> list[Anchor]:
    """Every statement balance of the owner's accounts, by account and then by day.

    Not those of an account that is carried flat (kind `manual`): what a pension certificate
    states does not follow from the one before it, and nothing is booked in between.
    """
    rows = conn.execute(
        "SELECT b.account_id, a.name, a.currency, b.date, b.amount_nano, f.period_start > b.date "
        "FROM balances b JOIN accounts a ON a.id = b.account_id "
        "JOIN statement_files f ON f.id = b.statement_file_id "
        "WHERE a.owner_id = ? AND b.source = 'statement' AND a.kind <> 'manual' "
        "ORDER BY b.account_id, b.date",
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


# What makes a row of `transactions t` wait for a category: the one condition behind the count
# that blocks a commit, the rows the month page lists and the rows a category can be given to.
WAITING = "t.category_id IS NULL AND t.kind <> 'internal'"


def uncategorized(conn: sqlite3.Connection, owner: int) -> int:
    """How many of the owner's rows still need a category."""
    row = conn.execute(
        "SELECT count(*) FROM transactions t JOIN accounts a ON a.id = t.account_id "  # noqa: S608  # only the fixed text above is interpolated
        f"WHERE a.owner_id = ? AND {WAITING}",
        (owner,),
    ).fetchone()
    return int(row[0])


@dataclass(frozen=True, slots=True)
class Waiting:
    """A row to give a category to, with what a choice for it depends on."""

    id: str
    amount_nano: int
    kind: str
    description: str
    counterparty: str


def committed_or_typed(file_column: str) -> str:
    """The SQL condition that keeps a row whose statement is committed, or that no statement
    gave (typed by hand). `file_column` is the row's `statement_file_id`, with its table's alias.

    One rule for every reader of transactions, balances and positions that shows committed
    data only; a staged or discarded statement's rows fail it.
    """
    return (
        f"({file_column} IS NULL OR EXISTS (SELECT 1 FROM statement_files cf "  # noqa: S608  # `file_column` is a column name given by the callers
        f"WHERE cf.id = {file_column} AND cf.committed_at IS NOT NULL))"
    )


# What can be given another category: money in or out as the statement books it, whose
# category a rule or a choice gave. The rest (trades, dividends, the interest split off a
# bill) is categorized by what it is.
CHANGEABLE = (
    "t.category_id IS NOT NULL AND t.kind IN ('income', 'expense', 'transfer') "
    "AND t.split_of IS NULL"
)


def waiting(
    conn: sqlite3.Connection, owner: int, ids: Collection[str], *, change: bool = False
) -> list[Waiting]:
    """Those of `ids` that are rows of the owner and still need a category; with `change`,
    those that have one which can be changed, staged or committed."""
    rows = conn.execute(
        "SELECT t.id, t.amount_nano, t.kind, t.description, t.counterparty "  # noqa: S608  # only "?" marks and the fixed text above are interpolated
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        f"WHERE a.owner_id = ? AND {CHANGEABLE if change else WAITING} "
        f"AND t.id IN ({marks(len(ids))}) ORDER BY t.id",
        (owner, *ids),
    ).fetchall()
    return [Waiting(r[0], int(r[1]), r[2], r[3], r[4]) for r in rows]


def set_category(
    conn: sqlite3.Connection, txn_id: str, category: int, source: str, kind: str
) -> None:
    """Set the category and kind of a row. Only an income or expense is money between two people
    (D14): with another kind the row stops naming the other person, as it would after an import.
    """
    conn.execute(
        "UPDATE transactions SET category_id = ?1, category_source = ?2, kind = ?3, "
        "counterparty_owner_id = CASE WHEN ?3 IN ('income', 'expense') "
        "THEN counterparty_owner_id END WHERE id = ?4",
        (category, source, kind, txn_id),
    )


def set_override(
    conn: sqlite3.Connection, txn_id: str, category: int, kind: str | None, created_at: str
) -> None:
    """Remember a choice made for this row alone: `apply_overrides` restores it after an import.

    `kind` is None unless the choice changed it: the row then keeps whatever kind a later
    import gives it. A kind an earlier choice stored stays until a choice gives another: the
    kind the import gives is still the one that choice changed.
    """
    conn.execute(
        "INSERT INTO overrides (transaction_id, category_id, kind, created_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT (transaction_id) DO UPDATE SET "
        "category_id = excluded.category_id, kind = COALESCE(excluded.kind, kind), "
        "created_at = excluded.created_at",
        (txn_id, category, kind, created_at),
    )


@dataclass(frozen=True, slots=True)
class Logo:
    media_type: str
    image: bytes
    digest: str  # SHA-256 of `image`, hex


def set_logo(conn: sqlite3.Connection, symbol: str, logo: Logo, source: str, added_at: str) -> None:
    """Keep `logo` for the instrument `symbol`, in place of any it had."""
    conn.execute(
        "INSERT OR REPLACE INTO logos (symbol, media_type, image, digest, source, added_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (symbol, logo.media_type, logo.image, logo.digest, source, added_at),
    )


def remove_logo(conn: sqlite3.Connection, symbol: str) -> bool:
    """Forget the logo of `symbol`; False when it had none."""
    return conn.execute("DELETE FROM logos WHERE symbol = ?", (symbol,)).rowcount > 0


def logo_digests(conn: sqlite3.Connection) -> dict[str, str]:
    """Symbol -> the digest of its logo, for every symbol that has one."""
    return dict(conn.execute("SELECT symbol, digest FROM logos ORDER BY symbol").fetchall())


def logo(conn: sqlite3.Connection, symbol: str) -> Logo | None:
    row = conn.execute(
        "SELECT media_type, image, digest FROM logos WHERE symbol = ?", (symbol,)
    ).fetchone()
    return None if row is None else Logo(row[0], bytes(row[1]), row[2])
