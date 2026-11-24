"""What the investments returned over a range, and how much of their change was money put in:
the performance view of the investments page.

**The portfolio** is the investment accounts: every broker's account (IBKR, Tastytrade, the
stock-plan accounts at Morgan Stanley and Schwab) and every other account that holds
securities, each with its cash. The pension funds (Pillar 3a at Finpension, a second-pillar
fund) are left out, and so is the card. The accounts of one person at one broker that differ
only by their currency (`IBKR CHF`, `IBKR USD`) are one account here, `IBKR`: an exchange
moves money between them and is no money put in. What an account is worth each day is what
net worth counts for it (`networth.value`): its cash and what it holds at the day's close.

**Money put in or taken out** (the external flows) is what moved into or out of those accounts
from outside them, on the day it was booked, as net worth books it:

- a row of kind `transfer`: a deposit, a withdrawal, a move between a person's own accounts,
  the proceeds a stock plan pays out;
- a row of kind `income` or `expense` outside the investing categories (`Investimenti`,
  `Trading`): shares a plan vests as salary, a referral bonus, a bill paid from the account.
  A bill's principal repaid is investing, and stays inside;
- shares moved in or out in kind (from another broker, a plan's shares withdrawn to IBKR):
  what they are worth at the close they are valued at that day, or what the statement says
  they were worth when no close is known;
- what an account brings when it comes into view: the cash its first statement opens with,
  and the shares held when its history starts (`networth.Series.arrival`). It was not earned
  over the range.

Everything else is part of the return: dividends, interest, fees and commissions, tax
withheld, the results of trading, securities bought and sold and exchanges inside an account,
and every change of a price.

Everything is converted into the currency asked for at the rate of each day: what the
accounts are worth, and each flow at the rate of its own day. So a move of the exchange rates
is part of the return in that currency: shares in dollars that stand still have returned
something in francs when the dollar rose. An account with anything in a currency no rate is
stored for is left out, and said to be, rather than leaving the page empty.

**The time-weighted return** links the return of every day of the range: what the accounts
are worth at the end of the day over what they were worth the day before with the day's flows,
which come at its start. It measures the investments, not when money went in. A day that
starts with nothing (or less) invested is no day of return.

**The money-weighted return** is the rate, a year, at which the value the range starts with
and every flow grow into the value it ends with (an internal rate of return of dated flows,
XIRR): it measures what the money put in earned, when it was put in. **The gain** is the
value at the end less that at the start and less the money put in. Nothing here is stored and
nothing here writes.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from scrooge import networth
from scrooge.cashflow import _INVESTING
from scrooge.networth import CARD, PENSION, PILLAR2, MissingRateError, Series, convert, rates_on
from scrooge.storage.money import from_nano
from scrooge.storage.store import committed_or_typed

RANGES = ("1m", "3m", "ytd", "1y", "3y", "all")  # as the overview names them
DEFAULT_RANGE = "ytd"
# How many days before the last day a range starts from, as the overview counts them.
_BACK = {"1m": 30, "3m": 91, "1y": 365, "3y": 1095}
WEEKLY_AFTER = 400  # days: a longer range is drawn one point a week
_BROKERS = ("broker_cash", "broker_securities")  # the kinds of account a broker feeds
_LEFT_OUT = frozenset({PENSION, PILLAR2, CARD})  # an account with these is no investment
_CENT = Decimal("0.01")
_ONE_DAY = timedelta(days=1)
_YEAR = 365.0
# The rates a money-weighted return is looked for between, a year: from all but everything
# lost up to a billion times what was put in. A range of days can make a small gain a huge
# rate a year; beyond these there is none to give.
_LOWEST, _HIGHEST = -0.999999, 1e9


@dataclass(frozen=True, slots=True)
class Measured:
    """What one account, or several together, did over a range, in one currency."""

    start: Decimal  # worth at the end of the day before the range
    end: Decimal  # worth at the end of its last day
    net_flows: Decimal  # money put in, less what was taken out
    twr: Decimal | None  # time-weighted, in percent over the range; None: nothing was invested
    mwr: Decimal | None  # money-weighted, in percent a year; None: there is no such rate

    @property
    def gain(self) -> Decimal:
        """What the investments made: the change that is not money put in or taken out."""
        return self.end - self.start - self.net_flows


@dataclass(frozen=True, slots=True)
class Point:
    """One day of the chart: what the accounts were worth, and what had been put in."""

    day: date
    value: Decimal
    invested: Decimal  # the value at the start of the range and the net flows since


@dataclass(frozen=True, slots=True)
class Holder:
    """An investment account as a person thinks of it: one broker's accounts in every
    currency together."""

    owner: str
    name: str
    accounts: tuple[str, ...]  # the accounts it is made of, as stored
    measured: Measured


@dataclass(frozen=True, slots=True)
class Performance:
    currency: str
    range: str  # one of RANGES
    first: date | None  # the first day of the range; None: no investment account is known
    last: date | None
    totals: Measured | None
    points: tuple[Point, ...]  # from the day before the range, oldest first; weekly if long
    holders: tuple[Holder, ...]  # by owner, then the largest at the end first
    missing: tuple[str, ...] = ()  # why accounts were left out: a currency without a rate
    left_out: tuple[str, ...] = ()  # those accounts, named with their owner


# ---------------------------------------------------------------------------- rates of return


def time_weighted(values: Sequence[Decimal], flows: Sequence[Decimal]) -> Decimal | None:
    """The return over the days of `values`, linked day by day, as a fraction.

    `values[0]` is the worth before the first day; `values[n]` and `flows[n]` are the worth at
    the end of day n and the money put in at its start (`flows[0]` is not read). A day that
    starts with nothing or less invested is left out. None when every day is.
    """
    grown, counted = Decimal(1), False
    for day in range(1, len(values)):
        invested = values[day - 1] + flows[day]
        if invested <= 0:
            continue
        grown *= values[day] / invested
        counted = True
    return grown - 1 if counted else None


def money_weighted(dated: Sequence[tuple[int, Decimal]]) -> Decimal | None:
    """The rate a year at which `dated`, amounts on days counted from the first, add up to
    nothing: what goes in is negative, what comes out positive. None when there is no such
    rate between all lost and a billion times over, or nothing ever moves.

    Found by halving the interval it lies in, which always converges once it is bracketed.
    """
    flows = [(days / _YEAR, float(amount)) for days, amount in dated if amount]
    if not flows or max(years for years, _ in flows) <= 0:
        return None

    def worth(rate: float) -> float:
        grown = math.log1p(rate)
        return sum(amount * math.exp(-years * grown) for years, amount in flows)

    low, high = _LOWEST, 1.0
    while worth(low) * worth(high) > 0:
        if high >= _HIGHEST:
            return None
        high = min(high * 10, _HIGHEST)
    at_low = worth(low)
    for _ in range(200):
        middle = (low + high) / 2
        at_middle = worth(middle)
        if at_middle == 0 or high - low < 1e-12:
            break
        if (at_middle > 0) == (at_low > 0):
            low, at_low = middle, at_middle
        else:
            high = middle
    return Decimal(repr((low + high) / 2))


def measure(values: Sequence[Decimal], flows: Sequence[Decimal]) -> Measured:
    """Start, end, money put in, and both returns of `values` and `flows`, read as for
    `time_weighted`. The money-weighted return counts the start value put in before the first
    day, each flow at the start of its day (the end of the day before) and the end value taken
    out at the end of the last."""
    start, end = values[0], values[-1]
    net = sum(flows[1:], Decimal(0))
    dated = [(0, -start)]
    dated += [(day - 1, -flow) for day, flow in enumerate(flows) if day and flow]
    dated.append((len(values) - 1, end))
    twr = time_weighted(values, flows)
    mwr = money_weighted(dated) if len(values) > 1 else None
    return Measured(
        start,
        end,
        net,
        None if twr is None else (twr * 100).quantize(_CENT),
        None if mwr is None else (mwr * 100).quantize(_CENT),
    )


def base_day(range_: str, first: date, last: date) -> date:
    """The day whose end a range starts from: so many days before `last` as the overview
    counts them, New Year's Eve for the year to date, and never before the day before `first`,
    from which all of the history starts with nothing."""
    before = first - _ONE_DAY
    if range_ == "all":
        return before
    if range_ == "ytd":
        return max(date(last.year - 1, 12, 31), before)
    return max(last - timedelta(days=_BACK[range_]), before)


def sampled(points: Sequence[Point]) -> tuple[Point, ...]:
    """`points`, or for a long range its first and one a week ending with the last."""
    if len(points) <= WEEKLY_AFTER:
        return tuple(points)
    weekly = points[len(points) - 1 :: -7][::-1]
    return (points[0], *weekly) if weekly[0] is not points[0] else tuple(weekly)


# ------------------------------------------------------------------------------- accounts


@dataclass(frozen=True, slots=True)
class _Account:
    id: int
    owner: str
    name: str
    kind: str
    currency: str | None
    source: str | None


def _holder_name(account: _Account) -> str:
    """The name of the account without the currency it ends with: `IBKR` for `IBKR USD`."""
    suffix = f" {account.currency}"
    if account.source is not None and account.currency and account.name.endswith(suffix):
        return account.name.removesuffix(suffix)
    return account.name


def _invested(
    accounts: Mapping[int, _Account], series: Sequence[Series]
) -> dict[tuple[str, str], list[int]]:
    """(owner, name) -> the accounts of the portfolio that make one holder, by id."""
    held: dict[int, set[str]] = defaultdict(set)
    securities = set()
    for each in series:
        held[each.account_id].add(each.asset)
        if each.instrument_id is not None:
            securities.add(each.account_id)
    holders: dict[tuple[str, str], list[int]] = defaultdict(list)
    for account_id, assets in held.items():
        account = accounts[account_id]
        if assets & _LEFT_OUT:
            continue
        if account.kind in _BROKERS or account_id in securities:
            holders[account.owner, _holder_name(account)].append(account_id)
    return holders


def _flows(
    conn: sqlite3.Connection, ids: set[int], days: Sequence[date]
) -> dict[int, list[tuple[date, str, Decimal]]]:
    """account -> (day, currency, amount) of each row that is money put in or taken out, and
    of each move of shares in kind, valued (see the module)."""
    first, last = days[0], days[-1]
    found: dict[int, list[tuple[date, str, Decimal]]] = defaultdict(list)
    investing = sorted(_INVESTING)
    rows = conn.execute(
        "SELECT t.account_id, t.booking_date, t.currency, t.amount_nano FROM transactions t "  # noqa: S608  # only "?" marks and the fixed condition of `store.committed_or_typed` are interpolated
        "LEFT JOIN categories c ON c.id = t.category_id "
        "LEFT JOIN categories p ON p.id = c.parent_id "
        f"WHERE {committed_or_typed('t.statement_file_id')} AND t.amount_nano <> 0 "
        "AND (t.kind = 'transfer' OR (t.kind IN ('income', 'expense') "
        "AND coalesce(p.name, c.name, '') NOT IN (?, ?)))",
        investing,
    )
    for account, day_text, currency, nano in rows:
        day = date.fromisoformat(day_text)
        if account in ids and first <= day <= last:
            found[account].append((day, str(currency), from_nano(nano)))
    moved = conn.execute(
        "SELECT t.account_id, t.booking_date, tr.instrument_id, i.currency, tr.quantity, "  # noqa: S608  # only the fixed condition of `store.committed_or_typed` is interpolated
        "tr.cost_nano FROM trades tr JOIN transactions t ON t.id = tr.transaction_id "
        "JOIN instruments i ON i.id = tr.instrument_id "
        f"WHERE t.kind = 'transfer' AND {committed_or_typed('t.statement_file_id')}"
    ).fetchall()
    moved = [row for row in moved if row[0] in ids and first <= date.fromisoformat(row[1]) <= last]
    when = sorted({date.fromisoformat(row[1]) for row in moved})
    closes = networth.closes_on(conn, when) if when else {}
    for account, day_text, instrument, currency, quantity, cost in moved:
        day = date.fromisoformat(day_text)
        on = closes.get(instrument)
        worth = Decimal(quantity) * on[when.index(day)] if on else from_nano(cost)
        found[account].append((day, str(currency), worth))
    return found


# -------------------------------------------------------------------------------- results


def run(
    conn: sqlite3.Connection,
    *,
    owner: str | None = None,
    currency: str = networth.DEFAULT_CURRENCY,
    range_: str = DEFAULT_RANGE,
) -> Performance:
    """What the investment accounts of `owner`, or of everyone, returned over `range_` (one of
    RANGES, ending with the last day net worth is known for), in `currency`."""
    valued = networth.value(conn, owner)
    accounts = {
        int(r[0]): _Account(int(r[0]), str(r[1]), str(r[2]), str(r[3]), r[4], r[5])
        for r in conn.execute(
            "SELECT a.id, o.name, a.name, a.kind, a.currency, a.source FROM accounts a "
            "JOIN owners o ON o.id = a.owner_id"
        )
    }
    holders = {} if valued is None else _invested(accounts, valued.series)
    if valued is None or not holders:
        return Performance(currency, range_, None, None, None, (), ())
    days = valued.days
    base = base_day(range_, days[0], days[-1])
    # The range's values start with the day before it: for all of the history, a day before
    # the first one, that held nothing.
    start = (base - days[0]).days
    rates = rates_on(conn, days)
    flows = _flows(conn, {i for members in holders.values() for i in members}, days)
    by_account: dict[int, list[Series]] = defaultdict(list)
    for each in valued.series:
        by_account[each.account_id].append(each)
    measured: list[tuple[tuple[str, str], list[int], list[Decimal], list[Decimal]]] = []
    missing: set[str] = set()
    left_out: list[str] = []
    for key, members in sorted(holders.items()):
        try:
            values, put_in = _daily(
                members, by_account, flows, days, currency=currency, rates=rates
            )
        except MissingRateError as exc:
            missing.add(str(exc))
            left_out.append(f"{key[1]} ({key[0]})")
            continue
        if start < 0:
            values, put_in = [Decimal(0), *values], [Decimal(0), *put_in]
        measured.append((key, members, values[max(start, 0) :], put_in[max(start, 0) :]))
    found = Performance(
        currency,
        range_,
        base + _ONE_DAY,
        days[-1],
        None,
        (),
        (),
        tuple(sorted(missing)),
        tuple(left_out),
    )
    if not measured:
        return found
    total = [sum(parts, Decimal(0)) for parts in zip(*(m[2] for m in measured), strict=True)]
    put = [sum(parts, Decimal(0)) for parts in zip(*(m[3] for m in measured), strict=True)]
    invested, points = total[0], []
    for at, day in enumerate([base, *days[start + 1 :]]):
        if at:
            invested += put[at]
        points.append(Point(day, total[at].quantize(_CENT), invested.quantize(_CENT)))
    rows = [
        Holder(
            owner_,
            name,
            tuple(sorted(accounts[i].name for i in members)),
            _rounded(measure(values, put_in)),
        )
        for (owner_, name), members, values, put_in in measured
    ]
    rows.sort(key=lambda row: (row.owner, -row.measured.end, row.name))
    return replace(
        found,
        totals=_rounded(measure(total, put)),
        points=sampled(points),
        holders=tuple(rows),
    )


def _rounded(measured: Measured) -> Measured:
    """`measured` with its amounts to the cent."""
    return replace(
        measured,
        start=measured.start.quantize(_CENT),
        end=measured.end.quantize(_CENT),
        net_flows=measured.net_flows.quantize(_CENT),
    )


def _daily(  # noqa: PLR0913  # the accounts, what they are made of, and how to convert them
    members: Sequence[int],
    series: Mapping[int, Sequence[Series]],
    flows: Mapping[int, Sequence[tuple[date, str, Decimal]]],
    days: Sequence[date],
    *,
    currency: str,
    rates: Mapping[str, Sequence[Decimal]],
) -> tuple[list[Decimal], list[Decimal]]:
    """What the accounts are worth at the end of each day, and the money put in at its start,
    in `currency`."""
    values = [Decimal(0)] * len(days)
    put_in = [Decimal(0)] * len(days)
    for account in members:
        for each in series.get(account, ()):
            converted = convert(each.values, each.currency, currency, rates)
            values = [a + b for a, b in zip(values, converted, strict=True)]
            if each.arrival is not None and each.arrival[0] >= days[0]:
                at = (each.arrival[0] - days[0]).days
                put_in[at] += _on(each.arrival[1], each.currency, currency, rates, at)
        for day, native, amount in flows.get(account, ()):
            at = (day - days[0]).days
            put_in[at] += _on(amount, native, currency, rates, at)
    return values, put_in


def _on(
    amount: Decimal, currency: str, into: str, rates: Mapping[str, Sequence[Decimal]], at: int
) -> Decimal:
    """`amount` of `currency` in `into` at the rate of the `at`th day."""
    that_day = {code: values[at : at + 1] for code, values in rates.items()}
    return convert([amount], currency, into, that_day)[0]
