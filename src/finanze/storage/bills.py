"""Split what a bill pays back into cost and interest, first in, first out (D13).

A bill sold, and at Tastytrade also a bill that matures, comes out of the extractor whole, as
`Investimenti`. With the purchases in the database the cost of the lots it closes is known:
the row keeps that cost, and the rest becomes a row of its own under `Interessi`. (An IBKR
maturity is not a trade: its extractor splits it from the profit the statement prints.)

The split is derived data. `split` first undoes every earlier split of the owner and then
redoes them all, so importing an earlier month later corrects the months after it.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from itertools import groupby

from finanze.validation import MissingLotsError

_INTEREST = "Interessi"


@dataclass(frozen=True, slots=True)
class Event:
    """A purchase (`quantity` > 0, `cost_micro` paid) or a disposal (`quantity` < 0)."""

    txn_id: str
    quantity: Decimal
    cost_micro: int = 0


def fifo_costs(events: Iterable[Event]) -> dict[str, int | None]:
    """What each disposal's lots cost, oldest lots first; None where lots are missing.

    A lot closed in part gives up its cost in proportion, rounded to the millionth; the rest
    stays with the lot, so the costs handed out never add up to more or less than was paid.
    """
    lots: deque[list[Decimal | int]] = deque()  # [quantity left, cost left]
    costs: dict[str, int | None] = {}
    for event in events:
        if event.quantity > 0:
            lots.append([event.quantity, event.cost_micro])
            continue
        needed, cost = -event.quantity, 0
        while needed and lots:
            left, paid = lots[0]
            taken = min(needed, Decimal(left))
            share = int((Decimal(paid) * taken / left).to_integral_value(ROUND_HALF_EVEN))
            lots[0] = [left - taken, paid - share]
            if taken == left:
                lots.popleft()
            needed -= taken
            cost += share
        costs[event.txn_id] = None if needed else cost
    return costs


def split(conn: sqlite3.Connection, owner: int) -> list[MissingLotsError]:
    """Redo the interest split of every bill disposal of `owner`; returns those it could not do."""
    _undo(conn, owner)
    interest = conn.execute(
        "SELECT id FROM categories WHERE side = 'entrate' AND name = ?", (_INTEREST,)
    ).fetchone()[0]
    missing: list[MissingLotsError] = []
    for _, rows in groupby(_bill_trades(conn, owner), key=lambda r: (r["account"], r["symbol"])):
        history = list(rows)
        costs = fifo_costs(
            Event(r["id"], Decimal(r["quantity"]), int(r["cost_micro"])) for r in history
        )
        for row in history:
            if row["id"] not in costs:
                continue
            cost = costs[row["id"]]
            if cost is None:
                day = date.fromisoformat(row["date"])
                missing.append(MissingLotsError(row["account"], row["symbol"], day))
            elif cost != row["amount_micro"]:
                _split_off(conn, row["id"], cost, row["amount_micro"] - cost, interest)
    return missing


def _undo(conn: sqlite3.Connection, owner: int) -> None:
    parts = (
        "FROM transactions part JOIN accounts a ON a.id = part.account_id "
        "WHERE a.owner_id = ? AND part.split_of"
    )
    conn.execute(
        "UPDATE transactions SET amount_micro = amount_micro + "  # noqa: S608  # `parts` is the literal above
        f"(SELECT part.amount_micro {parts} = transactions.id) "
        f"WHERE id IN (SELECT part.split_of {parts} IS NOT NULL)",
        (owner, owner),
    )
    conn.execute(
        "DELETE FROM transactions WHERE split_of IS NOT NULL AND account_id IN "
        "(SELECT id FROM accounts WHERE owner_id = ?)",
        (owner,),
    )


def _bill_trades(conn: sqlite3.Connection, owner: int) -> Sequence[sqlite3.Row]:
    """Every bill trade of the owner, in the order lots open and close."""
    return conn.execute(
        "SELECT t.id, a.name AS account, i.symbol, t.date, t.amount_micro, "
        "tr.quantity, tr.cost_micro "
        "FROM trades tr JOIN transactions t ON t.id = tr.transaction_id "
        "JOIN accounts a ON a.id = t.account_id JOIN instruments i ON i.id = tr.instrument_id "
        "WHERE a.owner_id = ? AND i.type = 'bill' "
        # on one day, purchases come before disposals
        "ORDER BY a.name, i.symbol, t.date, tr.quantity LIKE '-%', t.id",
        (owner,),
    ).fetchall()


def _split_off(
    conn: sqlite3.Connection, txn_id: str, cost: int, interest: int, category: int
) -> None:
    conn.execute("UPDATE transactions SET amount_micro = ? WHERE id = ?", (cost, txn_id))
    conn.execute(
        "INSERT INTO transactions (id, account_id, date, booking_date, amount_micro, currency, "
        "description, counterparty, kind, category_id, category_source, counterparty_owner_id, "
        "statement_file_id, source_ref, confidence, split_of) "
        "SELECT id || ':interest', account_id, date, booking_date, ?, currency, "
        "description || ' (interest)', counterparty, 'interest', ?, 'rule', NULL, "
        "statement_file_id, source_ref || ':interest', confidence, id "
        "FROM transactions WHERE id = ?",
        (interest, category, txn_id),
    )
