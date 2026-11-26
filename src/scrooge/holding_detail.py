"""Value history and latest committed transactions for one holding: the holding side panel.

All figures are read-only over committed statements. The daily value series reuses `networth.value`
(including split-adjusted quantities and straight-line bill valuation) converted into the requested
display currency; if an FX rate is missing, `series_error` says why while the holding's committed
transactions are still returned.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from scrooge import categories, networth, payouts, query
from scrooge.models import Kind
from scrooge.storage import store
from scrooge.storage.money import from_nano

DEFAULT_LIMIT = 8
MAX_LIMIT = 50
MAX_POINTS = 180

_CENT = Decimal("0.01")
_ASSET_BY_TYPE: dict[str, str] = {
    "etf": networth.ETFS,
    "stock": networth.STOCKS,
    "bill": networth.BILLS,
    "bond": networth.BILLS,
    "fund": networth.PENSION,
}
_PAYOUT_CATEGORIES = (categories.DIVIDENDS, categories.INTEREST, categories.WITHHOLDING_TAX)
_INSTRUMENT_KINDS = (
    Kind.DIVIDEND.value,
    Kind.INTEREST.value,
    Kind.TAX.value,
    Kind.COMMISSION.value,
)


@dataclass(frozen=True, slots=True)
class Point:
    """What the holding was worth on one day, in the requested currency."""

    day: date
    value: Decimal


@dataclass(frozen=True, slots=True)
class Detail:
    """One holding's value history and latest committed transactions."""

    symbol: str
    currency: str
    series: tuple[Point, ...]  # oldest first, from first non-zero day, downsampled
    transactions: tuple[query.Row, ...]  # newest first, up to `limit`
    total_transactions: int
    series_error: str | None = None  # set when a missing FX rate prevented series conversion


def run(  # noqa: PLR0913  # holding identity, owner scope, display currency and row cap
    conn: sqlite3.Connection,
    *,
    symbol: str,
    instrument_currency: str | None = None,
    asset: str | None = None,
    owner: str | None = None,
    currency: str = networth.DEFAULT_CURRENCY,
    limit: int = DEFAULT_LIMIT,
) -> Detail | None:
    """Return value history and latest committed transactions for `symbol`, or None if unknown."""
    query.check_names(conn, query.Filter(owner=owner))
    matched_instruments = _instruments(conn, symbol, instrument_currency, asset)
    if not matched_instruments:
        return None
    instrument_ids = [item[0] for item in matched_instruments]
    instrument_currencies = {item[1] for item in matched_instruments}

    valued = networth.value(conn, owner)
    accounts = store.account_names(conn)
    matched_series = _matching_series(valued, set(instrument_ids), instrument_currency, asset)
    series_accounts = {(s.owner, accounts[s.account_id]) for s in matched_series}

    rows = _transactions(
        conn,
        symbol=symbol,
        instrument_ids=instrument_ids,
        instrument_currencies=instrument_currencies,
        series_accounts=series_accounts,
        owner=owner,
    )
    if not matched_series and not rows:
        return None

    points, series_error = _series(conn, valued, matched_series, currency)
    capped = max(1, min(limit, MAX_LIMIT))
    return Detail(
        symbol=symbol,
        currency=currency,
        series=points,
        transactions=rows[:capped],
        total_transactions=len(rows),
        series_error=series_error,
    )


def _instruments(
    conn: sqlite3.Connection,
    symbol: str,
    instrument_currency: str | None,
    asset: str | None,
) -> list[tuple[int, str]]:
    """Matching `(instrument_id, currency)` pairs for `symbol`, currency, and asset."""
    rows = conn.execute(
        "SELECT id, currency, type FROM instruments WHERE symbol = ? ORDER BY id",
        (symbol,),
    ).fetchall()
    return [
        (int(row_id), str(row_currency))
        for row_id, row_currency, row_type in rows
        if (instrument_currency is None or str(row_currency) == instrument_currency)
        and (asset is None or _ASSET_BY_TYPE.get(str(row_type)) == asset)
    ]


def _matching_series(
    valued: networth.Valued | None,
    instrument_ids: set[int],
    instrument_currency: str | None,
    asset: str | None,
) -> list[networth.Series]:
    if valued is None:
        return []
    return [
        s
        for s in valued.series
        if s.instrument_id in instrument_ids
        and (instrument_currency is None or s.currency == instrument_currency)
        and (s.asset == asset if asset is not None else s.asset != networth.PENSION)
        and any(v != 0 for v in s.values)
    ]


def _series(
    conn: sqlite3.Connection,
    valued: networth.Valued | None,
    matched: Sequence[networth.Series],
    currency: str,
) -> tuple[tuple[Point, ...], str | None]:
    """Convert `matched` series into `currency` from the first non-zero day and downsample."""
    if valued is None or not matched:
        return (), None
    first = next(
        (i for i in range(len(valued.days)) if any(s.values[i] != 0 for s in matched)),
        None,
    )
    if first is None:
        return (), None
    days = valued.days[first:]
    try:
        rates = networth.rates_on(conn, days)
        totals = [Decimal(0)] * len(days)
        for s in matched:
            converted = networth.convert(s.values[first:], s.currency, currency, rates)
            for idx, value in enumerate(converted):
                totals[idx] += value
    except networth.MissingRateError as exc:
        return (), str(exc)
    points = [Point(d, v.quantize(_CENT)) for d, v in zip(days, totals, strict=True)]
    return _downsample(points, MAX_POINTS), None


def _downsample(points: Sequence[Point], max_points: int) -> tuple[Point, ...]:
    """Keep at most `max_points` evenly spaced points, preserving the first and last days."""
    count = len(points)
    if count <= max_points:
        return tuple(points)
    step = (count - 1) / (max_points - 1)
    chosen: list[Point] = []
    seen: set[int] = set()
    for i in range(max_points):
        idx = min(count - 1, round(i * step))
        if idx not in seen:
            seen.add(idx)
            chosen.append(points[idx])
    return tuple(chosen)


def _row_from(r: sqlite3.Row) -> query.Row:
    return query.Row(
        id=r["id"],
        owner=r["owner"],
        account=r["account"],
        date=date.fromisoformat(r["date"]),
        booking_date=date.fromisoformat(r["booking_date"]),
        amount=from_nano(r["amount_nano"]),
        currency=r["currency"],
        description=r["description"],
        counterparty=r["counterparty"],
        kind=Kind(r["kind"]),
        category=r["category"],
        category_parent=r["category_parent"],
        counterparty_owner=r["counterparty_owner"],
        file=r["file"],
    )


def _transactions(  # noqa: PLR0913  # holding identity, accounts, and owner scope
    conn: sqlite3.Connection,
    *,
    symbol: str,
    instrument_ids: Sequence[int],
    instrument_currencies: set[str],
    series_accounts: set[tuple[str, str]],
    owner: str | None,
) -> tuple[query.Row, ...]:
    """Committed trades, split bill-interest rows, and matching payouts/fees, newest first."""
    id_marks = store.marks(len(instrument_ids))
    direct_rows = conn.execute(
        "SELECT ct.id, ct.owner, ct.account, ct.date, ct.booking_date, ct.amount_nano, "  # noqa: S608  # only "?" marks from store.marks are interpolated
        "ct.currency, ct.description, ct.counterparty, ct.kind, ct.category, "
        "ct.category_parent, ct.counterparty_owner, t.statement_file_id AS file "
        "FROM committed_transactions ct "
        "JOIN transactions t ON t.id = ct.id "
        "WHERE ct.owner = coalesce(?, ct.owner) "
        "AND (t.id IN "
        f"(SELECT tr.transaction_id FROM trades tr WHERE tr.instrument_id IN ({id_marks})) "
        "OR t.split_of IN "
        f"(SELECT tr.transaction_id FROM trades tr WHERE tr.instrument_id IN ({id_marks})))",
        (owner, *instrument_ids, *instrument_ids),
    ).fetchall()

    by_id: dict[str, query.Row] = {str(r["id"]): _row_from(r) for r in direct_rows}
    accounts = set(series_accounts) | {(row.owner, row.account) for row in by_id.values()}
    if accounts and instrument_currencies:
        named = payouts.named_instruments(conn)
        target = symbol.upper()
        cat_marks = store.marks(len(_PAYOUT_CATEGORIES))
        kind_marks = store.marks(len(_INSTRUMENT_KINDS))
        curr_marks = store.marks(len(instrument_currencies))
        candidates = conn.execute(
            "SELECT ct.id, ct.owner, ct.account, ct.date, ct.booking_date, ct.amount_nano, "  # noqa: S608  # only "?" marks from store.marks are interpolated
            "ct.currency, ct.description, ct.counterparty, ct.kind, ct.category, "
            "ct.category_parent, ct.counterparty_owner, t.statement_file_id AS file "
            "FROM committed_transactions ct "
            "JOIN transactions t ON t.id = ct.id "
            "WHERE ct.owner = coalesce(?, ct.owner) "
            f"AND ct.currency IN ({curr_marks}) "
            f"AND (ct.category IN ({cat_marks}) OR ct.kind IN ({kind_marks}))",
            (owner, *sorted(instrument_currencies), *_PAYOUT_CATEGORIES, *_INSTRUMENT_KINDS),
        ).fetchall()
        for r in candidates:
            row_id = str(r["id"])
            if row_id in by_id or (str(r["owner"]), str(r["account"])) not in accounts:
                continue
            if payouts.holding_of(str(r["counterparty"] or ""), named) == target:
                by_id[row_id] = _row_from(r)

    return tuple(
        sorted(by_id.values(), key=lambda row: (row.date, row.booking_date, row.id), reverse=True)
    )
