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
from finanze.models import Holding, PrintedValue, SecurityTrade, Transaction
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
    """The account's id, creating it on first sight. `source` is None for a typed account."""
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
    holdings: bool = False,
) -> int:
    """The row of this exact file; importing the same bytes again keeps its id and timestamp.

    `holdings` says that the statement lists what its accounts held at the end of its period.
    """
    row = conn.execute(
        "INSERT INTO statement_files (owner_id, source, filename, sha256, period_start, "
        "period_end, reconciled, imported_at, holdings) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (owner_id, sha256) DO UPDATE SET source = excluded.source, "
        "filename = excluded.filename, period_start = excluded.period_start, "
        "period_end = excluded.period_end, reconciled = excluded.reconciled, "
        "holdings = excluded.holdings "
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
        ),
    ).fetchone()
    return int(row[0])


def delete_statement_rows(
    conn: sqlite3.Connection, source: str, accounts: Collection[int], period: tuple[date, date]
) -> set[int]:
    """Remove what earlier imports of `source` stored for these accounts in this period.

    Transactions go by booking date, which always lies inside the statement period; balances
    and holdings go with the earlier file of the same period. Returns the files those rows
    came from.
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
        (balances.replace("FROM balances", "FROM positions"), (*accounts, source, first, last)),
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


def instrument_id(
    conn: sqlite3.Connection, symbol: str, tab: str, currency: str, maturity: date | None = None
) -> int:
    """The instrument's id, creating it on first sight; what is said now replaces its type."""
    row = conn.execute(
        "INSERT INTO instruments (symbol, type, currency, maturity) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (symbol, currency) DO UPDATE SET type = excluded.type, "
        "maturity = coalesce(excluded.maturity, maturity) RETURNING id",
        (symbol, _INSTRUMENT_TYPES[tab], currency, maturity and maturity.isoformat()),
    ).fetchone()
    return int(row[0])


def _insert_trade(conn: sqlite3.Connection, txn_id: str, trade: SecurityTrade) -> None:
    instrument = instrument_id(conn, trade.ticker, trade.asset_tab, trade.currency, trade.maturity)
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
        "UPDATE transactions SET kind = o.kind, "
        "counterparty_owner_id = CASE WHEN o.kind IN ('income', 'expense') "
        "THEN transactions.counterparty_owner_id END "  # a transfer is nobody else's money (D14)
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


def set_holding(
    conn: sqlite3.Connection, account: int, day: date, holding: Holding, statement_file: int
) -> None:
    """What the statement says the account held of one instrument at the end of `day`.

    A share's close that day is kept as a price too. A bill's is a percentage of its face,
    and no price of the bill: its worth is the value on the row.
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
    if _INSTRUMENT_TYPES[holding.asset_tab] != "bill":
        set_price(conn, instrument, day, holding.price, source="statement")


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


def last_price_day(conn: sqlite3.Connection, instrument: int, source: str) -> date | None:
    """The latest day `source` has given a close of the instrument for."""
    row = conn.execute(
        "SELECT max(date) FROM prices WHERE instrument_id = ? AND source = ?", (instrument, source)
    ).fetchone()
    return None if row[0] is None else date.fromisoformat(row[0])


def last_rate_day(conn: sqlite3.Connection, currency: str, source: str) -> date | None:
    """The latest day `source` has given a rate of the currency for."""
    row = conn.execute(
        "SELECT max(date) FROM fx_rates WHERE currency = ? AND source = ?", (currency, source)
    ).fetchone()
    return None if row[0] is None else date.fromisoformat(row[0])


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


# What makes a row of `transactions t` wait for a category: the one condition behind the count
# that blocks a commit, the rows the month page lists and the rows a category can be given to.
WAITING = "t.category_id IS NULL AND t.kind <> 'internal'"


def uncategorized(conn: sqlite3.Connection, owner: int, period: tuple[date, date]) -> int:
    """How many of the owner's rows booked in the period still need a category."""
    row = conn.execute(
        "SELECT count(*) FROM transactions t JOIN accounts a ON a.id = t.account_id "  # noqa: S608  # only the fixed text above is interpolated
        f"WHERE a.owner_id = ? AND t.booking_date BETWEEN ? AND ? AND {WAITING}",
        (owner, period[0].isoformat(), period[1].isoformat()),
    ).fetchone()
    return int(row[0])


@dataclass(frozen=True, slots=True)
class Waiting:
    """A row without a category, with what a choice for it depends on."""

    id: str
    amount_nano: int
    kind: str
    description: str
    counterparty: str


def waiting(
    conn: sqlite3.Connection, owner: int, period: tuple[date, date], ids: Collection[str]
) -> list[Waiting]:
    """Those of `ids` that are rows of the owner booked in the period and still need a category."""
    marks = ", ".join("?" * len(ids))
    rows = conn.execute(
        "SELECT t.id, t.amount_nano, t.kind, t.description, t.counterparty "  # noqa: S608  # only "?" marks and the fixed text above are interpolated
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE a.owner_id = ? AND t.booking_date BETWEEN ? AND ? "
        f"AND {WAITING} AND t.id IN ({marks}) ORDER BY t.id",
        (owner, period[0].isoformat(), period[1].isoformat(), *ids),
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
    import gives it.
    """
    conn.execute(
        "INSERT OR REPLACE INTO overrides (transaction_id, category_id, kind, created_at) "
        "VALUES (?, ?, ?, ?)",
        (txn_id, category, kind, created_at),
    )
