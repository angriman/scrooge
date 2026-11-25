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

from scrooge import classification, staging
from scrooge.models import TypedHolding
from scrooge.staging import StagingError
from scrooge.storage import money

TABS = ("Stocks", "ETFs")  # what an instrument not known yet may be typed as
_CODE = 3  # letters in a currency code


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
