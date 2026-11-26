"""What investments paid: dividends and interest, and the tax withheld from them, month by
month and holding by holding. The income view of the investments page.

The rows are those of the categories `Dividends`, `Interest` and `Withholding tax` that the
cash-flow page counts, each converted at the rate of its own day into one currency, as the
analysis views do. Interest below nothing is interest charged (on a debit balance, a margin
loan): a cost, not a payout, and left out rather than taken off the interest received.

The range runs, unless asked otherwise, over the twelve months up to the last day committed:
the month of that day and the eleven before it. Those twelve months are added up whatever the
range, so the page can say what a year pays now. Rows in a currency no rate is stored for are
left out, and said to be, rather than leaving the whole page empty.

A row carries no link to an instrument: what paid it is named, if at all, only in its
counterparty. IBKR, Tastytrade and Schwab write the symbol there, Finpension the fund's ISIN,
and the interest split off a bill that matured the bill's CUSIP. A holding is that name, in
capitals with its spaces tidied; it takes the name of the instrument whose symbol it is, since
`instruments` keeps a ticker, an ISIN or a CUSIP as the statements have it. A name may carry the
ISIN in brackets after the symbol, as IBKR's descriptions do (`ACME(US0000000001)`): the
brackets are dropped, and the holding is the symbol when an instrument has it, else the ISIN
when one has that, else the symbol, so the tax withheld meets its dividend. A row that names
nothing (a dividend at Morgan Stanley, interest on cash) is put under the account it was paid
into, and the person whose account it is: for everyone, two people's accounts of the same name
are two lines, named after each person, while a holding both have is one. So the grouping is
only as good as the statements' counterparties: a ticker the broker renamed shows under each of
its names, and tax withheld under another name than its dividend is a holding of its own.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from scrooge import analysis, categories, query
from scrooge.analysis import CENT, Converted
from scrooge.cashflow import Counted, Grouping
from scrooge.categories import DIVIDENDS, INTEREST, WITHHOLDING_TAX
from scrooge.networth import DEFAULT_CURRENCY, MissingRateError, convert, rates_on

CATEGORIES = (DIVIDENDS, INTEREST, WITHHOLDING_TAX)


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
    owner: str | None  # whose the account is, when two people have one of its name; else None
    paid: Paid
    count: int  # rows, tax withheld included
    last: date  # the last day it paid; the last day tax was withheld when it only had that
    type: str | None = None  # of the instrument: etf, stock, fund, bill or bond; None if none
    maturity: date | None = None  # bills and bonds


@dataclass(frozen=True, slots=True)
class Payouts:
    currency: str
    first: date | None  # the range shown; None: nothing is committed
    last: date | None
    totals: Paid
    year_before: Paid | None  # the same months a year earlier; None when not all known
    trailing: Paid  # the twelve months up to `through`, whatever the range
    months: tuple[Month, ...]  # every month of the range, oldest first
    holdings: tuple[Holding, ...]  # what was paid the most first
    through: date | None  # the last day committed for everyone shown
    missing: tuple[str, ...] = ()  # why rows were left out: a currency without a rate


NOTHING = Paid(Decimal(0), Decimal(0), Decimal(0))

# An ISIN in brackets at the end of a name: `ACME(US0000000001)`.
_LISTED = re.compile(r"\s*\(([A-Z]{2}[A-Z0-9]{9}[0-9])\)$")


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
        if each.row.leaf != WITHHOLDING_TAX:
            self.paid_last = each.day

    def paid(self) -> Paid:
        return Paid(*(self.amounts[name].quantize(CENT) for name in CATEGORIES))


def is_payout(row: Counted) -> bool:
    """Whether `row` is a payout or tax withheld from one: interest charged (on a debit
    balance, a margin loan) is what the account cost, and is not (`categories.charged`; the
    cash flow already counts it under a leaf of its own)."""
    return row.leaf in CATEGORIES and not categories.charged(row.leaf, row.amount_nano)


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
    without `first` it starts eleven months before the month of its end. The rows of a
    currency that cannot be converted into `currency` are left out, and `missing` says why."""
    rates = rates_on(conn, ())
    why: dict[str, str | None] = {}  # currency -> why its rows cannot be converted, if not

    def wanted(row: Counted) -> bool:
        if not is_payout(row):
            return False
        if row.currency not in why:
            try:
                convert((), row.currency, currency, rates)
                why[row.currency] = None
            except MissingRateError as exc:
                why[row.currency] = str(exc)
        return why[row.currency] is None

    found = analysis.rows(conn, owner, currency, wanted)
    missing = tuple(sorted({reason for reason in why.values() if reason is not None}))
    if found.through is None:
        return Payouts(currency, None, None, NOTHING, None, NOTHING, (), (), None, missing)
    end = last or found.through
    start = first or analysis.add_months(end, -11)
    trailing = _sum(found.between(analysis.add_months(found.through, -11), found.through)).paid()
    if start > end:
        return Payouts(
            currency, start, end, NOTHING, None, trailing, (), (), found.through, missing
        )
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
        trailing,
        months,
        _holdings(conn, found.between(start, end)),
        found.through,
        missing,
    )


def _sum(rows: Iterable[Converted]) -> _Sums:
    sums = _Sums()
    for each in rows:
        sums.add(each)
    return sums


@dataclass(frozen=True, slots=True)
class _Instrument:
    """What `instruments` knows of one symbol, for naming what paid."""

    symbol: str
    name: str
    type: str
    maturity: date | None


def _instruments(conn: sqlite3.Connection) -> dict[str, _Instrument]:
    """Symbol in capitals -> instrument metadata, preferring entries with a name or maturity."""
    found: dict[str, _Instrument] = {}
    for symbol, name, kind, maturity in conn.execute(
        "SELECT symbol, name, type, maturity FROM instruments ORDER BY id"
    ):
        key = str(symbol).upper()
        day = None if maturity is None else date.fromisoformat(str(maturity))
        existing = found.get(key)
        if existing is None:
            found[key] = _Instrument(str(symbol), str(name), str(kind), day)
        else:
            found[key] = _Instrument(
                str(symbol) if not existing.name and name else existing.symbol,
                existing.name or str(name),
                existing.type,
                existing.maturity or day,
            )
    return found


def named_instruments(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """Symbol in capitals -> `(symbol, name)` as stored, preferring an entry with a name."""
    return {key: (inst.symbol, inst.name) for key, inst in _instruments(conn).items()}


def _holdings(conn: sqlite3.Connection, rows: Iterable[Converted]) -> tuple[Holding, ...]:
    """The rows grouped by what paid them, what was paid the most first."""
    instruments = _instruments(conn)
    groups: dict[tuple[str, str | None, str | None], _Sums] = {}
    for each in rows:
        key = holding_of(each.row.counterparty, instruments)
        where = (key, None, None) if key else (each.row.account, each.row.account, each.row.owner)
        groups.setdefault(where, _Sums()).add(each)
    owners: dict[str, set[str | None]] = {}  # an account's name -> the people who have one
    for _, account, owner in groups:
        if account is not None:
            owners.setdefault(account, set()).add(owner)
    holdings = []
    for (key, account, owner), sums in groups.items():
        inst = instruments.get(key) if account is None else None
        symbol, name, kind, maturity = (
            (None, "", None, None)
            if inst is None
            else (inst.symbol, inst.name, inst.type, inst.maturity)
        )
        shared = account is not None and len(owners[account]) > 1
        if shared:
            name = f"{account} ({owner})"
        last = sums.paid_last or sums.any_last
        whose = owner if shared else None
        holdings.append(
            Holding(
                key,
                name or symbol or key,
                symbol,
                account,
                whose,
                sums.paid(),
                sums.count,
                last,
                kind,
                maturity,
            )
        )
    holdings.sort(key=lambda holding: (-holding.paid.paid, holding.name))
    return tuple(holdings)


def holding_of(counterparty: str, named: Mapping[str, object]) -> str:
    """What a row's counterparty names, as its holding is keyed: in capitals with its spaces
    tidied, and without an ISIN in brackets after the symbol, unless only the ISIN is an
    instrument's symbol."""
    key = " ".join(counterparty.split()).upper()
    listed = _LISTED.search(key)
    if listed is None:
        return key
    symbol, isin = key[: listed.start()], listed[1]
    return isin if symbol not in named and (isin in named or not symbol) else symbol


def _known(conn: sqlite3.Connection, owner: str | None) -> date | None:
    """The first day of the month from which every person shown has statements committed."""
    firsts = [
        person.first.replace(day=1)
        for person in query.people(conn)
        if person.first is not None and owner in (None, person.name)
    ]
    return max(firsts, default=None)
