"""What investments paid: dividends and interest, and the tax withheld from them, month by
month and holding by holding. The income view of the investments page.

The rows are those of the categories `Dividendi`, `Interessi` and `Ritenute` that the cash-flow
page counts, each converted at the rate of its own day into one currency, as the analysis views
do. The range runs, unless asked otherwise, over the twelve months up to the last day committed:
the month of that day and the eleven before it.

A row carries no link to an instrument: what paid it is named, if at all, only in its
counterparty. IBKR, Tastytrade and Schwab write the symbol there, Finpension the fund's ISIN,
and the interest split off a bill that matured the bill's CUSIP. A holding is that name, in
capitals with its spaces tidied; it takes the name of the instrument whose symbol it is, since
`instruments` keeps a ticker, an ISIN or a CUSIP as the statements have it. A row that names
nothing (a dividend at Morgan Stanley, interest on cash) is put under the account it was paid
into. So the grouping is only as good as the statements' counterparties: a ticker the broker
renamed shows under each of its names, and tax withheld under another name than its dividend
is a holding of its own.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from scrooge import analysis, query
from scrooge.analysis import CENT, Converted
from scrooge.cashflow import Counted, Grouping
from scrooge.networth import DEFAULT_CURRENCY

DIVIDENDS, INTEREST, TAX = "Dividendi", "Interessi", "Ritenute"
CATEGORIES = (DIVIDENDS, INTEREST, TAX)


@dataclass(frozen=True, slots=True)
class Paid:
    """What was paid, and withheld, in one currency."""

    dividends: Decimal
    interest: Decimal
    tax: Decimal  # withheld: negative, as stored

    @property
    def paid(self) -> Decimal:
        """Dividends and interest, before tax."""
        return self.dividends + self.interest

    @property
    def net(self) -> Decimal:
        """What was paid, less what was withheld."""
        return self.paid + self.tax


@dataclass(frozen=True, slots=True)
class Month:
    name: str  # `2026-05`
    paid: Paid


@dataclass(frozen=True, slots=True)
class Holding:
    """What one holding, or one account's rows that name none, paid over the range."""

    key: str  # the counterparty as the rows are grouped by it, or the account's name
    name: str  # the instrument's name, else its symbol, else the key
    symbol: str | None  # as `instruments` has it; None when no instrument has it
    account: str | None  # the account, for rows that name nothing; else None
    paid: Paid
    count: int  # rows, tax withheld included
    last: date  # the last day it paid; the last day tax was withheld when it only had that


@dataclass(frozen=True, slots=True)
class Payouts:
    currency: str
    first: date | None  # the range shown; None: nothing is committed
    last: date | None
    totals: Paid
    year_before: Paid | None  # the same months a year earlier; None when not all known
    months: tuple[Month, ...]  # every month of the range, oldest first
    holdings: tuple[Holding, ...]  # what was paid the most first
    through: date | None  # the last day committed for everyone shown


NOTHING = Paid(Decimal(0), Decimal(0), Decimal(0))


@dataclass(slots=True)
class _Sums:
    amounts: dict[str, Decimal] = field(
        default_factory=lambda: dict.fromkeys(CATEGORIES, Decimal(0))
    )
    count: int = 0
    paid_last: date | None = None  # the last day of a dividend or interest
    any_last: date = date.min  # the last day of any row

    def add(self, each: Converted) -> None:
        """Add a row; rows are added oldest first."""
        self.amounts[each.row.leaf] += each.amount
        self.count += 1
        self.any_last = each.day
        if each.row.leaf != TAX:
            self.paid_last = each.day

    def paid(self) -> Paid:
        return Paid(*(self.amounts[name].quantize(CENT) for name in CATEGORIES))


def is_payout(row: Counted) -> bool:
    return row.leaf in CATEGORIES


def run(
    conn: sqlite3.Connection,
    *,
    first: date | None = None,
    last: date | None = None,
    owner: str | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> Payouts:
    """What the investments of `owner`, or of everyone, paid from `first` to `last`, both
    included, in `currency`. Without `last` the range ends with the last day committed;
    without `first` it starts eleven months before the month of its end."""
    found = analysis.rows(conn, owner, currency, is_payout)
    if found.through is None:
        return Payouts(currency, None, None, NOTHING, None, (), (), None)
    end = last or found.through
    start = first or analysis.add_months(end, -11)
    if start > end:
        return Payouts(currency, start, end, NOTHING, None, (), (), found.through)
    span = analysis.Span(start, end, found.through, _known(conn, owner) or start)
    months = tuple(
        Month(name, _sum(inside).paid())
        for name, _, inside in found.by_period(span, Grouping.MONTH)
    )
    before = span.year_before()
    return Payouts(
        currency,
        start,
        end,
        _sum(found.between(start, end)).paid(),
        None if before is None else _sum(found.between(*before)).paid(),
        months,
        _holdings(conn, found.between(start, end)),
        found.through,
    )


def _sum(rows: Iterable[Converted]) -> _Sums:
    sums = _Sums()
    for each in rows:
        sums.add(each)
    return sums


def _holdings(conn: sqlite3.Connection, rows: Iterable[Converted]) -> tuple[Holding, ...]:
    """The rows grouped by what paid them, what was paid the most first."""
    named: dict[str, tuple[str, str]] = {}  # symbol in capitals -> as stored, and its name
    for symbol, name in conn.execute("SELECT symbol, name FROM instruments ORDER BY id"):
        if not named.get(symbol.upper(), ("", ""))[1]:
            named[symbol.upper()] = (symbol, name)
    groups: dict[tuple[str, str | None], _Sums] = {}
    for each in rows:
        key = " ".join(each.row.counterparty.split()).upper()
        where = (key, None) if key else (each.row.account, each.row.account)
        groups.setdefault(where, _Sums()).add(each)
    holdings = []
    for (key, account), sums in groups.items():
        symbol, name = named.get(key, (None, "")) if account is None else (None, "")
        last = sums.paid_last or sums.any_last
        holdings.append(
            Holding(key, name or symbol or key, symbol, account, sums.paid(), sums.count, last)
        )
    holdings.sort(key=lambda holding: (-holding.paid.paid, holding.name))
    return tuple(holdings)


def _known(conn: sqlite3.Connection, owner: str | None) -> date | None:
    """The first day of the month from which every person shown has statements committed."""
    firsts = [
        person.first.replace(day=1)
        for person in query.people(conn)
        if person.first is not None and owner in (None, person.name)
    ]
    return max(firsts, default=None)
