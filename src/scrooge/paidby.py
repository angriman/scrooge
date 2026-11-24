"""Who paid: each person's share of what the household spent over a range, in all and in each
top-level category, beside the cash-flow page's view of both people together.

Spending is counted as the spending view counts it for both people together: expenses and
refunds of them, each converted at the rate of its own day; what the two paid each other is
left out, as it only moves money inside the household. A row is paid by the person whose
account it was paid from; a person counts once they have statements committed.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge import analysis, query, spending
from scrooge.analysis import CENT
from scrooge.networth import DEFAULT_CURRENCY


@dataclass(frozen=True, slots=True)
class Payer:
    name: str
    spent: Decimal  # money out of their accounts, less what came back
    share: Decimal | None  # of all the household spent; None when nothing was


@dataclass(frozen=True, slots=True)
class Split:
    name: str  # top-level category
    spent: Decimal  # by everyone
    by: dict[str, Decimal]  # by each of `PaidBy.people`, in that order


@dataclass(frozen=True, slots=True)
class PaidBy:
    currency: str
    spent: Decimal  # by everyone: the spending view's figure for both people together
    people: tuple[Payer, ...]  # everyone with statements committed, by name
    categories: tuple[Split, ...]  # the largest first


def run(
    conn: sqlite3.Connection,
    *,
    first: date | None = None,
    last: date | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> PaidBy | None:
    """Who paid what the household spent from `first` to `last`, both included, in `currency`,
    ranges filled in as for spending. None with fewer than two people with statements
    committed, or with nothing to show."""
    # Someone with nothing committed yet has paid nothing that can be seen.
    names = [person.name for person in query.people(conn) if person.committed]
    if len(names) < 2:  # a household is more than one person
        return None
    found = analysis.rows(conn, None, currency, spending.is_expense)
    span = found.span(first, last)
    if span is None:
        return None
    by_person: dict[str, Decimal] = dict.fromkeys(names, Decimal(0))
    by_top: dict[str, dict[str, Decimal]] = defaultdict(lambda: dict.fromkeys(names, Decimal(0)))
    for each in found.between(span.start, span.end):
        by_person[each.row.owner] -= each.amount
        by_top[each.row.top][each.row.owner] -= each.amount
    total = sum(by_person.values(), Decimal(0))
    categories = [
        Split(
            top,
            sum(amounts.values(), Decimal(0)).quantize(CENT),
            {name: amount.quantize(CENT) for name, amount in amounts.items()},
        )
        for top, amounts in by_top.items()
    ]
    return PaidBy(
        currency,
        total.quantize(CENT),
        tuple(
            Payer(name, amount.quantize(CENT), analysis.share(amount, total))
            for name, amount in by_person.items()
        ),
        tuple(sorted(categories, key=lambda split: (-split.spent, split.name))),
    )
