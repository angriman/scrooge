"""Closing an account: a bank account closed, a pension fund left (decided 2026-11-25, plan
decision 51).

A closed account keeps its history: its rows, its balances and what it held count in net
worth, the charts and the transactions as before. The pages list it apart, under closed
accounts, and it is never behind (`months.behind`): no statement of it is missing.

It is closed on a day it held nothing at the end of: no cash and no shares, as net worth
counts them from what is committed and typed. A pension fund left without a certificate that
says so takes a zero typed for that day first (`scrooge balance`, plan decision 39). After that
day nothing may be booked, stated or typed for it but none (`staging.refuse_after_closing`),
until it is opened again.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from collections.abc import Sequence
from datetime import date

from scrooge import networth, staging, together
from scrooge.storage import db, holdings, store


class ClosingError(staging.StagingError):
    """The account cannot be closed or opened again as asked."""


def close(conn: sqlite3.Connection, *, owner: str, account: str, day: date) -> None:
    """Close `account` of `owner` on `day`, refused unless it held nothing at the end of it and
    nothing is booked, stated or typed for it after; closing a closed account moves the day."""
    close_all(conn, owner=owner, accounts=[account], day=day)


def close_all(conn: sqlite3.Connection, *, owner: str, accounts: Sequence[str], day: date) -> None:
    """`close` for each of `accounts` in one transaction: all are closed, or none is."""
    with db.transaction(conn):
        for account in accounts:
            _close(conn, owner, account, day)


def _close(conn: sqlite3.Connection, owner: str, account: str, day: date) -> None:
    owner_id, found = _account(conn, owner, account)
    if store.has_staged(conn, found.id):
        msg = f"{account} of {owner} has statements staged: commit or discard them first"
        raise ClosingError(msg)
    store.set_closed(conn, found.id, day)
    staging.refuse_after_closing(conn, owner_id, "close it on a later day")
    held = _held_on(conn, owner, owner_id, found.id, day)
    if held:
        typed = (
            ", or type the zero its fund shows ('scrooge balance')"
            if found.kind == "manual"
            else ""
        )
        msg = (
            f"{account} of {owner} held {held[0]} at the end of {day}: close it on a day it "
            f"held nothing{typed}"
        )
        raise ClosingError(msg)


def reopen(conn: sqlite3.Connection, *, owner: str, account: str) -> date:
    """Open a closed account again; returns the day it had been closed."""
    with db.transaction(conn):
        return _reopen(conn, owner, account)


def reopen_all(conn: sqlite3.Connection, *, owner: str, accounts: Sequence[str]) -> None:
    """Open each of `accounts` that is closed again, in one transaction; refused when none is."""
    with db.transaction(conn):
        shut = [a for a in accounts if _account(conn, owner, a)[1].closed is not None]
        if not shut:
            msg = f"{accounts[0]} of {owner} is not closed"
            raise ClosingError(msg)
        for account in shut:
            _reopen(conn, owner, account)


def _reopen(conn: sqlite3.Connection, owner: str, account: str) -> date:
    _, found = _account(conn, owner, account)
    if found.closed is None:
        msg = f"{account} of {owner} is not closed"
        raise ClosingError(msg)
    store.set_closed(conn, found.id, None)
    return found.closed


def booked_as(conn: sqlite3.Connection, owner: str, account: str) -> tuple[str, ...]:
    """The accounts the books keep what the accounts page lists as `account`: a broker's
    currencies together (`together`), the account itself alone otherwise."""
    found = [
        part
        for row in together.rows(conn, owner)
        if row.line.account == account
        for part in row.parts
    ]
    return tuple(dict.fromkeys(found)) or (account,)


def _account(conn: sqlite3.Connection, owner: str, name: str) -> tuple[int, store.Account]:
    owner_id = store.owner_id(conn, owner)
    found = None if owner_id is None else store.account_of(conn, owner_id, name)
    if owner_id is None or found is None:
        msg = f"{owner} has no account {name!r}"
        raise ClosingError(msg)
    return owner_id, found


def _held_on(
    conn: sqlite3.Connection, owner: str, owner_id: int, account: int, day: date
) -> list[str]:
    """What the account held at the end of `day`: its cash as net worth counts it (carried
    past the last day valued), and each instrument by its quantity."""
    found: list[str] = []
    valued = networth.value(conn, owner)
    index = -1 if valued is None else bisect_right(valued.days, day) - 1
    if valued is not None and index >= 0:
        found += [
            f"{series.values[index]:,.2f} {series.currency}"
            for series in valued.series
            if series.account_id == account
            and series.instrument_id is None
            and series.values[index]
        ]
    symbols = store.symbols(conn)
    found += [
        f"{holding.on(day).normalize():f} {symbols[holding.instrument_id]}"
        for holding in holdings.held(conn, owner_id=owner_id)
        if holding.account_id == account and holding.on(day)
    ]
    return found
