"""Who paid: each person's share of what the household spent over a range, in all and in each
top-level category, beside the cash-flow page's view of both people together.

Spending is counted as the spending view counts it for both people together: expenses and
refunds of them, each converted at the rate of its own day; what the two paid each other is
left out of it, as it only moves money inside the household. A row is paid by the person whose
account it was paid from; a person counts once statements of theirs are committed.

What the two paid each other is shown beside it instead, as the net that went from one to the
other, and each person's spending after settling up: what they spent, and sent the other, less
what the other sent them. A payment is read in the statements of whoever sent it, so that one
seen on both sides counts once.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import combinations

from scrooge import analysis, cashflow, query, spending
from scrooge.analysis import CENT
from scrooge.cashflow import Counted
from scrooge.networth import DEFAULT_CURRENCY
from scrooge.storage import store


@dataclass(frozen=True, slots=True)
class Payer:
    name: str
    spent: Decimal  # money out of their accounts, less what came back
    share: Decimal | None  # of all the household spent; None when nothing was
    settled: Decimal  # `spent`, and what they sent the others, less what the others sent them
    settled_share: Decimal | None  # of all the household spent


@dataclass(frozen=True, slots=True)
class Move:
    """The net of what two people paid each other over the range."""

    sender: str
    receiver: str
    amount: Decimal  # above zero


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
    moves: tuple[Move, ...]  # between each two people who paid each other, by name


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
    # Someone with no statement committed has paid nothing that can be seen.
    names = [person.name for person in query.people(conn) if person.last is not None]
    if len(names) < 2:  # a household is more than one person
        return None
    found = analysis.rows(conn, None, currency, spending.is_expense)
    span = found.span(first, last)
    if span is None:
        return None
    by_person: dict[str, Decimal] = dict.fromkeys(names, Decimal(0))
    by_top: dict[str, dict[str, Decimal]] = defaultdict(lambda: dict.fromkeys(names, Decimal(0)))
    for each in found.between(span.start, span.end):
        owner, amounts = each.row.owner, by_top[each.row.top]
        by_person[owner] = by_person.get(owner, Decimal(0)) - each.amount
        amounts[owner] = amounts.get(owner, Decimal(0)) - each.amount
    total = sum(by_person.values(), Decimal(0))
    sent = _sent(conn, names, span, currency)
    settled = dict(by_person)
    for (sender, receiver), amount in sent.items():
        settled[sender] += amount
        settled[receiver] -= amount
    categories = [
        Split(
            top,
            sum(amounts.values(), Decimal(0)).quantize(CENT),
            {name: amount.quantize(CENT) for name, amount in amounts.items()},
        )
        for top, amounts in by_top.items()
    ]
    ranks = store.category_ranks(conn)
    return PaidBy(
        currency,
        total.quantize(CENT),
        tuple(
            Payer(
                name,
                amount.quantize(CENT),
                analysis.share(amount, total),
                settled[name].quantize(CENT),
                analysis.share(settled[name], total),
            )
            for name, amount in by_person.items()
        ),
        tuple(
            sorted(
                categories,
                key=lambda split: (-split.spent, ranks.top(split.name, cashflow.EXPENSES)),
            )
        ),
        _moves(names, sent),
    )


def _between(row: Counted) -> bool:
    """What one person paid the other, or got from them, outside investing."""
    return row.counterparty_owner is not None and not row.investing


def _sent(
    conn: sqlite3.Connection, names: list[str], span: analysis.Span, currency: str
) -> dict[tuple[str, str], Decimal]:
    """(sender, receiver) -> what one person paid another over the range, as the sender's
    statements have it: a payment is seen there, and again in the receiver's, once."""
    sent: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for name in names:
        for each in analysis.rows(conn, name, currency, _between).between(span.start, span.end):
            receiver = each.row.counterparty_owner
            if each.amount < 0 and receiver in names:
                sent[name, receiver] -= each.amount
    return sent


def _moves(names: list[str], sent: dict[tuple[str, str], Decimal]) -> tuple[Move, ...]:
    moves = []
    for one, other in combinations(sorted(names), 2):
        net = sent.get((one, other), Decimal(0)) - sent.get((other, one), Decimal(0))
        net = net.quantize(CENT)
        if net > 0:
            moves.append(Move(one, other, net))
        elif net < 0:
            moves.append(Move(other, one, -net))
    return tuple(moves)
