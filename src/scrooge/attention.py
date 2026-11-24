"""What needs a look besides the statements: committed rows still in the category of those no
rule matched, and recurring payments that changed lately.

The rows to categorise are those in the config's `unmatched` category, counted per person:
an import puts there what no rule knew, so that it needs no review before it is committed,
and the rows wait there until someone gives them a category of their own.

A recurring payment changed when its price went up in the last two months, or when it came
for the first time in the last three (the cash-flow page calls it new over the same three:
a monthly payment is only known as one once it came three times). Both are judged against
the last day of its rows, the day the list of recurring payments is as of, and only for those
still paid. What changed is converted at the rate of that day and rounded to the cent.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from scrooge import networth, query, recurring
from scrooge.analysis import CENT
from scrooge.extractors.common import month_end

UP_WITHIN = 2  # months in which a rise in price is news
NEW_WITHIN = 3  # months in which a payment that started is news


@dataclass(frozen=True, slots=True)
class Uncategorised:
    owner: str
    count: int


@dataclass(frozen=True, slots=True)
class Change:
    """A recurring payment that went up (`change` is by how much, a payment) or is new
    (`change` is what it costs, a payment); `change` is None when a rate is missing."""

    found: query.Recurrence
    kind: Literal["up", "new"]
    change: Decimal | None
    since: date  # the first payment at the new price, or the first payment of all


@dataclass(frozen=True, slots=True)
class Changes:
    currency: str
    changes: tuple[Change, ...]  # the latest first
    errors: tuple[networth.MissingRateError, ...]  # one per currency without a rate


def uncategorised(
    conn: sqlite3.Connection, owner: str | None, category: str
) -> tuple[Uncategorised, ...]:
    """How many committed rows of `owner`, or of each person, are still in `category`, by
    name; nobody when no category is named, or the database has none of that name."""
    if not category:
        return ()
    try:
        rows = query.run(conn, query.Filter(owner=owner, category=category)).rows
    except query.QueryError:
        return ()
    counted = Counter(row.owner for row in rows)
    return tuple(Uncategorised(name, counted[name]) for name in sorted(counted))


def months_before(day: date, months: int) -> date:
    """The same day `months` months before `day`, or the last of that month when it is
    shorter."""
    index = day.year * 12 + day.month - 1 - months
    end = month_end(date(index // 12, index % 12 + 1, 1))
    return end.replace(day=min(day.day, end.day))


def _changed(found: query.Recurrence) -> tuple[Literal["up", "new"], Decimal, date] | None:
    """Whether `found` went up or is new, by how much a payment (as paid, positive), and
    since when; None when neither."""
    if found.first > months_before(found.through, NEW_WITHIN):
        return "new", -found.latest.amount, found.first
    # The payments at the latest price, back to the one before them.
    same = 1
    while same < len(found.rows) and found.rows[-same - 1].amount == found.latest.amount:
        same += 1
    if same == len(found.rows):
        return None
    since, before = found.rows[-same].date, found.rows[-same - 1].amount
    if since <= months_before(found.through, UP_WITHIN) or found.latest.amount >= before:
        return None
    return "up", before - found.latest.amount, since


def changes(conn: sqlite3.Connection, *, owner: str | None, currency: str) -> Changes:
    """The recurring payments of `owner`, or of everyone, still paid that went up or started
    lately, with what changed in `currency`."""
    found = [
        (each.found, changed)
        for each in recurring.run(conn, owner=owner, currency=currency).active
        if (changed := _changed(each.found)) is not None
    ]
    listed: list[Change] = []
    errors: dict[str, networth.MissingRateError] = {}
    if found:
        rates = networth.rates_on(conn, [max(group.through for group, _ in found)])
        for group, (kind, paid, since) in found:
            amount: Decimal | None
            try:
                amount = networth.convert([paid], group.latest.currency, currency, rates)[0]
                amount = amount.quantize(CENT)
            except networth.MissingRateError as exc:
                amount = None
                errors.setdefault(str(exc), exc)
            listed.append(Change(group, kind, amount, since))
    listed.sort(key=lambda each: (each.since, each.found.name), reverse=True)
    return Changes(currency, tuple(listed), tuple(errors.values()))
