"""What an account held at the end of a day where no statement says it, typed by hand: its
balance (`scrooge balance`) or how many of one instrument it held (`scrooge position`), from
the command line or the accounts page. The figures are read and checked here, then kept by
`staging`, which refuses what does not follow from what is known.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from scrooge import classification, staging
from scrooge.models import CERTIFICATE_SOURCES, TypedHolding
from scrooge.staging import StagingError
from scrooge.storage import money
from scrooge.storage.money import from_nano

TABS = ("Stocks", "ETFs")  # what an instrument not known yet may be typed as
_CODE = 3  # letters in a currency code
# Sources whose export states neither a balance nor what is held (`staging._TYPED_INTO`).
_TYPED_INTO = frozenset({"schwab"})

Typable = Literal["none", "balance", "both"]


@dataclass(frozen=True, slots=True)
class Figure:
    """One balance or position typed by hand for an account."""

    owner: str
    account: str
    day: date
    what: Literal["balance", "position"]
    currency: str
    amount: Decimal | None = None
    symbol: str | None = None
    quantity: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ManualAccount:
    """An account fed only by typed figures (`accounts.source IS NULL`)."""

    owner: str
    account: str
    closed: date | None


def typable(source: str | None) -> Typable:
    """What kind of figure may be typed into an account fed by `source` (None: typed-only)."""
    if source is None or source in _TYPED_INTO:
        return "both"
    if source in CERTIFICATE_SOURCES:
        return "balance"
    return "none"


def figures(
    conn: sqlite3.Connection, owner: str | None = None
) -> dict[tuple[str, str], tuple[Figure, ...]]:
    """Every balance and position typed by hand for `owner` (everyone without one), grouped by
    `(owner, account)` and ordered newest first."""
    balances = conn.execute(
        "SELECT o.name, a.name, b.date, b.amount_nano, a.currency "
        "FROM balances b JOIN accounts a ON a.id = b.account_id "
        "JOIN owners o ON o.id = a.owner_id "
        "WHERE b.source = 'manual' AND o.name = coalesce(?, o.name)",
        (owner,),
    ).fetchall()
    positions = conn.execute(
        "SELECT o.name, a.name, p.date, i.symbol, p.quantity, i.currency "
        "FROM positions p JOIN accounts a ON a.id = p.account_id "
        "JOIN owners o ON o.id = a.owner_id "
        "JOIN instruments i ON i.id = p.instrument_id "
        "WHERE p.source = 'manual' AND o.name = coalesce(?, o.name)",
        (owner,),
    ).fetchall()
    grouped: dict[tuple[str, str], list[Figure]] = {}
    for who, account, on, amount_nano, code in balances:
        grouped.setdefault((who, account), []).append(
            Figure(
                owner=who,
                account=account,
                day=date.fromisoformat(on),
                what="balance",
                currency=code,
                amount=from_nano(amount_nano),
            )
        )
    for who, account, on, symbol, qty, code in positions:
        grouped.setdefault((who, account), []).append(
            Figure(
                owner=who,
                account=account,
                day=date.fromisoformat(on),
                what="position",
                currency=code,
                symbol=symbol,
                quantity=Decimal(qty),
            )
        )
    return {
        key: tuple(sorted(items, key=lambda f: (-f.day.toordinal(), f.what, f.symbol or "")))
        for key, items in grouped.items()
    }


def manual_accounts(
    conn: sqlite3.Connection, owner: str | None = None
) -> tuple[ManualAccount, ...]:
    """Every typed-only account (`accounts.source IS NULL`) of `owner`, or of everyone."""
    rows = conn.execute(
        "SELECT o.name, a.name, a.closed_on FROM accounts a "
        "JOIN owners o ON o.id = a.owner_id "
        "WHERE a.source IS NULL AND o.name = coalesce(?, o.name) "
        "ORDER BY o.name, a.name",
        (owner,),
    ).fetchall()
    return tuple(
        ManualAccount(who, account, date.fromisoformat(closed) if closed else None)
        for who, account, closed in rows
    )


@dataclass(frozen=True, slots=True)
class Where:
    """Whose account, which one, and the day whose end it is typed for."""

    owner: str
    account: str
    day: date


def amount(text: str) -> Decimal:
    """`text` as an amount the database can keep."""
    try:
        found = Decimal(text)
    except InvalidOperation:
        found = Decimal("NaN")
    if not (found.is_finite() and money.fits(found)):
        msg = f"{text!r} is not an amount like 1234.56"
        raise StagingError(msg)
    return found


def quantity(text: str) -> Decimal:
    """`text` as a number of shares held: 0 or more."""
    found = amount(text)
    if found < 0:
        msg = f"{text!r} is no number of shares held: 0 or more"
        raise StagingError(msg)
    return found


def currency_code(text: str) -> str:
    """A currency as the database keeps it: three capital letters."""
    code = text.upper()
    if not (len(code) == _CODE and code.isascii() and code.isalpha()):
        msg = f"{text!r} is not a currency code like CHF or USD"
        raise StagingError(msg)
    return code


def known_as(symbol: str, instruments_file: Path) -> str:
    """The symbol `instruments.yaml` knows an instrument by, for a symbol typed: the one whose
    `price_symbol` it is (NESN for NESN.SW), or the new name of a ticker renamed. So a
    position typed is the same instrument a statement books, not a second one."""
    symbol = symbol.upper()
    instruments = classification.load_instruments(instruments_file)
    for name, entry in instruments.items():
        if entry.price_symbol.upper() == symbol:
            return name
    renamed = instruments.get(symbol)
    return renamed.ticker if renamed is not None and renamed.ticker else symbol


def _begun(day: date, today: date) -> None:
    if day > today:
        msg = f"{day} is after today ({today}): type what was held on a day that has begun"
        raise StagingError(msg)


def balance(
    conn: sqlite3.Connection, where: Where, held: Decimal, currency: str, today: date
) -> str:
    """Keep what the account held at the end of the day; returns the currency as kept."""
    code = currency_code(currency)
    _begun(where.day, today)
    staging.type_balance(
        conn, owner=where.owner, account=where.account, day=where.day, amount=held, currency=code
    )
    return code


def position(
    conn: sqlite3.Connection,
    where: Where,
    holding: TypedHolding,
    today: date,
    instruments_file: Path,
) -> TypedHolding:
    """Keep how many of one instrument the account held at the end of the day; returns it as
    kept, under the symbol the instruments file knows it by."""
    if holding.tab is not None and holding.tab not in TABS:
        msg = f"{holding.tab!r} is no kind of instrument to type: {' or '.join(TABS)}"
        raise StagingError(msg)
    code = currency_code(holding.currency)
    _begun(where.day, today)
    kept = TypedHolding(
        known_as(holding.symbol, instruments_file), holding.quantity, code, holding.tab
    )
    staging.type_holding(
        conn, owner=where.owner, account=where.account, day=where.day, holding=kept
    )
    return kept


def remove_balance(conn: sqlite3.Connection, where: Where) -> None:
    """Remove the balance typed for the day."""
    staging.remove_typed(conn, owner=where.owner, account=where.account, day=where.day)


def remove_position(
    conn: sqlite3.Connection, where: Where, symbol: str, instruments_file: Path
) -> str:
    """Remove what was typed of `symbol` for the day; returns the symbol as the instruments
    file knows it."""
    known = known_as(symbol, instruments_file)
    staging.remove_typed(
        conn, owner=where.owner, account=where.account, day=where.day, symbol=known
    )
    return known
