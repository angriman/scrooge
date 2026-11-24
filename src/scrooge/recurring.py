"""The payments that come back at a steady pace, subscriptions among them, and what they cost
a month: the panel of the cash-flow page.

Only money spent, as the cash-flow page counts it, is listed: a salary comes back each month
too, but it is not something one pays for, and neither is a standing transfer to one's own
broker or pension fund, or (for both people together) what the two pay each other. What each
costs a month is converted at the rate of the last day of the statements, the day the list is
as of, and rounded to the cent. Each payment is named as the cash flow names its merchant, so
the transactions page can list its rows by that name, when no other payee shares it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge import cashflow, networth, query, spending
from scrooge.analysis import CENT


@dataclass(frozen=True, slots=True)
class Payment:
    """One recurrence, and what its latest amount costs a month in the currency asked for;
    None when a rate is missing. `alone` when the merchant filter of the transactions, with
    its account and person, lists its own counterparty's rows and nobody else's."""

    found: query.Recurrence
    monthly: Decimal | None
    alone: bool = False


@dataclass(frozen=True, slots=True)
class Recurring:
    currency: str
    through: date | None  # the last day of the rows looked at; None: there are none
    active: tuple[Payment, ...]  # the most a month first
    ended: tuple[Payment, ...]  # the last paid most recently first
    monthly: Decimal | None  # what the active ones cost a month; None when a rate is missing
    errors: tuple[networth.MissingRateError, ...]  # one per currency without a rate


def run(conn: sqlite3.Connection, *, owner: str | None, currency: str) -> Recurring:
    """The recurring payments of `owner`, or of everyone, with their monthly cost in
    `currency`."""
    rows = query.run(conn, query.Filter(owner=owner)).rows
    # For everyone, what the two pay each other is not counted; for one person it is spent.
    spent = {row.id for row in cashflow.counted(conn, owner) if spending.is_spent(row)}
    found = [
        group
        for group in query.recurrences(rows)
        if group.latest.amount < 0 and group.latest.id in spent
    ]
    through = max((row.date for row in rows), default=None)
    errors: dict[str, networth.MissingRateError] = {}
    payments = []
    if found and through is not None:
        rates = networth.rates_on(conn, [through])
        alone = _alone(conn, found)
        for group in found:
            cost: Decimal | None
            try:
                cost = networth.convert([-group.monthly], group.latest.currency, currency, rates)[0]
                cost = cost.quantize(CENT)
            except networth.MissingRateError as exc:
                cost = None
                errors.setdefault(str(exc), exc)
            payments.append(Payment(group, cost, group.id in alone))
    active = [each for each in payments if not each.found.ended]
    active.sort(key=lambda each: (each.monthly is None, -(each.monthly or 0), each.found.name))
    ended = sorted(
        (each for each in payments if each.found.ended), key=lambda each: each.found.last
    )[::-1]
    costs = [each.monthly for each in active]
    total = None if None in costs else sum((c for c in costs if c is not None), Decimal(0))
    return Recurring(currency, through, tuple(active), tuple(ended), total, tuple(errors.values()))


def _alone(conn: sqlite3.Connection, found: list[query.Recurrence]) -> set[str]:
    """The ids of the groups whose rows the transactions page lists by merchant, account and
    person: those whose name no other counterparty of that account is paid under. A name that is
    the account's own is never one: every expense that names nobody is given it."""
    payees: dict[tuple[str, str, str], set[str]] = {}
    for whose in {group.owner for group in found}:
        for row in cashflow.counted(conn, whose):
            if spending.is_expense(row):
                key = (whose, row.account, spending.merchant(row).casefold())
                payees.setdefault(key, set()).add(row.counterparty.strip().casefold())
    return {
        group.id
        for group in found
        if group.name.casefold() != group.account.casefold()
        and payees.get((group.owner, group.account, group.name.casefold()))
        == {group.latest.counterparty.strip().casefold()}
    }
