"""Recognise money between the two people where only one statement names the other (D14).

A bank may name the other person on one side only: Revolut shows "Transfer from <name>" to
whoever receives, and just its own name to whoever sent. The named side carries
`counterparty_owner` from the import. Here each such row looks for its other half among the
rows of the person it names: same bank, same currency, the opposite amount, at most a day
apart. A half that is named too needs nothing; one that is not gets `counterparty_owner`.

Derived data, redone on every import. A row of a committed month is never changed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

_APART = 1  # days between the two halves: one bank may book a late transfer the day after


@dataclass(slots=True)
class _Row:
    id: str
    owner: int
    named: int | None  # the other person, when the statement says so
    key: tuple[str, str]  # bank, currency
    amount: int
    day: date
    fixed: bool  # in a committed month
    paired: bool = False


def mark(conn: sqlite3.Connection) -> int:
    """Set `counterparty_owner` on the unnamed halves; returns how many were found."""
    rows = [
        _Row(row[0], row[1], row[2], (row[3], row[4]), row[5], date.fromisoformat(row[6]), row[7])
        for row in conn.execute(
            "SELECT t.id, a.owner_id, t.counterparty_owner_id, a.source, t.currency, "
            "t.amount_nano, t.date, coalesce(m.status = 'committed', 0) "
            "FROM transactions t JOIN accounts a ON a.id = t.account_id "
            "LEFT JOIN months m ON m.owner_id = a.owner_id "
            "AND m.month = date(t.booking_date, 'start of month') "
            "WHERE t.kind IN ('income', 'expense') AND a.source IS NOT NULL "
            "ORDER BY t.date, t.id"
        )
    ]
    named = [row for row in rows if row.named is not None]
    for row in named:  # two halves that name each other
        _pair(row, [other for other in named if other.named == row.owner])
    found = 0
    for row in named:
        half = _pair(row, [other for other in rows if other.named is None and not other.fixed])
        if half is not None:
            conn.execute(
                "UPDATE transactions SET counterparty_owner_id = ? WHERE id = ?",
                (row.owner, half.id),
            )
            found += 1
    return found


def _pair(row: _Row, candidates: list[_Row]) -> _Row | None:
    """The other half of `row` among `candidates`, the nearest in time; both are then taken."""
    if row.paired:
        return None
    halves = [
        other
        for other in candidates
        if not other.paired
        and other.owner == row.named
        and other.key == row.key
        and other.amount == -row.amount
        and abs((other.day - row.day).days) <= _APART
    ]
    if not halves:
        return None
    half = min(halves, key=lambda other: abs((other.day - row.day).days))
    row.paired = half.paired = True
    return half
