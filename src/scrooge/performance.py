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
- a row of kind `income` or `expense` outside the investing categories (`Investments`,
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

**The time-weighted return** links the return of every day of the range. Cash that comes in
on a day comes at its start and earns that day. What goes out goes at its end and earned that
day first, and so does what comes in valued at the day's close (shares moved in in kind, the
shares an account brings when it comes into view): it was worth that at the end of the day,
and made nothing on it for the account. The day returns its worth at the end, less what came
at its end, over its worth the day before with what came at its start. Flows within a day are
netted first, so money moved between two accounts counted together on one day moves nothing.
It measures the investments, not when money went in. A day before any money is put in, or that
starts with less than 100 francs invested (`FLOOR`, in the currency asked for at the day's
rate, so that every currency leaves out the same days), or that ends with less than nothing,
is no day of return (`time_weighted`); the day the return is measured from is said when it is
not the range's first.

**The money-weighted return** is the rate at which the value the range starts with and every
flow, dated as above, grow into the value it ends with (an internal rate of return of dated
flows, XIRR): it measures what the money put in earned, when it was put in. Over a range of a
year or more it is a rate a year; over a shorter one it is the rate over the range, as a rate
a year would make a few good weeks look like a fortune. **The gain** is the value at the end
less that at the start and less the money put in. Nothing here is stored and nothing here
writes.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from scrooge import benchmark, networth
from scrooge.cashflow import _INVESTING
from scrooge.networth import CARD, PENSION, PILLAR2, MissingRateError, Series, convert, rates_on
from scrooge.storage.money import from_nano
from scrooge.storage.store import committed_or_typed, marks

RANGES = ("1m", "3m", "ytd", "1y", "3y", "all")  # as the overview names them
DEFAULT_RANGE = "ytd"
# How many days before the last day a range starts from, as the overview counts them.
_BACK = {"1m": 30, "3m": 91, "1y": 365, "3y": 1095}
WEEKLY_AFTER = 400  # days: a longer range is drawn one point a week
_BROKERS = ("broker_cash", "broker_securities")  # the kinds of account a broker feeds
_LEFT_OUT = frozenset({PENSION, PILLAR2, CARD})  # an account with these is no investment
_CENT = Decimal("0.01")
# Below this many francs invested a day has nothing to return on: a few francs left in an
# emptied account that a late dividend doubles say nothing of the investments. In francs, the
# currency every rate is stored against, converted at each day's rate into the one asked for:
# the same days count whatever the currency. Without a rate for that currency, 100 of it.
FLOOR = Decimal(100)
_ONE_DAY = timedelta(days=1)
_YEAR = 365  # days: a range at least this long has its money-weighted return a year
# The rates a money-weighted return is looked for between: from all but everything lost up to
# a billion times what was put in; beyond these there is none to give.
_LOWEST, _HIGHEST = -0.999999, 1e9


@dataclass(frozen=True, slots=True)
class Measured:
    """What one account, or several together, did over a range, in one currency."""

    start: Decimal  # worth at the end of the day before the range
    end: Decimal  # worth at the end of its last day
    net_flows: Decimal  # money put in, less what was taken out
    twr: Decimal | None  # time-weighted, in percent over the range; None: nothing was invested
    mwr: Decimal | None  # money-weighted, in percent; None: there is no such rate
    a_year: bool = False  # `mwr` is a rate a year (a range of a year or more), else over it
    # The first day the time-weighted return counts, when it is after the range's first day;
    # None: it counts from the first day, or not at all.
    since: date | None = None

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
    benchmarks: tuple[Decimal, ...] = ()  # the line of each of `Performance.benchmarks`


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
    benchmarks: tuple[benchmark.Benchmark, ...] = ()  # what holding each returned instead
    unbenchmarked: tuple[str, ...] = ()  # why a benchmark asked for is left out


# ---------------------------------------------------------------------------- rates of return


def time_weighted(
    values: Sequence[Decimal],
    flows: Sequence[Decimal],
    *,
    late: Sequence[Decimal] = (),
    floor: Sequence[Decimal] = (),
) -> tuple[Decimal, int] | None:
    """The return over the days of `values`, linked day by day, as a fraction, and the first
    day it counts.

    `values[0]` is the worth before the first day; `values[n]` is the worth at the end of day
    n and `flows[n]` the money that day put in, less what it took out (`flows[0]` is not read).
    `late[n]` is the part of `flows[n]` valued at the day's close (none when it is empty): it
    comes at the end of the day. Of the rest, what comes in comes at the start of the day and
    what goes out at its end. `floor[n]` is the least day n must start with (`FLOOR` each day
    when it is empty).

    Some days are left out, as no day of return. Those before any money is put in (the start
    value and the flows so far add up to nothing): what an account makes before the deposit
    that pays for it (a bill bought first, an export that reaches back only so far) is made on
    money the history does not show. A day that starts with less than its floor: there is next
    to nothing to return on. A day that ends with less than nothing: losing more than was
    invested says that money put in is missing, not what was returned. None when every day is
    left out.
    """
    put_in, started = values[0], values[0] > 0
    grown, since = Decimal(1), 0
    for day in range(1, len(values)):
        flow = flows[day]
        at_close = late[day] if late else Decimal(0)
        put_in += flow
        started = started or put_in > 0
        cash = flow - at_close
        invested = values[day - 1] + max(cash, Decimal(0))
        if not started or invested < (floor[day] if floor else FLOOR) or values[day] < 0:
            continue
        grown *= (values[day] - min(cash, Decimal(0)) - at_close) / invested
        since = since or day
    return (grown - 1, since) if since else None


def money_weighted(dated: Sequence[tuple[int, Decimal]], period: int = _YEAR) -> Decimal | None:
    """The rate over `period` days at which `dated`, amounts on days counted from the first,
    add up to nothing: what goes in is negative, what comes out positive. None when there is
    no such rate between all lost and a billion times over, or nothing ever moves.

    Found by halving the interval it lies in, which always converges once it is bracketed.
    """
    flows = [(days / period, float(amount)) for days, amount in dated if amount]
    if not flows or max(periods for periods, _ in flows) <= 0:
        return None

    def worth(rate: float) -> float:
        grown = math.log1p(rate)
        return sum(amount * math.exp(-periods * grown) for periods, amount in flows)

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


def measure(
    values: Sequence[Decimal],
    flows: Sequence[Decimal],
    first: date,
    *,
    late: Sequence[Decimal] = (),
    floor: Sequence[Decimal] = (),
) -> Measured:
    """Start, end, money put in, and both returns of `values` and `flows`, read as for
    `time_weighted`; `first` is the day of `values[1]`. The money-weighted return counts the
    start value put in before the first day, cash in at the start of its day (the end of the
    day before), money out and what comes in at the close at the end of its day, and the end
    value taken out at the end of the last; a rate a year over a year or more, over the range
    otherwise."""
    start, end = values[0], values[-1]
    days = len(values) - 1
    net = sum(flows[1:], Decimal(0))
    dated = [(0, -start)]
    for day in range(1, days + 1):
        at_close = late[day] if late else Decimal(0)
        cash = flows[day] - at_close
        dated += [(day - 1, -max(cash, Decimal(0))), (day, -min(cash, Decimal(0)) - at_close)]
    dated.append((days, end))
    linked = time_weighted(values, flows, late=late, floor=floor)
    a_year = days >= _YEAR
    mwr = money_weighted(dated, _YEAR if a_year else days) if days else None
    return Measured(
        start,
        end,
        net,
        None if linked is None else _percent(linked[0]),
        None if mwr is None else _percent(mwr),
        a_year,
        first + timedelta(days=linked[1] - 1) if linked and linked[1] > 1 else None,
    )


def _percent(fraction: Decimal) -> Decimal:
    """`fraction` in percent to the cent; never -0.00."""
    return (fraction * 100).quantize(_CENT) + 0


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


@dataclass(frozen=True, slots=True)
class _Flow:
    day: date
    currency: str
    amount: Decimal  # put in; negative when taken out
    at_close: bool = False  # valued at the close of its day: it comes at the day's end


@dataclass(frozen=True, slots=True)
class _Days:
    """What a holder is worth at the end of each day, and what came in and went out, in the
    currency asked for: one per day, the first that before the range."""

    values: list[Decimal]
    flows: list[Decimal]
    late: list[Decimal]  # the part of `flows` valued at the day's close

    def after(self, start: int) -> _Days:
        """From the `start`th day; from a day before the first, that held nothing, when it is
        negative."""
        if start < 0:
            return _Days(*([Decimal(0), *part] for part in (self.values, self.flows, self.late)))
        return _Days(self.values[start:], self.flows[start:], self.late[start:])


def _flows(conn: sqlite3.Connection, ids: set[int], days: Sequence[date]) -> dict[int, list[_Flow]]:
    """account -> each row of the accounts `ids` within `days` that is money put in or taken
    out, and each move of shares in kind, valued (see the module).

    A committed row always has a category (a row waiting for one holds its month back), so an
    income or an expense is investing or not by its category; one typed without a category is
    money from outside."""
    within = (json.dumps(sorted(ids)), days[0].isoformat(), days[-1].isoformat())
    accounts = "t.account_id IN (SELECT value FROM json_each(?)) AND t.booking_date BETWEEN ? AND ?"
    found: dict[int, list[_Flow]] = defaultdict(list)
    rows = conn.execute(
        "SELECT t.account_id, t.booking_date, t.currency, t.amount_nano FROM transactions t "  # noqa: S608  # only "?" marks and the fixed conditions above and of `store.committed_or_typed` are interpolated
        "LEFT JOIN categories c ON c.id = t.category_id "
        "LEFT JOIN categories p ON p.id = c.parent_id "
        f"WHERE {accounts} AND {committed_or_typed('t.statement_file_id')} "
        "AND t.amount_nano <> 0 AND (t.kind = 'transfer' OR (t.kind IN ('income', 'expense') "
        f"AND coalesce(p.key, c.key, '') NOT IN ({marks(len(_INVESTING))})))",
        (*within, *sorted(_INVESTING)),
    )
    for account, day, currency, nano in rows:
        found[account].append(_Flow(date.fromisoformat(day), str(currency), from_nano(nano)))
    moved = conn.execute(
        "SELECT t.account_id, t.booking_date, tr.instrument_id, i.currency, tr.quantity, "  # noqa: S608  # only "?" marks and the fixed conditions above and of `store.committed_or_typed` are interpolated
        "tr.cost_nano FROM trades tr JOIN transactions t ON t.id = tr.transaction_id "
        "JOIN instruments i ON i.id = tr.instrument_id "
        f"WHERE {accounts} AND t.kind = 'transfer' AND {committed_or_typed('t.statement_file_id')}",
        within,
    ).fetchall()
    # Only on the days shares moved in kind: the closes are read again, as net worth reads
    # them, for those days alone.
    when = sorted({date.fromisoformat(row[1]) for row in moved})
    closes = networth.closes_on(conn, when) if when else {}
    for account, day, instrument, currency, quantity, cost in moved:
        on = closes.get(instrument)
        at = when.index(date.fromisoformat(day))
        worth = Decimal(quantity) * on[at] if on else from_nano(cost)
        found[account].append(_Flow(date.fromisoformat(day), str(currency), worth, True))
    return found


# -------------------------------------------------------------------------------- results


def run(
    conn: sqlite3.Connection,
    *,
    owner: str | None = None,
    currency: str = networth.DEFAULT_CURRENCY,
    range_: str = DEFAULT_RANGE,
    benchmarks: Sequence[str] = (),
) -> Performance:
    """What the investment accounts of `owner`, or of everyone, returned over `range_` (one of
    RANGES, ending with the last day net worth is known for), in `currency`; and beside them
    the `benchmarks`, by symbol (`benchmark.follow`)."""
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
    measured: list[tuple[tuple[str, str], list[int], _Days]] = []
    missing: set[str] = set()
    left_out: list[str] = []
    for key, members in sorted(holders.items()):
        try:
            daily = _daily(members, by_account, flows, days, currency=currency, rates=rates)
        except MissingRateError as exc:
            missing.add(str(exc))
            left_out.append(f"{key[1]} ({key[0]})")
            continue
        measured.append((key, members, daily.after(start)))
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
    every = _Days(*(_added(getattr(m[2], part) for m in measured) for part in _Days.__slots__))
    total, put = every.values, every.flows
    floor = _floor(currency, rates, days).after(start).values
    totals = _rounded(_measure(every, base + _ONE_DAY, floor))
    followed, unbenchmarked = _followed(
        conn, benchmarks, [base, *days[start + 1 :]], every, totals=totals, currency=currency
    )
    invested, points = total[0], []
    for at, day in enumerate([base, *days[start + 1 :]]):
        if at:
            invested += put[at]
        lines = tuple(each.values[at] for each in followed)
        points.append(Point(day, total[at].quantize(_CENT), invested.quantize(_CENT), lines))
    rows = [
        Holder(
            owner_,
            name,
            tuple(sorted(accounts[i].name for i in members)),
            _rounded(_measure(daily, base + _ONE_DAY, floor)),
        )
        for (owner_, name), members, daily in measured
    ]
    rows.sort(key=lambda row: (row.owner, -row.measured.end, row.name))
    return replace(
        found,
        totals=totals,
        points=sampled(points),
        holders=tuple(rows),
        benchmarks=tuple(each.benchmark for each in followed),
        unbenchmarked=tuple(unbenchmarked),
    )


def _followed(  # noqa: PLR0913  # the benchmarks, and the days and money of the range
    conn: sqlite3.Connection,
    symbols: Sequence[str],
    days: Sequence[date],
    every: _Days,
    *,
    totals: Measured,
    currency: str,
) -> tuple[list[benchmark.Followed], list[str]]:
    """Each benchmark over the range's `days`, its return counted from where the time-weighted
    return is; and why each one that cannot be is left out."""
    start = 0 if totals.since is None else (totals.since - days[0]).days - 1
    followed: list[benchmark.Followed] = []
    unbenchmarked: list[str] = []
    for symbol in symbols:
        try:
            followed.append(
                benchmark.follow(
                    conn,
                    symbol,
                    days,
                    [every.values[0], *every.flows[1:]],
                    currency=currency,
                    late=every.late,
                    start=start,
                )
            )
        except (benchmark.BenchmarkError, MissingRateError) as exc:
            unbenchmarked.append(f"{symbol} is left out: {exc}")
    return followed, unbenchmarked


def _rounded(measured: Measured) -> Measured:
    """`measured` with its amounts to the cent."""
    return replace(
        measured,
        start=measured.start.quantize(_CENT),
        end=measured.end.quantize(_CENT),
        net_flows=measured.net_flows.quantize(_CENT),
    )


def _measure(daily: _Days, first: date, floor: Sequence[Decimal]) -> Measured:
    return measure(daily.values, daily.flows, first, late=daily.late, floor=floor)


def _added(parts: Iterable[Sequence[Decimal]]) -> list[Decimal]:
    return [sum(day, Decimal(0)) for day in zip(*parts, strict=True)]


def _floor(currency: str, rates: Mapping[str, Sequence[Decimal]], days: Sequence[date]) -> _Days:
    """`FLOOR` francs in `currency` on each day, as the values of `_Days` (to cut it to the
    range as they are); 100 of `currency` a day without a rate for it."""
    francs = rates.get(currency) or [Decimal(1)] * len(days)  # francs per unit
    floor = [FLOOR / rate for rate in francs]
    return _Days(floor, list(floor), list(floor))


def _daily(  # noqa: PLR0913  # the accounts, what they are made of, and how to convert them
    members: Sequence[int],
    series: Mapping[int, Sequence[Series]],
    flows: Mapping[int, Sequence[_Flow]],
    days: Sequence[date],
    *,
    currency: str,
    rates: Mapping[str, Sequence[Decimal]],
) -> _Days:
    """What the accounts are worth at the end of each day, and the money put in that day, in
    `currency`. Cash an account brings when it comes into view is there before the day's rows;
    the shares it brings are valued at the day's close."""
    values = [Decimal(0)] * len(days)
    put_in = [Decimal(0)] * len(days)
    late = [Decimal(0)] * len(days)
    for account in members:
        for each in series.get(account, ()):
            converted = convert(each.values, each.currency, currency, rates)
            values = [a + b for a, b in zip(values, converted, strict=True)]
            if each.arrival is not None and each.arrival[0] >= days[0]:
                at = (each.arrival[0] - days[0]).days
                amount = _on(each.arrival[1], each.currency, currency, rates, at)
                put_in[at] += amount
                if each.instrument_id is not None:
                    late[at] += amount
        for flow in flows.get(account, ()):
            at = (flow.day - days[0]).days
            amount = _on(flow.amount, flow.currency, currency, rates, at)
            put_in[at] += amount
            if flow.at_close:
                late[at] += amount
    return _Days(values, put_in, late)


def _on(
    amount: Decimal, currency: str, into: str, rates: Mapping[str, Sequence[Decimal]], at: int
) -> Decimal:
    """`amount` of `currency` in `into` at the rate of the `at`th day."""
    that_day = {code: values[at : at + 1] for code, values in rates.items()}
    return convert([amount], currency, into, that_day)[0]
