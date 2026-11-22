"""What each account held of each instrument on any day, and whether the statements agree.

The quantity held is derived: the trades and the shares moved in kind add up, a split
multiplies what is held on its day, and a statement that prints its holdings is a known point.
From a known point the quantity runs forward with what was booked since; before the first one
it runs backward, so that a history which starts with shares already held is right too. A
statement that prints something else than what the rows before it lead to is a mismatch: a
missing statement, or a split that is not stored yet.

Nothing here is stored (migration 0001: nothing computable is), and nothing here writes.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finanze.validation import HoldingsMismatchError


def _committed(file: str, wanted: bool) -> str:
    """The condition that keeps the rows of committed statements only (and those typed by
    hand), as the dashboards read them. `file` is the column naming the row's statement."""
    if not wanted:
        return ""
    return (
        f"AND ({file} IS NULL OR EXISTS (SELECT 1 FROM statement_files cf "  # noqa: S608  # `file` is a column name given by the callers below
        f"WHERE cf.id = {file} AND cf.committed_at IS NOT NULL)) "
    )


# On one day: a split comes first (it applies to what was held overnight), then what was
# booked that day, then what a statement says was held at its end, then what was typed.
_SPLIT, _TRADE, _PRINTED, _TYPED = 0, 1, 2, 3
_KNOWN = (_PRINTED, _TYPED)  # what says the quantity held, rather than moving it


@dataclass(frozen=True, slots=True)
class Change:
    """Something that moves the quantity held on `day`. `kind` is one of the three above:
    a split multiplies by `amount` and divides by `per` (kept apart: one for three is no
    exact decimal), a trade adds `amount`, a statement says it is the quantity, and so does a
    quantity typed by hand (`finanze position`), which is never a mismatch: it is what was
    held from then on."""

    day: date
    kind: int
    amount: Decimal
    per: Decimal = Decimal(1)


@dataclass(frozen=True, slots=True)
class Step:
    """`quantity` is held from the end of `day` until the next step."""

    day: date
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class Mismatch:
    """A statement printed `printed` for the end of `day`; the rows before lead to `derived`."""

    day: date
    printed: Decimal
    derived: Decimal


@dataclass(frozen=True, slots=True)
class Holding:
    """One instrument in one account over time."""

    account_id: int
    instrument_id: int
    opening: Decimal  # held before the first step: zero unless the history starts later
    steps: tuple[Step, ...]  # one per day on which something changed, oldest first
    mismatches: tuple[Mismatch, ...]

    def on(self, day: date) -> Decimal:
        """What was held at the end of `day`."""
        quantity = self.opening
        for step in self.steps:
            if step.day > day:
                break
            quantity = step.quantity
        return quantity


def timeline(changes: Iterable[Change]) -> tuple[Decimal, list[Step], list[Mismatch]]:
    """The quantity held after each day of `changes`: (opening, steps, mismatches).

    Every statement after the first is checked against what the changes before it lead to,
    and the quantity goes on from what it prints.
    """
    ordered = sorted(changes, key=lambda change: (change.day, change.kind))
    quantity = _opening(ordered)
    opening = quantity
    steps: list[Step] = []
    mismatches: list[Mismatch] = []
    for change in ordered:
        if change.kind == _SPLIT:
            quantity = quantity * change.amount / change.per
        elif change.kind == _TRADE:
            quantity += change.amount
        elif change.kind == _TYPED:
            quantity = change.amount
        else:
            if quantity != change.amount:
                mismatches.append(Mismatch(change.day, change.amount, quantity))
            quantity = change.amount
        if steps and steps[-1].day == change.day:
            steps[-1] = Step(change.day, quantity)
        else:
            steps.append(Step(change.day, quantity))
    return opening, steps, mismatches


def _opening(ordered: Sequence[Change]) -> Decimal:
    """What must have been held before the first change for the first statement to be right.

    Zero when no statement prints the holdings: the trades are then all there is.
    """
    first = next((n for n, change in enumerate(ordered) if change.kind in _KNOWN), None)
    if first is None:
        return Decimal(0)
    quantity = ordered[first].amount
    for change in reversed(ordered[:first]):
        if change.kind == _SPLIT:
            quantity = quantity * change.per / change.amount
        else:
            quantity -= change.amount
    return quantity


def held(
    conn: sqlite3.Connection, *, owner_id: int | None = None, committed: bool = False
) -> list[Holding]:
    """Every instrument of every account of `owner_id` (or of everyone) over time.

    With `committed`, only what committed statements book or print is read.
    """
    changes: dict[tuple[int, int], list[Change]] = defaultdict(list)
    for account, instrument, day, quantity in conn.execute(
        "SELECT t.account_id, tr.instrument_id, t.booking_date, tr.quantity "
        "FROM trades tr JOIN transactions t ON t.id = tr.transaction_id "
        "JOIN accounts a ON a.id = t.account_id "
        "WHERE a.owner_id = coalesce(?, a.owner_id) "
        + _committed("t.statement_file_id", committed),
        (owner_id,),
    ):
        changes[account, instrument].append(
            Change(date.fromisoformat(day), _TRADE, Decimal(quantity))
        )
    for account, instrument, day, quantity, fed in conn.execute(
        "SELECT p.account_id, p.instrument_id, p.date, p.quantity, a.source IS NOT NULL "
        "FROM positions p JOIN accounts a ON a.id = p.account_id "
        "WHERE p.source = 'manual' AND a.owner_id = coalesce(?, a.owner_id)",
        (owner_id,),
    ):
        # Typed for one instrument: it says nothing of the account's others. In an account an
        # export feeds, it is a known point the rows must agree with, as a printed one is.
        kind = _PRINTED if fed else _TYPED
        changes[account, instrument].append(
            Change(date.fromisoformat(day), kind, Decimal(quantity))
        )
    printed = _printed(conn, owner_id, committed=committed)
    for (account, day), quantities in printed.items():
        # A statement that prints its holdings says of everything else that none was held.
        instruments = {i for a, i in changes if a == account} | quantities.keys()
        for instrument in instruments:
            quantity = quantities.get(instrument, Decimal(0))
            if quantity is not None:
                changes[account, instrument].append(Change(day, _PRINTED, quantity))
    splits: dict[int, list[Change]] = defaultdict(list)
    for instrument, day, numerator, denominator in conn.execute(
        "SELECT instrument_id, date, numerator, denominator FROM splits"
    ):
        splits[instrument].append(
            Change(date.fromisoformat(day), _SPLIT, Decimal(numerator), Decimal(denominator))
        )
    holdings: list[Holding] = []
    for (account, instrument), moves in sorted(changes.items()):
        opening, steps, mismatches = timeline([*moves, *splits.get(instrument, [])])
        holdings.append(Holding(account, instrument, opening, tuple(steps), tuple(mismatches)))
    return holdings


def _printed(
    conn: sqlite3.Connection, owner_id: int | None, *, committed: bool
) -> dict[tuple[int, date], dict[int, Decimal | None]]:
    """(account, day) -> what each statement that prints its holdings says was held.

    The accounts of a statement are those it gives a balance for, and those it says hold
    something. None stands for a quantity that says nothing: it includes shares not yet
    vested.
    """
    printed: dict[tuple[int, date], dict[int, Decimal | None]] = {}
    for account, day in conn.execute(
        "SELECT DISTINCT b.account_id, f.period_end FROM statement_files f "  # noqa: S608  # only fixed text is joined
        "JOIN balances b ON b.statement_file_id = f.id "
        "JOIN accounts a ON a.id = b.account_id "
        "WHERE f.holdings = 'printed' AND a.owner_id = coalesce(?, a.owner_id) "
        + _committed("f.id", committed),
        (owner_id,),
    ):
        printed[account, date.fromisoformat(day)] = {}
    for account, day, instrument, quantity, unvested in conn.execute(
        "SELECT p.account_id, p.date, p.instrument_id, p.quantity, p.unvested FROM positions p "  # noqa: S608  # only fixed text is joined
        "JOIN accounts a ON a.id = p.account_id "
        "WHERE p.source = 'statement' AND a.owner_id = coalesce(?, a.owner_id) "
        + _committed("p.statement_file_id", committed),
        (owner_id,),
    ):
        known = None if unvested else Decimal(quantity)
        printed.setdefault((account, date.fromisoformat(day)), {})[instrument] = known
    return printed


def check(conn: sqlite3.Connection, owner_id: int) -> list[HoldingsMismatchError]:
    """Where a statement of the owner prints other holdings than the rows before it lead to."""
    accounts = dict(conn.execute("SELECT id, name FROM accounts").fetchall())
    symbols = dict(conn.execute("SELECT id, symbol FROM instruments").fetchall())
    return [
        HoldingsMismatchError(
            accounts[holding.account_id],
            symbols[holding.instrument_id],
            mismatch.day,
            mismatch.printed,
            mismatch.derived,
        )
        for holding in held(conn, owner_id=owner_id)
        for mismatch in holding.mismatches
    ]
