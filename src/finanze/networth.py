"""Net worth, day by day: what the dashboard draws (D20).

Only committed months are read. For every day: the cash of each account, plus what each
account held that day at the last close on or before it, each converted at that day's rate
into the currency asked for. Nothing here is stored and nothing here writes.

- **Cash** runs from the statement balances with what the account's own statements booked
  since; an account starts with its first statement and counts for nothing before. Money
  set aside inside an account (Revolut's savings, D8) stays part of it. An account that is
  only ever typed is carried flat between the days it was typed for.
- **A second-pillar pension fund** has no assets to follow: it is valued like cash in francs,
  on a line of its own (`Pillar 2`), nothing before the day of its first certificate, then
  what each certificate states until the day of the next one (decided 2026-10-10).
- **Shares, ETFs and funds** are the quantity held (`storage.holdings`) times the last close
  on or before the day: typed, then printed by a statement, then fetched. Without any close
  the price of the last trade stands in, and a note says so.
- **Bills** have no quoted price: each lot runs in a straight line from what it cost on the
  day it was bought to its face value at maturity.
- **Options and futures are not assets**: their premiums are cash, nothing else is counted.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from finanze import query
from finanze.extractors.common import month_end
from finanze.storage import holdings
from finanze.storage.bills import Event, open_lots
from finanze.storage.money import from_nano

BASE = "CHF"  # every stored rate is francs per unit of the currency
CURRENCIES = ("EUR", "CHF", "USD", "GBP", "JPY")  # what net worth can be shown in
DEFAULT_CURRENCY = "EUR"
CASH, ETFS, STOCKS, BILLS, PENSION, CARD = "Cash", "ETFs", "Stocks", "Bills", "Pillar 3a", "Card"
PILLAR2 = "Pillar 2"  # an occupational pension fund: only the figure its certificates state
ASSETS = (CASH, ETFS, STOCKS, BILLS, PILLAR2, PENSION, CARD)  # in the order they are listed
# From the bottom of a stacked chart up: what moves least first, so that the swings of the
# rest do not shake it. A card is only ever owed: it comes last and hangs below zero.
STACK = (CASH, BILLS, PILLAR2, PENSION, ETFS, STOCKS, CARD)
_PENSION_SOURCE = "finpension"
_CARRIED_FLAT = "manual"  # the kind of an account without rows: typed, or a pension fund's
_BY_TYPE = {"etf": ETFS, "stock": STOCKS, "bill": BILLS, "bond": BILLS, "fund": PENSION}
_CENT = Decimal("0.01")
_ONE_DAY = timedelta(days=1)
# Which close counts when a day has several: typed, printed by a statement, fetched; the
# price of a trade only where there is nothing else.
_TRADE_PRICE = 3


class MissingRateError(Exception):
    """No rate is stored for a currency: nothing in it can be converted."""


@dataclass(frozen=True, slots=True)
class Line:
    """What one person holds of one kind of asset, day by day, in the currency asked for."""

    owner: str
    asset: str
    values: tuple[Decimal, ...]  # one per day of `NetWorth.days`, to the cent


@dataclass(frozen=True, slots=True)
class NetWorth:
    currency: str
    days: tuple[date, ...]  # every day from the first anything is known for, oldest first
    lines: tuple[Line, ...]  # by owner and asset; kinds a person never held are absent
    notes: tuple[str, ...]  # what the reader must know about how a figure came about

    @property
    def total(self) -> tuple[Decimal, ...]:
        return _added(line.values for line in self.lines) or (Decimal(0),) * len(self.days)

    def by(self, attribute: str) -> dict[str, tuple[Decimal, ...]]:
        """The lines added up by "owner" or by "asset", in the order they are listed."""
        groups: dict[str, list[tuple[Decimal, ...]]] = {}
        for line in self.lines:
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


def _added(series: Iterable[Sequence[Decimal]]) -> tuple[Decimal, ...]:
    return tuple(sum(values, Decimal(0)) for values in zip(*series, strict=True))


# ----------------------------------------------------------------------------------------
# What every figure is made of: one series per account and per holding, in its own currency
# ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Series:
    owner: str
    account_id: int
    asset: str
    currency: str
    values: list[Decimal]  # one per day


@dataclass(frozen=True, slots=True)
class _Account:
    id: int
    owner: str
    name: str
    kind: str
    currency: str
    source: str | None


@dataclass(frozen=True, slots=True)
class _Valued:
    days: tuple[date, ...]
    series: tuple[_Series, ...]
    notes: tuple[str, ...]


def _days(first: date, last: date) -> tuple[date, ...]:
    return tuple(first + timedelta(days=n) for n in range((last - first).days + 1))


def _last_days(conn: sqlite3.Connection, owner: str | None) -> dict[str, date]:
    """owner -> the last day of their latest committed month."""
    rows = conn.execute(
        "SELECT o.name, max(m.month) FROM months m JOIN owners o ON o.id = m.owner_id "
        "WHERE m.status = 'committed' AND o.name = coalesce(?, o.name) GROUP BY o.name",
        (owner,),
    ).fetchall()
    return {str(name): month_end(date.fromisoformat(month)) for name, month in rows}


def _accounts(conn: sqlite3.Connection, owners: Iterable[str]) -> dict[int, _Account]:
    wanted = set(owners)
    rows = conn.execute(
        "SELECT a.id, o.name, a.name, a.kind, a.currency, a.source FROM accounts a "
        "JOIN owners o ON o.id = a.owner_id ORDER BY a.id"
    ).fetchall()
    return {
        int(r[0]): _Account(int(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in rows if r[1] in wanted
    }


def _value(conn: sqlite3.Connection, owner: str | None) -> _Valued | None:
    """Every series of `owner`, or of everyone, up to the last day all of them have committed."""
    query.check_names(conn, query.Filter(owner=owner))
    last_days = _last_days(conn, owner)
    if not last_days:
        return None
    last = min(last_days.values())
    accounts = _accounts(conn, last_days)
    flows = _flows(conn, accounts, last)
    anchors = _anchors(conn, accounts, last)
    held = [
        holding
        for holding in holdings.held(conn, committed=True)
        if holding.account_id in accounts and holding.steps and holding.steps[0].day <= last
    ]
    starts = [min(booked) for booked in flows.values()]
    starts += [min(known) for known in anchors.values()]
    starts += [holding.steps[0].day for holding in held]
    if not starts:
        return None
    days = _days(min(starts), last)
    notes: list[str] = []
    series = _cash(accounts, flows, anchors, days, notes)
    series += _securities(conn, accounts, held, days, notes)
    return _Valued(days, tuple(series), tuple(notes))


# ---------------------------------------------------------------------------------- cash


@dataclass(frozen=True, slots=True)
class _Flow:
    nano: int = 0  # what the account's own statements booked that day
    aside: int = 0  # what went into the money set aside inside the account that day


def _flows(
    conn: sqlite3.Connection, accounts: Mapping[int, _Account], last: date
) -> dict[int, dict[date, _Flow]]:
    """account -> day -> what was booked, for committed months, oldest day first.

    Rows that another source books on an account (the interest of Revolut's savings, D8) are
    not in its statement balance: they add to what is set aside there, and so does every
    move the account's own statement calls internal.
    """
    rows = conn.execute(
        "SELECT t.account_id, t.booking_date, t.amount_nano, t.kind, "
        "f.source IS NULL OR f.source IS a.source AS own "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "JOIN months m ON m.owner_id = a.owner_id AND m.status = 'committed' "
        "AND m.month = date(t.booking_date, 'start of month') "
        "LEFT JOIN statement_files f ON f.id = t.statement_file_id "
        "WHERE t.booking_date <= ? ORDER BY t.booking_date",
        (last.isoformat(),),
    ).fetchall()
    fed_by_another = {int(r[0]) for r in rows if not r[4]}
    flows: dict[int, dict[date, _Flow]] = defaultdict(dict)
    for account, day_text, nano, kind, own in rows:
        if account not in accounts:
            continue
        day = date.fromisoformat(day_text)
        flow = flows[account].get(day, _Flow())
        if not own:
            flow = _Flow(flow.nano, flow.aside + nano)
        elif kind == "internal" and account in fed_by_another:
            flow = _Flow(flow.nano + nano, flow.aside - nano)
        else:
            flow = _Flow(flow.nano + nano, flow.aside)
        flows[account][day] = flow
    return flows


def _anchors(
    conn: sqlite3.Connection, accounts: Mapping[int, _Account], last: date
) -> dict[int, dict[date, int]]:
    """account -> day -> the balance a statement gives, or that was typed, oldest first.

    A statement counts once its month is committed: the month of the balance it closes
    on, or the month it starts in for the balance it opens with. A pension certificate is
    a statement of the one day it is dated.
    """
    rows = conn.execute(
        "SELECT b.account_id, b.date, b.amount_nano FROM balances b "
        "JOIN accounts a ON a.id = b.account_id "
        "LEFT JOIN statement_files f ON f.id = b.statement_file_id "
        "WHERE b.date <= ? AND (f.id IS NULL OR EXISTS ("
        "SELECT 1 FROM months m WHERE m.owner_id = a.owner_id AND m.status = 'committed' "
        "AND m.month = date(max(b.date, f.period_start), 'start of month'))) ORDER BY b.date",
        (last.isoformat(),),
    ).fetchall()
    anchors: dict[int, dict[date, int]] = defaultdict(dict)
    for account, day, nano in rows:
        if account in accounts:
            anchors[account][date.fromisoformat(day)] = int(nano)
    return anchors


def _cash(
    accounts: Mapping[int, _Account],
    flows: Mapping[int, Mapping[date, _Flow]],
    anchors: Mapping[int, Mapping[date, int]],
    days: Sequence[date],
    notes: list[str],
) -> list[_Series]:
    series: list[_Series] = []
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
                f"state, the last one for {max(known)}."
            )
        asset = CASH
        if account.kind == "card":
            asset = CARD
        elif account.source == _PENSION_SOURCE:
            asset = PENSION
        elif account.kind == _CARRIED_FLAT and account.source is not None:
            asset = PILLAR2
        series.append(_Series(account.owner, account.id, asset, account.currency, values))
    return series


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


def _securities(
    conn: sqlite3.Connection,
    accounts: Mapping[int, _Account],
    held: Sequence[holdings.Holding],
    days: Sequence[date],
    notes: list[str],
) -> list[_Series]:
    instruments = {
        int(r[0]): (str(r[1]), str(r[2]), str(r[3]), r[4] and date.fromisoformat(r[4]))
        for r in conn.execute("SELECT id, symbol, type, currency, maturity FROM instruments")
    }
    closes = _closes(conn)
    lots = _bill_trades(conn)
    unpriced: set[str] = set()
    series: list[_Series] = []
    for holding in held:
        account = accounts[holding.account_id]
        symbol, kind, currency, maturity = instruments[holding.instrument_id]
        if kind in {"bill", "bond"}:
            trades = lots.get((holding.account_id, holding.instrument_id), [])
            values = _bill_values(trades, maturity, days)
            if not trades and any(step.quantity for step in holding.steps):
                notes.append(f"{symbol} was bought before the history starts: it is not counted.")
        else:
            points = closes.get(holding.instrument_id, [])
            values = _share_values(holding, [(day, close) for day, _, close in points], days)
            if any(values) and all(rank == _TRADE_PRICE for _, rank, _ in points):
                unpriced.add(symbol)
            if not points and any(step.quantity for step in holding.steps):
                notes.append(f"{symbol} has no price at all: it is not counted.")
        if not any(values):
            continue
        asset = PENSION if account.source == _PENSION_SOURCE else _BY_TYPE[kind]
        series.append(_Series(account.owner, account.id, asset, currency, values))
    if unpriced:
        notes.append(
            f"No close is stored for {', '.join(sorted(unpriced))}: valued at the price of the "
            "last trade. Run `finanze prices`."
        )
    return series


def _closes(conn: sqlite3.Connection) -> dict[int, list[tuple[date, int, Decimal]]]:
    """instrument -> (day, rank, close), one per day, oldest first: the best close of each day.

    The price a trade was made at stands in on days without any close.
    """
    best: dict[int, dict[date, tuple[int, Decimal]]] = defaultdict(dict)
    rows = conn.execute(
        "SELECT instrument_id, date, close, CASE source WHEN 'manual' THEN 0 "
        "WHEN 'statement' THEN 1 ELSE 2 END FROM prices "
        "UNION ALL SELECT tr.instrument_id, t.booking_date, tr.price, 3 FROM trades tr "
        "JOIN transactions t ON t.id = tr.transaction_id WHERE tr.price IS NOT NULL"
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


def _carried(points: Sequence[tuple[date, Decimal]], days: Sequence[date]) -> list[Decimal]:
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
        # is counted before, even where the history begins with shares already held.
        quantity = steps[index - 1][1] if index else Decimal(0)
        values.append(quantity * price)
    return values


def _bill_trades(conn: sqlite3.Connection) -> dict[tuple[int, int], list[tuple[date, Event]]]:
    """(account, bill) -> its committed purchases and disposals, in the order lots close."""
    rows = conn.execute(
        "SELECT t.account_id, tr.instrument_id, t.booking_date, t.id, tr.quantity, tr.cost_nano "
        "FROM trades tr JOIN transactions t ON t.id = tr.transaction_id "
        "JOIN accounts a ON a.id = t.account_id JOIN instruments i ON i.id = tr.instrument_id "
        "JOIN months m ON m.owner_id = a.owner_id AND m.status = 'committed' "
        "AND m.month = date(t.booking_date, 'start of month') "
        "WHERE i.type IN ('bill', 'bond') "
        "ORDER BY t.booking_date, tr.quantity LIKE '-%', t.id"
    )
    trades: dict[tuple[int, int], list[tuple[date, Event]]] = defaultdict(list)
    for account, instrument, day, txn_id, quantity, cost in rows:
        event = Event(str(txn_id), Decimal(quantity), int(cost))
        trades[account, instrument].append((date.fromisoformat(day), event))
    return trades


def _bill_values(
    trades: Sequence[tuple[date, Event]], maturity: date | None, days: Sequence[date]
) -> list[Decimal]:
    """What the lots still held are worth each day: from cost towards face, in a straight line."""
    bought_on = {event.txn_id: day for day, event in trades}
    values: list[Decimal] = []
    done = 0
    lots: list[tuple[str, Decimal, int]] = []
    for day in days:
        seen = done
        while done < len(trades) and trades[done][0] <= day:
            done += 1
        if done != seen:
            lots = open_lots(event for _, event in trades[:done])
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


def _rates(conn: sqlite3.Connection, days: Sequence[date]) -> dict[str, list[Decimal]]:
    """currency -> francs per unit on each day: the last rate on or before it."""
    best: dict[str, dict[date, tuple[int, Decimal]]] = defaultdict(dict)
    rows = conn.execute(
        "SELECT currency, date, rate_to_chf, CASE source WHEN 'manual' THEN 0 "
        "WHEN 'statement' THEN 1 ELSE 2 END FROM fx_rates"
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


def _converted(
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
            msg = f"no exchange rate is stored for {needed}: run `finanze prices`"
            raise MissingRateError(msg)
    return [
        value * rate / target
        for value, rate, target in zip(values, rates[currency], rates[into], strict=True)
    ]


# -------------------------------------------------------------------------------- results


def run(
    conn: sqlite3.Connection, *, owner: str | None = None, currency: str = DEFAULT_CURRENCY
) -> NetWorth:
    """Net worth of `owner`, or of everyone, on every day, in `currency`.

    For several people it ends on the last day all of them have committed.
    """
    valued = _value(conn, owner)
    if valued is None:
        return NetWorth(currency, (), (), ())
    rates = _rates(conn, valued.days)
    sums: dict[tuple[str, str], list[list[Decimal]]] = defaultdict(list)
    for series in valued.series:
        converted = _converted(series.values, series.currency, currency, rates)
        sums[series.owner, series.asset].append(converted)
    lines = tuple(
        Line(owner_name, asset, tuple(value.quantize(_CENT) for value in _added(parts)))
        for (owner_name, asset), parts in sorted(
            sums.items(), key=lambda item: (item[0][0], ASSETS.index(item[0][1]))
        )
    )
    return NetWorth(currency, valued.days, lines, valued.notes)


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
    valued = _value(conn, owner)
    if valued is None:
        return ()
    rates = _rates(conn, valued.days)
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
                    _converted(s.values[at : at + 1], s.currency, currency, that_day)[0]
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
