"""The payments that come back at a steady pace, subscriptions among them, and what they cost
a month: the panel of the cash-flow page.

Only money spent, as the cash-flow page counts it, is listed: a salary comes back each month
too, but it is not something one pays for, and neither is a standing transfer to one's own
broker or pension fund, or (for both people together) what the two pay each other. What each
costs a month is converted at the rate of the last day of the statements, the day the list is
as of, and rounded to the cent.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from scrooge import cashflow, networth, query, spending
from scrooge.analysis import CENT


@dataclass(frozen=True, slots=True)
class Payment:
    """One recurrence, and what its latest amount costs a month in the currency asked for;
    None when a rate is missing."""

    found: query.Recurrence
    monthly: Decimal | None


@dataclass(frozen=True, slots=True)
class Recurring:
    currency: str
    active: tuple[Payment, ...]  # the most a month first
    ended: tuple[Payment, ...]  # the last paid most recently first
    monthly: Decimal | None  # what the active ones cost a month; None when a rate is missing
    errors: tuple[networth.MissingRateError, ...]  # one per currency without a rate


def run(conn: sqlite3.Connection, *, owner: str | None, currency: str) -> Recurring:
    """The recurring payments of `owner`, or of everyone, with their monthly cost in
    `currency`."""
    rows = query.run(conn, query.Filter(owner=owner)).rows
    spent = {row.id for row in cashflow.counted(conn, owner) if spending.is_spent(row)}
    found = [
        group
        for group in query.recurrences(rows)
        if group.latest.amount < 0 and group.latest.id in spent
    ]
    errors: dict[str, networth.MissingRateError] = {}
    payments = []
    if found:
        rates = networth.rates_on(conn, [found[0].through])
        for group in found:
            cost: Decimal | None
            try:
                cost = networth.convert([-group.monthly], group.latest.currency, currency, rates)[0]
                cost = cost.quantize(CENT)
            except networth.MissingRateError as exc:
                cost = None
                errors.setdefault(str(exc), exc)
            payments.append(Payment(group, cost))
    active = [each for each in payments if not each.found.ended]
    active.sort(key=lambda each: (each.monthly is None, -(each.monthly or 0), each.found.name))
    ended = sorted(
        (each for each in payments if each.found.ended), key=lambda each: each.found.last
    )[::-1]
    costs = [each.monthly for each in active]
    total = None if None in costs else sum((c for c in costs if c is not None), Decimal(0))
    return Recurring(currency, tuple(active), tuple(ended), total, tuple(errors.values()))
