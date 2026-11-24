"""Net worth, day by day: what the dashboard draws (D20).

Only committed statements are read. For every day: the cash of each account, plus what each
account held that day at the last close on or before it, each converted at that day's rate
into the currency asked for. Nothing here is stored and nothing here writes.

**The days run to the newest close or rate stored** (decided 2026-11-04), or to the last day
committed statements cover, or typed, or a pension certificate is dated, when that is later
(no day after today can be typed). A certificate covers no day around its own, so the days a
person's statements cover end where their last statement does.
After the last day a person's statements cover, what they held then is carried: the same cash
and the same quantities (a split still multiplies them), at the closes and rates of each day
after, and a note says so. A bill goes on towards its face. So the first page shows what is
held at the latest prices, not at those of the last statement.

- **Cash** runs from the statement balances with what the account's own statements booked
  since; an account starts with its first statement and counts for nothing before. Money
  set aside inside an account (Revolut's savings, D8) stays part of it. An account that is
  only ever typed is carried flat between the days it was typed for.
- **A second-pillar pension fund** has no assets to follow: it is valued like cash in francs,
  on a line of its own (`Pillar 2`), nothing before the day of its first certificate, then
  what each certificate states until the day of the next one (decided 2026-10-10), or what
  was typed for it in between, as the fund shows it online (decided 2026-11-21).
- **Shares, ETFs and funds** are the quantity held (`storage.holdings`) times the last close
  on or before the day: typed, then printed by a statement, then fetched. Without any close
  the price of the last trade stands in, and a note says so.
- **Bills** have no quoted price: each lot runs in a straight line from what it cost on the
  day it was bought to its face value at maturity.
- **Options and futures are not assets**: their premiums are cash, nothing else is counted.

**Liquid and frozen** (decided 2026-11-24): what is in a pension, of the second pillar or the
third, is frozen until retirement, and so is what an account the config names `frozen` holds
(a rent deposit). Everything else is liquid, bills included: they are sold or mature soon.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import TypeVar

from scrooge import query
from scrooge.extractors.common import month_end
from scrooge.models import CERTIFICATE_SOURCES
from scrooge.storage import holdings
from scrooge.storage.bills import Event, open_lots
from scrooge.storage.money import from_nano
from scrooge.storage.store import committed_or_typed

BASE = "CHF"  # every stored rate is francs per unit of the currency
CURRENCIES = ("EUR", "CHF", "USD", "GBP", "JPY")  # what net worth can be shown in
DEFAULT_CURRENCY = "EUR"
DEFAULT_COLUMNS = ("CHF", "USD", "EUR")  # the currencies of the month-end table, side by side
CASH, ETFS, STOCKS, BILLS, PENSION, CARD = "Cash", "ETFs", "Stocks", "Bills", "Pillar 3a", "Card"
PILLAR2 = "Pillar 2"  # an occupational pension fund: only the figure its certificates state
ASSETS = (CASH, ETFS, STOCKS, BILLS, PILLAR2, PENSION, CARD)  # in the order they are listed
# From the bottom of a stacked chart up: what moves least first, so that the swings of the
# rest do not shake it. A card is only ever owed: it comes last and hangs below zero.
STACK = (CASH, BILLS, PILLAR2, PENSION, ETFS, STOCKS, CARD)
LIQUID, FROZEN = "Liquid", "Frozen"
LIQUIDITY = (LIQUID, FROZEN)  # in the order they are listed
FROZEN_ASSETS = (PILLAR2, PENSION)  # frozen whichever account holds them
_PENSION_SOURCE = "finpension"
_CARRIED_FLAT = "manual"  # the kind of an account without rows: typed, or a pension fund's
_BY_TYPE = {"etf": ETFS, "stock": STOCKS, "bill": BILLS, "bond": BILLS, "fund": PENSION}
_CENT = Decimal("0.01")
_ONE_DAY = timedelta(days=1)
# Which close counts when a day has several: typed, printed by a statement, fetched; the
# price of a trade only where there is nothing else.
_TRADE_PRICE = 3
_T = TypeVar("_T")


class MissingRateError(Exception):
    """No rate is stored for a currency: nothing in it can be converted."""


@dataclass(frozen=True, slots=True)
class FrozenAccount:
    """An account the config names `frozen`, by its name as the pages show it: of `owner`
    alone, or without one, every account so called, whoever's it is."""

    account: str
    owner: str | None = None

    def names(self, owner: str, account: str) -> bool:
        return self.account == account and self.owner in (None, owner)


@dataclass(frozen=True, slots=True)
class Line:
    """What one person holds of one kind of asset, day by day, in the currency asked for: of
    what can be reached, or of what is frozen."""

    owner: str
    asset: str
    values: tuple[Decimal, ...]  # one per day of `NetWorth.days`, to the cent
    liquidity: str = LIQUID  # one of LIQUIDITY


@dataclass(frozen=True, slots=True)
class NetWorth:
    currency: str
    days: tuple[date, ...]  # every day from the first anything is known for, oldest first
    lines: tuple[Line, ...]  # by owner, asset and liquidity; what a person never held is absent
    notes: tuple[str, ...]  # what the reader must know about how a figure came about
    # What came into view, by owner and asset: the balance a cash series starts with, before its
    # first day's rows, and the shares a statement says were held at its start (`Series.arrival`),
    # after the first day of all. Net worth rises by it without anything having been earned: a
    # change over days that takes it out is what moved.
    arrivals: tuple[Line, ...] = ()

    @property
    def total(self) -> tuple[Decimal, ...]:
        return _added(line.values for line in self.lines) or (Decimal(0),) * len(self.days)

    @property
    def arrived(self) -> tuple[Decimal, ...]:
        """What came into view each day, everyone and every asset together."""
        return _added(line.values for line in self.arrivals) or (Decimal(0),) * len(self.days)

    def by(self, attribute: str, *, arrivals: bool = False) -> dict[str, tuple[Decimal, ...]]:
        """The lines (their arrivals) added up by "owner", "asset" or "liquidity", in the order
        they are listed."""
        groups: dict[str, list[tuple[Decimal, ...]]] = {}
        for line in self.arrivals if arrivals else self.lines:
            groups.setdefault(getattr(line, attribute), []).append(line.values)
        return {name: _added(values) for name, values in groups.items()}


@dataclass(frozen=True, slots=True)
class Check:
    """A line of a statement's own valuation next to what is derived for the same accounts."""

    owner: str
    accounts: str
    day: date
    label: str  # the statement's word: Cash, Stock, Bonds, Options, Total, ...
    currency: str
    printed: Decimal
    derived: Decimal | None  # None: something these pages do not count (options, accruals)

    @property
    def difference(self) -> Decimal | None:
        return None if self.derived is None else self.derived - self.printed


def before(values: Sequence[Decimal], days: int) -> Decimal | None:
    """What a series was worth `days` before its last day; None when that is before its first."""
    return values[-1 - days] if len(values) > days else None


def _added(series: Iterable[Sequence[Decimal]]) -> tuple[Decimal, ...]:
    return tuple(sum(values, Decimal(0)) for values in zip(*series, strict=True))


# ----------------------------------------------------------------------------------------
# What every figure is made of: one series per account and per holding, in its own currency
# ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Quote:
    """The close a holding is valued at on a day: the last one on or before it."""

    day: date  # the day the close is from; after the day valued where the first one stands in
    close: Decimal
    traded: bool  # no close is stored for that day: the price of a trade stands in


@dataclass(frozen=True, slots=True)
class Series:
    """What one account's cash, or one instrument it holds, is worth day by day."""

    owner: str
    account_id: int
    asset: str
    currency: str
    values: list[Decimal]  # one per day, not rounded
    instrument_id: int | None = None  # None: the cash of the account
    quantity: Decimal | None = None  # of the instrument, held at the end of the last day
    quote: Quote | None = None  # what the last day is valued at; None for cash and bills
    # What it brings on the day it first counts, in `currency`: a cash balance the statements
    # start from (before that day's rows), or what the shares held at the start of the history
    # are worth that day; None when it starts from nothing (its rows, or shares bought)
    arrival: tuple[date, Decimal] | None = None


@dataclass(frozen=True, slots=True)
class _Account:
    id: int
    owner: str
    name: str
    kind: str
    currency: str
    source: str | None
    # The last day the statements of its owner cover; for a typed account, its last day typed
    # when that is later (decided 2026-11-21: a balance or position typed today counts today).
    through: date
    typed: bool = False  # a balance or position was typed for it


@dataclass(frozen=True, slots=True)
class Valued:
    """Every series up to the last day, each in its own currency: what every figure is made of."""

    days: tuple[date, ...]
    series: tuple[Series, ...]
    notes: tuple[str, ...]
    security_notes: tuple[str, ...]  # those of `notes` that are about what is held
    # owner -> the last day their committed statements cover, or for one with typed figures
    # only, the last day typed: what they held then is carried to the last of `days`
    through: dict[str, date] = field(default_factory=dict)

    @property
    def covered(self) -> date:
        """The earliest of the owners' last days: up to it everyone in view is known."""
        return min(self.through.values(), default=self.days[-1])


def _days(first: date, last: date) -> tuple[date, ...]:
    return tuple(first + timedelta(days=n) for n in range((last - first).days + 1))


def _last_days(conn: sqlite3.Connection, owner: str | None) -> tuple[dict[str, date], set[str]]:
    """owner -> the last day their committed statements cover, and the owners whose only
    statements are pension certificates. A certificate, the one day it is dated, covers none
    around it: it counts only for a person who has nothing else."""
    rows = conn.execute(
        "SELECT o.name, max(f.period_end) FILTER (WHERE f.source NOT IN (?, ?)), "
        "max(f.period_end) FROM statement_files f JOIN owners o ON o.id = f.owner_id "
        "WHERE f.committed_at IS NOT NULL AND o.name = coalesce(?, o.name) GROUP BY o.name",
        (*CERTIFICATE_SOURCES, owner),
    ).fetchall()
    last = {
        str(name): date.fromisoformat(statements or latest) for name, statements, latest in rows
    }
    return last, {str(name) for name, statements, _ in rows if statements is None}


def _certified_days(conn: sqlite3.Connection, owner: str | None) -> dict[int, tuple[str, date]]:
    """account -> its owner and the day of its last committed certificate."""
    rows = conn.execute(
        "SELECT s.account_id, o.name, max(f.period_end) FROM statement_files f "
        "JOIN statement_accounts s ON s.statement_file_id = f.id "
        "JOIN owners o ON o.id = f.owner_id "
        "WHERE f.committed_at IS NOT NULL AND f.source IN (?, ?) AND o.name = coalesce(?, o.name) "
        "GROUP BY s.account_id",
        (*CERTIFICATE_SOURCES, owner),
    )
    return {int(account): (str(name), date.fromisoformat(day)) for account, name, day in rows}


def _priced_through(conn: sqlite3.Connection) -> list[date]:
    """The newest day a close or a rate is stored for, if any is."""
    newest = conn.execute(
        "SELECT max(day) FROM (SELECT max(date) AS day FROM prices "
        "UNION ALL SELECT max(date) FROM fx_rates "
        "UNION ALL SELECT max(date) FROM committed_closes "
        "UNION ALL SELECT max(date) FROM committed_rates)"
    ).fetchone()[0]
    return [date.fromisoformat(newest)] if newest else []


def _typed_days(conn: sqlite3.Connection, owner: str | None) -> dict[int, tuple[str, date]]:
    """account -> its owner and the last day a balance or a position was typed for it."""
    rows = conn.execute(
        "SELECT t.account_id, o.name, max(t.date) FROM (SELECT account_id, date FROM balances "
        "WHERE source = 'manual' UNION ALL SELECT account_id, date FROM positions "
        "WHERE source = 'manual') t JOIN accounts a ON a.id = t.account_id "
        "JOIN owners o ON o.id = a.owner_id WHERE o.name = coalesce(?, o.name) "
        "GROUP BY t.account_id",
        (owner,),
    )
    return {int(account): (str(name), date.fromisoformat(day)) for account, name, day in rows}


def _accounts(
    conn: sqlite3.Connection,
    last_days: Mapping[str, date],
    typed: Mapping[int, tuple[str, date]],
    certified: Mapping[int, tuple[str, date]],
) -> dict[int, _Account]:
    """The accounts of the people who have committed statements or typed figures. An account
    counts up to its owner's last day covered, or its own last day typed or certified when that
    is later (decided 2026-11-21: a balance or position typed today counts today)."""
    rows = conn.execute(
        "SELECT a.id, o.name, a.name, a.kind, a.currency, a.source FROM accounts a "
        "JOIN owners o ON o.id = a.owner_id ORDER BY a.id"
    ).fetchall()
    people = set(last_days) | {name for name, _ in typed.values()}
    return {
        int(r[0]): _Account(
            int(r[0]),
            r[1],
            r[2],
            r[3],
            r[4],
            r[5],
            max(
                last_days.get(r[1], date.min),
                typed.get(int(r[0]), ("", date.min))[1],
                certified.get(int(r[0]), ("", date.min))[1],
            ),
            int(r[0]) in typed,
        )
        for r in rows
        if r[1] in people
    }


def value(conn: sqlite3.Connection, owner: str | None) -> Valued | None:
    """Every series of `owner`, or of everyone, up to the newest close or rate stored, or the
    last day committed when that is later; each person carried from their own last day.

    None when nothing is committed, or nothing is known of what is.
    """
    query.check_names(conn, query.Filter(owner=owner))
    last_days, certificates_only = _last_days(conn, owner)
    typed = _typed_days(conn, owner)
    if not last_days and not typed:
        return None
    certified = _certified_days(conn, owner)
    accounts = _accounts(conn, last_days, typed, certified)
    through = {account.id: account.through for account in accounts.values()}
    flows = _flows(conn, through)
    anchors = _anchors(conn, through)
    splits = _splits(conn)
    held = [
        _carried_from(holding, through[holding.account_id], splits.get(holding.instrument_id, []))
        for holding in holdings.held(conn, committed=True)
        if holding.account_id in accounts
        and holding.steps
        and holding.steps[0].day <= through[holding.account_id]
    ]
    starts = [min(booked) for booked in flows.values()]
    starts += [min(known) for known in anchors.values()]
    starts += [holding.steps[0].day for holding in held]
    if not starts:
        return None
    last = max(
        [
            *last_days.values(),
            *(day for _, day in typed.values()),
            *(day for _, day in certified.values()),
            *_priced_through(conn),
        ]
    )
    days = _days(min(starts), last)
    notes = [
        f"The statements of {name} end on {day}: from then on, what was held that day is "
        f"valued at the closes and rates stored up to {last}."
        for name, day in sorted(last_days.items())
        if day < last and name not in certificates_only
    ]
    series = _cash(accounts, flows, anchors, days, notes)
    security_notes: list[str] = []
    series += _securities(conn, accounts, held, days, security_notes)
    # How far each person is known: their statements; with certificates alone, the later of
    # those and the figures typed.
    known = {name: last_days[name] for name in certificates_only}
    for name, typed_day in typed.values():
        known[name] = max(typed_day, known.get(name, typed_day))
    known.update({n: d for n, d in last_days.items() if n not in certificates_only})
    return Valued(
        days,
        tuple(series),
        (*notes, *security_notes),
        tuple(security_notes),
        known,
    )


# ---------------------------------------------------------------------------------- cash


@dataclass(frozen=True, slots=True)
class _Flow:
    nano: int = 0  # what the account's own statements booked that day
    aside: int = 0  # what went into the money set aside inside the account that day


def _flows(conn: sqlite3.Connection, through: Mapping[int, date]) -> dict[int, dict[date, _Flow]]:
    """account -> day -> what committed statements booked up to the day in `through` (the
    accounts counted), oldest day first.

    Rows that another source books on an account (the interest of Revolut's savings, D8) are
    not in its statement balance: they add to what is set aside there, and so does every
    move the account's own statement calls internal.
    """
    rows = conn.execute(
        "SELECT t.account_id, t.booking_date, t.amount_nano, t.kind, "  # noqa: S608  # only the fixed condition of `store.committed_or_typed` is interpolated
        "f.source IS NULL OR f.source IS a.source AS own "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "LEFT JOIN statement_files f ON f.id = t.statement_file_id "
        f"WHERE {committed_or_typed('t.statement_file_id')} ORDER BY t.booking_date"
    ).fetchall()
    fed_by_another = {int(r[0]) for r in rows if not r[4]}
    flows: dict[int, dict[date, _Flow]] = defaultdict(dict)
    for account, day_text, nano, kind, own in rows:
        day = date.fromisoformat(day_text)
        if account not in through or day > through[account]:
            continue
        flow = flows[account].get(day, _Flow())
        if not own:
            flow = _Flow(flow.nano, flow.aside + nano)
        elif kind == "internal" and account in fed_by_another:
            flow = _Flow(flow.nano + nano, flow.aside - nano)
        else:
            flow = _Flow(flow.nano + nano, flow.aside)
        flows[account][day] = flow
    return flows


def _anchors(conn: sqlite3.Connection, through: Mapping[int, date]) -> dict[int, dict[date, int]]:
    """account -> day -> the balance a statement gives, or that was typed, up to the day in
    `through` (the accounts counted), oldest first.

    A statement counts once it is committed. A pension certificate is a statement of the one
    day it is dated.
    """
    rows = conn.execute(
        "SELECT b.account_id, b.date, b.amount_nano FROM balances b "  # noqa: S608  # only the fixed condition of `store.committed_or_typed` is interpolated
        f"WHERE {committed_or_typed('b.statement_file_id')} ORDER BY b.date"
    ).fetchall()
    anchors: dict[int, dict[date, int]] = defaultdict(dict)
    for account, day_text, nano in rows:
        day = date.fromisoformat(day_text)
        if account in through and day <= through[account]:
            anchors[account][day] = int(nano)
    return anchors


def _cash(
    accounts: Mapping[int, _Account],
    flows: Mapping[int, Mapping[date, _Flow]],
    anchors: Mapping[int, Mapping[date, int]],
    days: Sequence[date],
    notes: list[str],
) -> list[Series]:
    series: list[Series] = []
    for account in accounts.values():
        booked, known = flows.get(account.id, {}), anchors.get(account.id, {})
        if not booked and not known:
            continue
        values = _balances(booked, known, days)
        if not known:
            pass  # no statement prints a balance (Tastytrade): the cash adds up from its first row
        elif account.source is None:
            typed = max(known)
            notes.append(
                f"{account.name} of {account.owner} has no statements: it is carried at what "
                f"was typed, last for {typed}."
            )
        elif account.kind == _CARRIED_FLAT:
            notes.append(
                f"{account.name} of {account.owner} is carried at what its certificates "
                f"state{', or was typed' if account.typed else ''}, the last "
                f"{'for' if account.typed else 'one for'} {max(known)}."
            )
        asset = CASH
        if account.kind == "card":
            asset = CARD
        elif account.source == _PENSION_SOURCE:
            asset = PENSION
        elif account.kind == _CARRIED_FLAT and account.source is not None:
            asset = PILLAR2
        opening = _opening(booked, known)
        series.append(
            Series(
                account.owner,
                account.id,
                asset,
                account.currency,
                values,
                arrival=(min([*booked, *known]), from_nano(opening)) if opening else None,
            )
        )
    return series


def _opening(booked: Mapping[date, _Flow], known: Mapping[date, int]) -> int:
    """The balance an account's cash starts from, before the rows of its first day, in nano:
    what its first balance says, less what was booked up to it. Zero when no balance is known,
    the cash then adding up from its first row."""
    if not known:
        return 0
    first = min(known)
    return known[first] - sum(f.nano for day, f in booked.items() if day <= first)


def _balances(
    booked: Mapping[date, _Flow], known: Mapping[date, int], days: Sequence[date]
) -> list[Decimal]:
    """The account's cash at the end of each day; nothing before its first day.

    A balance on a day replaces what the rows lead to: the reconciled figure wins (D10), and
    the chain check has made sure the two agree.
    """
    start = min([*booked, *known])
    balance = 0
    if known:
        first = min(known)
        balance = known[first] - sum(f.nano for day, f in booked.items() if day <= first)
    aside = 0
    values: list[Decimal] = []
    for day in days:
        if day < start:
            values.append(Decimal(0))
            continue
        flow = booked.get(day)
        if flow is not None:
            balance += flow.nano
            aside += flow.aside
        if day in known:
            balance = known[day]
        values.append(from_nano(balance + aside))
    return values


# ---------------------------------------------------------------------------- securities


def _splits(conn: sqlite3.Connection) -> dict[int, list[tuple[date, Decimal, Decimal]]]:
    """instrument -> (day, new shares, old shares) of each split, oldest first."""
    splits: dict[int, list[tuple[date, Decimal, Decimal]]] = defaultdict(list)
    for instrument, day, new, old in conn.execute(
        "SELECT instrument_id, date, numerator, denominator FROM splits ORDER BY date"
    ):
        splits[instrument].append((date.fromisoformat(day), Decimal(new), Decimal(old)))
    return splits


def _carried_from(
    holding: holdings.Holding, last: date, splits: Sequence[tuple[date, Decimal, Decimal]]
) -> holdings.Holding:
    """`holding` as the statements up to `last` leave it: after that only a split changes it."""
    steps = [step for step in holding.steps if step.day <= last]
    quantity = steps[-1].quantity
    for day, new, old in splits:
        if day > last:
            quantity = quantity * new / old
            steps.append(holdings.Step(day, quantity))
    return replace(holding, steps=tuple(steps))


def _securities(
    conn: sqlite3.Connection,
    accounts: Mapping[int, _Account],
    held: Sequence[holdings.Holding],
    days: Sequence[date],
    notes: list[str],
) -> list[Series]:
    instruments = {
        int(r[0]): (str(r[1]), str(r[2]), str(r[3]), r[4] and date.fromisoformat(r[4]))
        for r in conn.execute("SELECT id, symbol, type, currency, maturity FROM instruments")
    }
    closes = _closes(conn)
    lots = lot_trades(conn)
    unpriced: set[str] = set()
    series: list[Series] = []
    for holding in held:
        account = accounts[holding.account_id]
        symbol, kind, currency, maturity = instruments[holding.instrument_id]
        quote = None
        if kind in {"bill", "bond"}:
            trades = [
                trade
                for trade in lots.get((holding.account_id, holding.instrument_id), [])
                if trade.day <= account.through
            ]
            values = bill_values(trades, maturity, days)
            if not trades and any(step.quantity for step in holding.steps):
                notes.append(f"{symbol} was bought before the history starts: it is not counted.")
        else:
            points = closes.get(holding.instrument_id, [])
            values = _share_values(holding, [(day, close) for day, _, close in points], days)
            quoted = [(day, Quote(day, close, rank == _TRADE_PRICE)) for day, rank, close in points]
            quote = _carried(quoted, days[-1:])[0] if quoted else None
            if any(values) and all(rank == _TRADE_PRICE for _, rank, _ in points):
                unpriced.add(symbol)
            if not points and any(step.quantity for step in holding.steps):
                notes.append(f"{symbol} has no price at all: it is not counted.")
        if not any(values):
            continue
        # Shares held before the history starts come into view with what they are worth on
        # its first day; those bought then do not.
        arrival = None
        start = holding.steps[0]
        if kind not in {"bill", "bond"} and holding.opening > 0 and start.quantity:
            worth = values[(start.day - days[0]).days]
            arrival = (start.day, worth * holding.opening / start.quantity)
        asset = PENSION if account.source == _PENSION_SOURCE else _BY_TYPE[kind]
        series.append(
            Series(
                account.owner,
                account.id,
                asset,
                currency,
                values,
                holding.instrument_id,
                holding.on(days[-1]),
                quote,
                arrival,
            )
        )
    if unpriced:
        notes.append(
            f"No close is stored for {', '.join(sorted(unpriced))}: valued at the price of the "
            "last trade. Run `scrooge prices`."
        )
    return series


def _closes(conn: sqlite3.Connection) -> dict[int, list[tuple[date, int, Decimal]]]:
    """instrument -> (day, rank, close), one per day, oldest first: the best close of each day.

    The price a trade was made at stands in on days without any close.
    """
    best: dict[int, dict[date, tuple[int, Decimal]]] = defaultdict(dict)
    rows = conn.execute(
        "SELECT instrument_id, date, close, CASE source WHEN 'manual' THEN 0 ELSE 2 END "  # noqa: S608  # only the fixed condition of `store.committed_or_typed` is interpolated
        "FROM prices "
        "UNION ALL SELECT instrument_id, date, close, 1 FROM committed_closes "
        "UNION ALL SELECT tr.instrument_id, t.booking_date, tr.price, 3 FROM trades tr "
        "JOIN transactions t ON t.id = tr.transaction_id "
        f"WHERE tr.price IS NOT NULL AND {committed_or_typed('t.statement_file_id')}"
    )
    for instrument, day_text, close, rank in rows:
        day = date.fromisoformat(day_text)
        known = best[instrument].get(day)
        if known is None or rank < known[0]:
            best[instrument][day] = (rank, Decimal(close))
    return {
        instrument: [(day, rank, close) for day, (rank, close) in sorted(points.items())]
        for instrument, points in best.items()
    }


def _carried(points: Sequence[tuple[date, _T]], days: Sequence[date]) -> list[_T]:
    """For each day the value of the last of `points` on or before it; the first one stands
    in for the days before it. There must be a point."""
    when = [day for day, _ in points]
    return [points[max(bisect_right(when, day) - 1, 0)][1] for day in days]


def _share_values(
    holding: holdings.Holding, closes: Sequence[tuple[date, Decimal]], days: Sequence[date]
) -> list[Decimal]:
    if not closes:
        return [Decimal(0)] * len(days)
    steps = [(step.day, step.quantity) for step in holding.steps]
    when = [day for day, _ in steps]
    values: list[Decimal] = []
    for day, price in zip(days, _carried(closes, days), strict=True):
        index = bisect_right(when, day)
        # Like an account, a holding starts with the first thing known about it: nothing
        # is counted before. Shares held when the account's statements begin are known from
        # their first day (`holdings.held`).
        quantity = steps[index - 1][1] if index else Decimal(0)
        values.append(quantity * price)
    return values


@dataclass(frozen=True, slots=True)
class LotTrade:
    """A committed purchase or disposal of an instrument in an account, as lots see it."""

    day: date  # the booking date
    event: Event
    priced: bool  # false for shares moved in or out in kind without a price: no cost is known


def lot_trades(conn: sqlite3.Connection) -> dict[tuple[int, int], list[LotTrade]]:
    """(account, instrument) -> its committed purchases and disposals, in the order lots close."""
    rows = conn.execute(
        "SELECT t.account_id, tr.instrument_id, t.booking_date, t.id, tr.quantity, tr.cost_nano, "  # noqa: S608  # only the fixed condition of `store.committed_or_typed` is interpolated
        "tr.price IS NOT NULL "
        "FROM trades tr JOIN transactions t ON t.id = tr.transaction_id "
        f"WHERE {committed_or_typed('t.statement_file_id')} "
        # on one day, purchases come before disposals
        "ORDER BY t.booking_date, tr.quantity LIKE '-%', t.id"
    )
    trades: dict[tuple[int, int], list[LotTrade]] = defaultdict(list)
    for account, instrument, day, txn_id, quantity, cost, priced in rows:
        event = Event(str(txn_id), Decimal(quantity), int(cost))
        trades[account, instrument].append(LotTrade(date.fromisoformat(day), event, bool(priced)))
    return trades


def bill_values(
    trades: Sequence[LotTrade], maturity: date | None, days: Sequence[date]
) -> list[Decimal]:
    """What the lots still held are worth each day: from cost towards face, in a straight line."""
    bought_on = {trade.event.txn_id: trade.day for trade in trades}
    values: list[Decimal] = []
    done = 0
    lots: list[tuple[str, Decimal, int]] = []
    for day in days:
        seen = done
        while done < len(trades) and trades[done].day <= day:
            done += 1
        if done != seen:
            lots = open_lots(trade.event for trade in trades[:done])
        worth = Decimal(0)
        for purchase, face, cost_nano in lots:
            cost, bought = from_nano(cost_nano), bought_on[purchase]
            if maturity is None or maturity <= bought:
                worth += cost
                continue
            elapsed = min((day - bought).days, (maturity - bought).days)
            worth += cost + (face - cost) * elapsed / (maturity - bought).days
        values.append(worth)
    return values


# ----------------------------------------------------------------------------- currencies


def rates_on(conn: sqlite3.Connection, days: Sequence[date]) -> dict[str, list[Decimal]]:
    """currency -> francs per unit on each day: the last rate on or before it."""
    best: dict[str, dict[date, tuple[int, Decimal]]] = defaultdict(dict)
    rows = conn.execute(
        "SELECT currency, date, rate_to_chf, CASE source WHEN 'manual' THEN 0 ELSE 2 END "
        "FROM fx_rates UNION ALL SELECT currency, date, rate, 1 FROM committed_rates"
    )
    for currency, day_text, rate, rank in rows:
        day = date.fromisoformat(day_text)
        known = best[currency].get(day)
        if known is None or rank < known[0]:
            best[currency][day] = (rank, Decimal(rate))
    rates = {
        currency: _carried([(day, rate) for day, (_, rate) in sorted(points.items())], days)
        for currency, points in best.items()
    }
    rates[BASE] = [Decimal(1)] * len(days)
    return rates


def convert(
    values: Sequence[Decimal],
    currency: str,
    into: str,
    rates: Mapping[str, Sequence[Decimal]],
) -> list[Decimal]:
    """`values` of `currency` in `into`, each at the rate of its own day, through francs."""
    if currency == into:
        return list(values)
    for needed in (currency, into):
        if needed not in rates:
            msg = f"no exchange rate is stored for {needed}: run `scrooge prices`"
            raise MissingRateError(msg)
    return [
        value * rate / target
        for value, rate, target in zip(values, rates[currency], rates[into], strict=True)
    ]


# -------------------------------------------------------------------------------- results


def run(
    conn: sqlite3.Connection,
    *,
    owner: str | None = None,
    currency: str = DEFAULT_CURRENCY,
    frozen: Collection[FrozenAccount] = (),
) -> NetWorth:
    """Net worth of `owner`, or of everyone, on every day, in `currency` (see `value`); what
    the accounts `frozen` names hold is frozen (see `worth_of`)."""
    return worth_of(conn, value(conn, owner), currency, frozen=frozen)


def worth_of(
    conn: sqlite3.Connection,
    valued: Valued | None,
    currency: str,
    rates: Mapping[str, Sequence[Decimal]] | None = None,
    *,
    frozen: Collection[FrozenAccount] = (),
) -> NetWorth:
    """`run` for what `value` already worked out: a page that shows more than net worth
    values everything once. `rates` are those `rates_on` read for `valued.days`, for who
    converts into several currencies; they are read here without them. A line is frozen when
    its asset is a pension (`FROZEN_ASSETS`) or one of `frozen` names its account, and liquid
    otherwise."""
    if valued is None:
        return NetWorth(currency, (), (), ())
    if rates is None:
        rates = rates_on(conn, valued.days)
    names = _account_names(conn) if frozen else {}
    sums: dict[tuple[str, str, str], list[list[Decimal]]] = defaultdict(list)
    came: dict[tuple[str, str, str], list[list[Decimal]]] = defaultdict(list)
    first = valued.days[0]
    for series in valued.series:
        converted = convert(series.values, series.currency, currency, rates)
        name = names.get(series.account_id, "")
        held = series.asset in FROZEN_ASSETS or any(f.names(series.owner, name) for f in frozen)
        key = (series.owner, series.asset, FROZEN if held else LIQUID)
        sums[key].append(converted)
        if series.arrival is None or series.arrival[0] <= first:
            continue
        index = (series.arrival[0] - first).days
        native = [Decimal(0)] * len(converted)
        native[index] = series.arrival[1]
        came[key].append(convert(native, series.currency, currency, rates))

    def ordered(groups: Mapping[tuple[str, str, str], list[list[Decimal]]]) -> tuple[Line, ...]:
        return tuple(
            Line(owner_name, asset, tuple(value.quantize(_CENT) for value in _added(parts)), reach)
            for (owner_name, asset, reach), parts in sorted(
                groups.items(),
                key=lambda item: (
                    item[0][0],
                    ASSETS.index(item[0][1]),
                    LIQUIDITY.index(item[0][2]),
                ),
            )
        )

    return NetWorth(currency, valued.days, ordered(sums), valued.notes, ordered(came))


def _account_names(conn: sqlite3.Connection) -> dict[int, str]:
    return {int(r[0]): str(r[1]) for r in conn.execute("SELECT id, name FROM accounts")}


def frozen_notes(conn: sqlite3.Connection, frozen: Iterable[FrozenAccount]) -> list[str]:
    """What the reader must know of the config's `frozen`: an entry that names no account,
    which a name mistyped there would quietly leave liquid, and a bare name that freezes the
    accounts of several people, which may be meant for one of them alone."""
    rows = conn.execute(
        "SELECT o.name, a.name FROM accounts a JOIN owners o ON o.id = a.owner_id"
    ).fetchall()
    held = {(str(owner), str(account)) for owner, account in rows}
    notes: list[str] = []
    for entry in frozen:
        owners = sorted({owner for owner, account in held if entry.names(owner, account)})
        if not owners:
            whose = f"{entry.owner} has no account" if entry.owner else "No account is"
            notes.append(
                f"{whose} called {entry.account}, which 'frozen' in scrooge.yaml names: write "
                "it as the pages name the account."
            )
        elif len(owners) > 1:
            notes.append(
                f"{', '.join(owners[:-1])} and {owners[-1]} each have an account called "
                f"{entry.account}, and 'frozen' in scrooge.yaml freezes them all: write "
                f"{{{owners[0]}: {entry.account}}} there to freeze one alone."
            )
    return notes


def balances(
    conn: sqlite3.Connection, valued: Valued | None, currency: str
) -> dict[tuple[str, str], Decimal]:
    """What each account holds on the last day, its cash and its securities together, in
    `currency` at that day's rates, to the cent; by its owner and name."""
    if valued is None:
        return {}
    names = _account_names(conn)
    rates = rates_on(conn, valued.days[-1:])
    sums: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for series in valued.series:
        [value] = convert(series.values[-1:], series.currency, currency, rates)
        sums[series.owner, names[series.account_id]] += value
    return {key: value.quantize(_CENT) for key, value in sums.items()}


# ---------------------------------------------------------------------------- month by month


@dataclass(frozen=True, slots=True)
class Figure:
    """Net worth at a month's end in one currency, how it moved since the month before and what
    came into view besides, and what of it is liquid and what frozen that day."""

    total: Decimal
    change: Decimal | None  # None: the history starts within the month
    percent: Decimal | None  # `change` in percent of the month before; None unless that is above 0
    liquid: Decimal
    frozen: Decimal
    # What accounts brought into view within the month, left out of `change`: the two add up
    # to the difference of the totals. None with `change`.
    arrived: Decimal | None


@dataclass(frozen=True, slots=True)
class MonthEnd:
    """The last day of a month there is, and net worth on it in each currency asked for."""

    month: date  # its first day
    day: date  # its last day, or the last day there is while the month is not over
    values: dict[str, Figure]  # by currency, in the order asked for

    @property
    def partial(self) -> bool:
        """Whether the month has not ended: its figures are so far."""
        return self.day < month_end(self.month)


def month_ends(
    conn: sqlite3.Connection,
    valued: Valued | None,
    currencies: Sequence[str],
    unconverted: Callable[[MissingRateError], object] | None = None,
    *,
    frozen: Collection[FrozenAccount] = (),
) -> tuple[MonthEnd, ...]:
    """Net worth at the end of every month of `valued.days` in each of `currencies`, newest
    first (see `by_month`). Everything is valued once and the rates are read once: only the
    conversion is done for each currency. With `unconverted`, a currency without rates is
    left out of every month and its error handed there, so the others are still worked
    out; without it, the error is raised. The accounts `frozen` names are frozen (`worth_of`)."""
    if valued is None:
        return ()
    rates = rates_on(conn, valued.days)
    worths: list[NetWorth] = []
    for currency in currencies:
        try:
            worths.append(worth_of(conn, valued, currency, rates, frozen=frozen))
        except MissingRateError as exc:
            if unconverted is None:
                raise
            unconverted(exc)
    return by_month(worths)


def by_month(worths: Sequence[NetWorth]) -> tuple[MonthEnd, ...]:
    """The month-ends of the same days in several currencies, newest first.

    A month ends on its last day, or on the last day there is while it is not over. Its
    change is against the end of the month before, less what accounts brought into view in
    between (`NetWorth.arrived`), which the month's recap sets apart too: an account whose
    first statement is imported brings what it held, and that was not made in the month. So
    a change is not always the difference of two totals: with what arrived, it is. The first
    month has nothing before it to change from. What is liquid and what frozen is that of the
    month's last day.
    """
    if not worths or not worths[0].days:
        return ()
    days = worths[0].days
    ends = [
        index
        for index, day in enumerate(days)
        if index == len(days) - 1 or days[index + 1].month != day.month
    ]
    nothing = (Decimal(0).quantize(_CENT),) * len(days)
    reach = [worth.by("liquidity") for worth in worths]
    found: list[MonthEnd] = []
    for at, index in enumerate(ends):
        before = ends[at - 1] if at else None
        values: dict[str, Figure] = {}
        for worth, parts in zip(worths, reach, strict=True):
            total = worth.total[index]
            liquid = parts.get(LIQUID, nothing)[index]
            frozen = parts.get(FROZEN, nothing)[index]
            if before is None:
                values[worth.currency] = Figure(total, None, None, liquid, frozen, None)
                continue
            was = worth.total[before]
            arrived = sum(worth.arrived[before + 1 : index + 1], Decimal(0)).quantize(_CENT)
            change = (total - was - arrived).quantize(_CENT)
            percent = (change * 100 / was).quantize(_CENT) if was > 0 else None
            values[worth.currency] = Figure(total, change, percent, liquid, frozen, arrived)
        found.append(MonthEnd(days[index].replace(day=1), days[index], values))
    return tuple(reversed(found))


# What a statement calls an asset, and what that is here. Its total is set against the sum
# of these: the difference is then what the statement counts and these pages do not.
_PRINTED = {"Cash": (CASH,), "Stock": (ETFS, STOCKS), "Bonds": (BILLS,)}
_PRINTED["Total"] = tuple(asset for assets in _PRINTED.values() for asset in assets)


def checks(conn: sqlite3.Connection, *, owner: str | None = None) -> tuple[Check, ...]:
    """Each statement's own valuation against what is derived for its accounts that day.

    A difference is expected and small: another close, a bill valued in a straight line,
    shares awarded and not yet vested. What the statement counts and these pages do not
    (options, accruals) has nothing derived next to it, and shows in the total.
    """
    valued = value(conn, owner)
    if valued is None:
        return ()
    rates = rates_on(conn, valued.days)
    index = {day: n for n, day in enumerate(valued.days)}
    accounts: dict[int, dict[int, str]] = {}
    found: list[Check] = []
    rows = conn.execute(
        "SELECT f.id, o.name, f.period_end, v.label, v.currency, v.value_nano "
        "FROM statement_values v JOIN statement_files f ON f.id = v.statement_file_id "
        "JOIN owners o ON o.id = f.owner_id WHERE ?1 IS NULL OR o.name = ?1 "
        "ORDER BY o.name, f.period_end, f.id, v.label",
        (owner,),
    ).fetchall()
    for file_id, owner_name, day_text, label, currency, nano in rows:
        day = date.fromisoformat(day_text)
        if day not in index:
            continue
        if file_id not in accounts:
            accounts[file_id] = {
                int(r[0]): str(r[1])
                for r in conn.execute(
                    "SELECT DISTINCT a.id, a.name FROM balances b JOIN accounts a "
                    "ON a.id = b.account_id WHERE b.statement_file_id = ? ORDER BY a.name",
                    (file_id,),
                )
            }
        of_file, at = accounts[file_id], index[day]
        derived = None
        if label in _PRINTED:
            that_day = {code: values[at : at + 1] for code, values in rates.items()}
            derived = sum(
                (
                    convert(s.values[at : at + 1], s.currency, currency, that_day)[0]
                    for s in valued.series
                    if s.account_id in of_file and s.asset in _PRINTED[label]
                ),
                Decimal(0),
            ).quantize(_CENT)
        found.append(
            Check(
                owner_name,
                ", ".join(of_file.values()),
                day,
                label,
                currency,
                from_nano(nano).quantize(_CENT),
                derived,
            )
        )
    return tuple(found)
