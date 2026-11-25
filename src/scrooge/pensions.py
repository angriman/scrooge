"""The pensions as a group of their own: what each pension account is worth, how it changed
over the last twelve months and since the year began, what was paid into it, and what it
returned where that can be known. The pension part of the investments page.

**The pensions** are the accounts net worth counts as `Pillar 2` or `Pillar 3a`, each on its
own line: one person's pension fund, or one of their 3a portfolios. Their worth each day is what
net worth counts for them (`networth.value`), converted at each day's rate into the currency
asked for. Of a 3a account that is its fund units at the day's close and its cash together:
the one figure the account is worth, not the units alone the securities list shows.

**What can be known differs between the two pillars.**

- **Pillar 3a** is a portfolio like a broker's account: its statements give every deposit and
  every trade. It is measured as `performance` measures an investment account, as a portfolio
  of its own: the money paid in (a deposit, or money moved in from another 3a provider) is
  money put in; fund units bought and sold, dividends, interest and fees are its return. So it
  has a time-weighted and a money-weighted return, read as the performance view reads them.
- **Pillar 2** is a pension fund's certificate: one figure on one day, carried until the next
  (decided 2026-10-10), or what was typed for it in between. A certificate states the balance
  only: what the employer and the employee paid in and what the fund credited as interest
  are not told apart. So its change is shown as it is, with no money paid in and no return,
  and the day of the figure it is carried at is said. Before its first figure nothing is known
  of it: the change from a day before that is not known either, rather than the whole balance.

A change is over the twelve months up to the last day net worth is known for (from the end of
the day 365 days before it), and over the year to date (from New Year's Eve), as the
performance view counts its ranges; never from before the history starts. An account with
anything in a currency no rate is stored for is left out, and said to be. Nothing here is
stored and nothing here writes.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from scrooge import networth, performance
from scrooge.networth import PENSION, PILLAR2, MissingRateError, Series
from scrooge.storage.store import committed_or_typed

PILLARS = (PILLAR2, PENSION)  # in the order they are listed, as net worth lists them
RANGES = ("1y", "ytd")  # the twelve months up to the last day, and the year to date
_CENT = Decimal("0.01")
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Change:
    """What one pension did from the end of `since` to the end of the last day."""

    range: str  # one of RANGES
    since: date  # the day whose end the change is measured from
    start: Decimal | None  # worth at the end of `since`; None: nothing was known of it then
    paid_in: Decimal | None  # money paid in, less what was taken out; None: not to be told
    twr: Decimal | None  # time-weighted, in percent over the range; None: not to be told
    mwr: Decimal | None  # money-weighted, in percent; a year when `mwr_a_year`
    mwr_a_year: bool = False
    twr_since: date | None = None  # the first day `twr` counts, when after the range's first


@dataclass(frozen=True, slots=True)
class Pension:
    """One pension account of one person."""

    owner: str
    account: str
    pillar: str  # one of PILLARS
    value: Decimal  # at the end of `Pensions.day`, in `Pensions.currency`, to the cent
    cash: Decimal | None  # Pillar 3a: what of `value` is not invested in fund units; else None
    # Pillar 2: the day of the figure it is carried at, and whether it was typed rather than
    # read from a certificate; None and False for Pillar 3a
    stated: date | None
    typed: bool
    changes: tuple[Change, ...]  # one per RANGES, in their order


@dataclass(frozen=True, slots=True)
class Pensions:
    currency: str
    day: date | None  # the last day net worth is known for; None: nothing is
    pensions: tuple[Pension, ...]  # by owner, pillar, value (largest first) and account
    missing: tuple[str, ...] = ()  # why accounts were left out: a currency without a rate
    left_out: tuple[str, ...] = ()  # those accounts, named with their owner


def run(
    conn: sqlite3.Connection,
    *,
    owner: str | None = None,
    currency: str = networth.DEFAULT_CURRENCY,
) -> Pensions:
    """The pension accounts of `owner`, or of everyone, in `currency`."""
    valued = networth.value(conn, owner)
    if valued is None:
        return Pensions(currency, None, ())
    by_account: dict[int, list[Series]] = defaultdict(list)
    for each in valued.series:
        if each.asset in PILLARS:
            by_account[each.account_id].append(each)
    if not by_account:
        return Pensions(currency, valued.days[-1], ())
    days = valued.days
    rates = networth.rates_on(conn, days)
    # Measured as the performance view measures an investment account: the same flows, and
    # the same days of return.
    flows = performance.external_flows(conn, set(by_account), days)
    floor = performance.floors(currency, rates, days)
    names = dict(conn.execute("SELECT id, name FROM accounts").fetchall())
    figures = _figures(conn, set(by_account))
    on_last = {code: values[-1:] for code, values in rates.items()}
    found: list[Pension] = []
    missing: set[str] = set()
    left_out: list[str] = []
    for account_id, series in sorted(by_account.items()):
        holder, pillar = series[0].owner, series[0].asset
        try:
            daily = performance.daily_worth(
                [account_id], {account_id: series}, flows, days, currency=currency, rates=rates
            )
        except MissingRateError as exc:
            missing.add(str(exc))
            left_out.append(f"{names[account_id]} ({holder})")
            continue
        cash = sum(
            (
                networth.convert(s.values[-1:], s.currency, currency, on_last)[0]
                for s in series
                if s.instrument_id is None
            ),
            Decimal(0),
        )
        first, last, typed = figures.get(account_id, (None, None, False))
        if pillar == PENSION:
            changes = tuple(_measured(range_, days, daily, floor) for range_ in RANGES)
        else:
            changes = tuple(_stated(range_, days, daily.values, first) for range_ in RANGES)
        found.append(
            Pension(
                holder,
                names[account_id],
                pillar,
                daily.values[-1].quantize(_CENT),
                cash.quantize(_CENT) if pillar == PENSION else None,
                last if pillar == PILLAR2 else None,
                typed and pillar == PILLAR2,
                changes,
            )
        )
    found.sort(key=lambda p: (p.owner, PILLARS.index(p.pillar), -p.value, p.account))
    return Pensions(currency, days[-1], tuple(found), tuple(sorted(missing)), tuple(left_out))


def _measured(
    range_: str, days: tuple[date, ...], daily: performance.Days, floor: performance.Days
) -> Change:
    """What a 3a account did over `range_`, measured as an investment account is: its money
    paid in, and both its returns."""
    base = performance.base_day(range_, days[0], days[-1])
    start = (base - days[0]).days
    within = daily.after(start)
    measured = performance.measure(
        within.values,
        within.flows,
        base + _ONE_DAY,
        late=within.late,
        floor=floor.after(start).values,
    )
    return Change(
        range_,
        base,
        measured.start.quantize(_CENT),
        measured.net_flows.quantize(_CENT),
        measured.twr,
        measured.mwr,
        measured.a_year,
        measured.since,
    )


def _stated(
    range_: str, days: tuple[date, ...], values: list[Decimal], first: date | None
) -> Change:
    """What a pension fund's stated figure was at the start of `range_`: nothing is known of
    it before its first figure, and nothing of what was paid in or returned."""
    base = performance.base_day(range_, days[0], days[-1])
    start = None
    if first is not None and first <= base:
        start = values[(base - days[0]).days].quantize(_CENT)
    return Change(range_, base, start, None, None, None)


def _figures(conn: sqlite3.Connection, ids: set[int]) -> dict[int, tuple[date, date, bool]]:
    """account -> the day of its first figure stated (a balance a statement gives, or one
    typed), that of its last, and whether that last one was typed."""
    rows = conn.execute(
        "SELECT b.account_id, b.date, b.statement_file_id IS NULL FROM balances b "  # noqa: S608  # only the fixed condition of `store.committed_or_typed` is interpolated
        f"WHERE {committed_or_typed('b.statement_file_id')} ORDER BY b.date"
    ).fetchall()
    figures: dict[int, tuple[date, date, bool]] = {}
    for account, day_text, typed in rows:
        if account not in ids:
            continue
        day = date.fromisoformat(day_text)
        first = figures[account][0] if account in figures else day
        figures[account] = (first, day, bool(typed))
    return figures
