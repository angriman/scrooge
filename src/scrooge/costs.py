"""What investing costs, year by year: the commissions the brokers charged and the tax withheld
from what investments paid. The costs under the income view of the investments page.

**Commissions** are the rows of the category `Commissioni` on the accounts a broker's statements
feed (`query.ACCOUNTS["broker"]`): what a trade cost at IBKR or Tastytrade, a regulatory
fee, the administrative fee of the pension fund at Finpension. A bank's fees have the same
category but are no cost of investing, and are left out; so is the fee Revolut's savings fund
takes from its interest, booked on the bank account. **Tax withheld** is every row of the
category `Ritenute`, as the income view counts it (`payouts`), wherever it was withheld.
Both are rows of their own as the statements are read: IBKR and Tastytrade write each
commission apart from its trade, and IBKR, Morgan Stanley and Schwab the tax apart from its
dividend. Morgan Stanley's statements charge no commission, and a Schwab export with one is
refused when it is read, so neither has any here.

A cost is what was paid out, so it is above zero; a commission refunded lowers it. Each row is
converted at the rate of its own day into one currency, as the analysis views do, and the rows
of a currency no rate is stored for are left out, and said to be, rather than leaving the
whole panel empty. Years are calendar years, from the first one with statements committed to
that of the last day committed, which is counted so far.

The tax is also a share of what investments paid (dividends and interest before tax, as
`payouts` adds them up) in the same year. Commissions are not set against what was invested:
what the accounts were worth day by day is the performance view's, which leaves the pension
funds out, and working it out again here would cost more than the panel is worth.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge import analysis, payouts, query
from scrooge.analysis import CENT, Converted
from scrooge.cashflow import Counted
from scrooge.networth import DEFAULT_CURRENCY, MissingRateError, convert, rates_on

COMMISSIONS, TAX = "Commissioni", payouts.TAX
_BROKERS = query.ACCOUNTS["broker"]  # the kinds of account a broker feeds


@dataclass(frozen=True, slots=True)
class Cost:
    """What investing cost, above zero: a refund lowers it."""

    commissions: Decimal
    tax: Decimal  # withheld

    @property
    def total(self) -> Decimal:
        return self.commissions + self.tax


@dataclass(frozen=True, slots=True)
class Year:
    year: int
    cost: Cost
    paid: Decimal  # dividends and interest before tax, as `payouts` adds them up
    last: date  # the last day counted: the year's own, or the last day committed

    @property
    def whole(self) -> bool:
        """Whether every day of the year is counted; the year of the last day committed is
        counted so far."""
        return self.last == date(self.year, 12, 31)

    @property
    def tax_share(self) -> Decimal | None:
        """The tax withheld as a part of what was paid; None when nothing was."""
        return analysis.share(self.cost.tax, self.paid)


@dataclass(frozen=True, slots=True)
class Account:
    """What one account cost, every year together."""

    owner: str
    account: str  # as stored
    name: str  # the account's, with its owner's after it when two people have one of its name
    cost: Cost
    broker: bool  # a broker's: its commissions count; else only its tax withheld does


@dataclass(frozen=True, slots=True)
class Costs:
    currency: str
    through: date | None  # the last day committed for everyone shown; None: nothing is
    years: tuple[Year, ...]  # oldest first
    totals: Cost  # every year together
    paid: Decimal
    accounts: tuple[Account, ...]  # what cost the most first; those that cost nothing left out
    missing: tuple[str, ...] = ()  # why rows were left out: a currency without a rate

    @property
    def tax_share(self) -> Decimal | None:
        return analysis.share(self.totals.tax, self.paid)


NOTHING = Cost(Decimal(0), Decimal(0))


def run(
    conn: sqlite3.Connection, *, owner: str | None = None, currency: str = DEFAULT_CURRENCY
) -> Costs:
    """What investing cost `owner`, or everyone, each year in `currency`. The rows of a
    currency that cannot be converted into `currency` are left out, and `missing` says why."""
    brokers = {
        (str(whose), str(name))
        for whose, name in conn.execute(
            "SELECT o.name, a.name FROM accounts a JOIN owners o ON o.id = a.owner_id "
            "WHERE a.kind IN (?, ?)",
            _BROKERS,
        )
    }
    rates = rates_on(conn, ())
    why: dict[str, str | None] = {}  # currency -> why its rows cannot be converted, if not

    def wanted(row: Counted) -> bool:
        if row.leaf != TAX and (row.leaf != COMMISSIONS or (row.owner, row.account) not in brokers):
            return False
        if row.currency not in why:
            try:
                convert((), row.currency, currency, rates)
                why[row.currency] = None
            except MissingRateError as exc:
                why[row.currency] = str(exc)
        return why[row.currency] is None

    found = analysis.rows(conn, owner, currency, wanted)
    firsts = [p.first for p in query.people(conn) if p.first and owner in (None, p.name)]
    if found.through is None:
        return Costs(currency, None, (), NOTHING, Decimal(0), (), _missing(why, ()))
    end = found.through
    start = date(min(firsts, default=end).year, 1, 1)
    paid = payouts.run(conn, first=start, last=end, owner=owner, currency=currency)
    by_year: dict[int, Decimal] = {}
    for month in paid.months:
        year = int(month.name[:4])
        by_year[year] = by_year.get(year, Decimal(0)) + month.paid.paid
    years = []
    for year in range(start.year, end.year + 1):
        last = min(date(year, 12, 31), end)
        cost = _cost(found.between(date(year, 1, 1), last))
        years.append(Year(year, cost, by_year.get(year, Decimal(0)), last))
    return Costs(
        currency,
        end,
        tuple(years),
        _cost(found.between(start, end)),
        sum((year.paid for year in years), Decimal(0)),
        _accounts(found.between(start, end), brokers),
        _missing(why, paid.missing),
    )


def _cost(rows: Iterable[Converted]) -> Cost:
    """What `rows` cost, to the cent."""
    sums = dict.fromkeys((COMMISSIONS, TAX), Decimal(0))
    for each in rows:
        sums[each.row.leaf] -= each.amount
    return Cost(sums[COMMISSIONS].quantize(CENT), sums[TAX].quantize(CENT))


def _accounts(rows: Iterable[Converted], brokers: set[tuple[str, str]]) -> tuple[Account, ...]:
    """The rows grouped by account, what cost the most first."""
    grouped: dict[tuple[str, str], list[Converted]] = {}
    for each in rows:
        grouped.setdefault((each.row.owner, each.row.account), []).append(each)
    owners: dict[str, set[str]] = {}  # an account's name -> the people who have one
    for whose, account in grouped:
        owners.setdefault(account, set()).add(whose)
    found = []
    for (whose, account), members in grouped.items():
        cost = _cost(members)
        if cost == NOTHING:
            continue
        name = f"{account} ({whose})" if len(owners[account]) > 1 else account
        found.append(Account(whose, account, name, cost, (whose, account) in brokers))
    found.sort(key=lambda each: (-each.cost.total, each.name))
    return tuple(found)


def _missing(why: dict[str, str | None], more: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({reason for reason in why.values() if reason is not None} | set(more)))
