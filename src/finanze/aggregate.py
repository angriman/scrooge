"""Normalized transactions -> the rows of `Budget Mensile` (step 6 of the flow)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finanze import categories
from finanze.models import Kind, Transaction

_CENT = Decimal("0.01")


def _positions(order: tuple[str, ...]) -> dict[str, int]:
    return {category: position for position, category in enumerate(dict.fromkeys(order))}


# Each side lists its own categories first, in taxonomy order.
_USCITE_ORDER = _positions(categories.ORDER)
_ENTRATE_ORDER = _positions((*categories.ENTRATE, *categories.ORDER))


class UncategorizedError(Exception):
    """Transactions without a category cannot be aggregated: the user must decide first."""


@dataclass(frozen=True, slots=True)
class BudgetRow:
    category: str
    account: str
    amount: Decimal  # always positive; the side (Uscite/Entrate) carries the sign


@dataclass(frozen=True, slots=True)
class Budget:
    month: date
    uscite: tuple[BudgetRow, ...]
    entrate: tuple[BudgetRow, ...]


def aggregate(month: date, transactions: Iterable[Transaction]) -> Budget:
    """Sum by (category, account). The sign of each sum decides its side (D1); zero sums vanish.

    Internal rows are left out. Sums are rounded to the cent, as the sheet shows them.
    """
    sums: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for txn in transactions:
        if txn.kind is Kind.INTERNAL:
            continue
        if not txn.category:
            msg = f"no category for {txn.description!r} ({txn.account}, {txn.amount})"
            raise UncategorizedError(msg)
        sums[txn.category, txn.account] += txn.amount

    rows = [
        (BudgetRow(category, account, abs(amount)), amount < 0)
        for (category, account), total in sums.items()
        if (amount := total.quantize(_CENT))
    ]
    return Budget(
        month,
        uscite=_sorted((row for row, outgoing in rows if outgoing), _USCITE_ORDER),
        entrate=_sorted((row for row, outgoing in rows if not outgoing), _ENTRATE_ORDER),
    )


def _sorted(rows: Iterable[BudgetRow], order: dict[str, int]) -> tuple[BudgetRow, ...]:
    def key(row: BudgetRow) -> tuple[int, str, str]:
        return order.get(row.category, len(order)), row.category, row.account

    return tuple(sorted(rows, key=key))
