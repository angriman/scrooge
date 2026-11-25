"""Query strings into what the services take. Nothing here touches the database."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from scrooge import context, networth, performance, query
from scrooge.cashflow import Grouping
from scrooge.models import Kind
from scrooge.storage import money


@dataclass(frozen=True, slots=True)
class Parsed:
    wanted: query.Filter
    errors: tuple[str, ...]  # one sentence per field that could not be read; it is then unset


@dataclass(frozen=True, slots=True)
class CashFlowRange:
    first: date | None
    last: date | None
    owner: str | None
    grouping: Grouping
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalysisView:
    first: date | None
    last: date | None
    owner: str | None
    grouping: Grouping
    currency: str
    errors: tuple[str, ...]


BREAKDOWNS = ("total", "asset", "owner", "liquidity")  # how the chart of net worth cuts its line
PAGE_BREAKDOWNS = BREAKDOWNS[:3]  # those the first page of web/app.py offers; /app has them all
DEFAULT_BREAKDOWN = "asset"  # the parts by asset type unless asked otherwise


@dataclass(frozen=True, slots=True)
class NetWorthView:
    owner: str | None
    currency: str
    by: str  # one of BREAKDOWNS
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HoldingsView:
    owner: str | None
    currency: str
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MonthView:
    owner: str | None
    currency: str
    month: date | None  # its first day; None: the last one every account covers
    errors: tuple[str, ...]


def _text(params: Mapping[str, str], name: str) -> str | None:
    return params.get(name, "").strip() or None


def _day(params: Mapping[str, str], name: str, end: int, errors: list[str]) -> date | None:
    """The first (`end` 0) or last (`end` 1) day of what the field names."""
    value = _text(params, name)
    try:
        return None if value is None else query.span(value)[end]
    except ValueError:
        errors.append(f"'{value}' is not a day, a month or a year like 2025-05-17 or 2025-05.")
        return None


def cashflow_range(params: Mapping[str, str]) -> CashFlowRange:
    """`from`, `to`, `owner` and `by` (week, month, quarter or year; month when left out)."""
    errors: list[str] = []
    by = _text(params, "by")
    if by is not None and by not in set(Grouping):
        errors.append(f"'{by}' is not week, month, quarter or year.")
        by = None
    return CashFlowRange(
        first=_day(params, "from", 0, errors),
        last=_day(params, "to", 1, errors),
        owner=_text(params, "owner"),
        grouping=Grouping.MONTH if by is None else Grouping(by),
        errors=tuple(errors),
    )


def analysis_view(params: Mapping[str, str]) -> AnalysisView:
    """`from`, `to`, `owner` and `by` as for the cash flow, and `currency` (euros when left
    out)."""
    span = cashflow_range(params)
    errors = list(span.errors)
    currency = _currency(params, errors)
    return AnalysisView(span.first, span.last, span.owner, span.grouping, currency, tuple(errors))


def transactions_filter(params: Mapping[str, str]) -> Parsed:
    """`from`, `to`, `booking`, `owner`, `account`, `accounts`, `category`, `kind`, `min`,
    `max`, `text`, `merchant`.

    `from` and `to` are a day, a month or a year, as for `scrooge query`. `accounts` is a kind
    of account, as `query.ACCOUNTS` names them (`broker`). `kind` may name several kinds,
    separated by commas. An empty field is the same as a missing one.
    """
    errors: list[str] = []

    def text(name: str) -> str | None:
        return _text(params, name)

    def amount(name: str) -> Decimal | None:
        value = text(name)
        try:
            number = None if value is None else Decimal(value.replace(",", "").replace("'", ""))
        except InvalidOperation:
            number = Decimal("NaN")
        if number is not None and not (number.is_finite() and money.fits(number)):
            errors.append(f"'{value}' is not an amount like 1234.56.")
            return None
        return None if number is None else number.copy_abs()

    kinds: list[Kind] = []
    for name in filter(None, (part.strip() for part in (text("kind") or "").split(","))):
        if name in set(Kind):
            kinds.append(Kind(name))
        else:
            errors.append(f"'{name}' is not a kind of transaction.")
    group = text("accounts")
    if group is not None and group not in query.ACCOUNTS:
        names = list(query.ACCOUNTS)
        errors.append(f"'{group}' is not {', '.join(names[:-1])} or {names[-1]} accounts.")
        group = None
    wanted = query.Filter(
        first=_day(params, "from", 0, errors),
        last=_day(params, "to", 1, errors),
        booking=text("booking") is not None,
        owner=text("owner"),
        account=text("account"),
        category=text("category"),
        kind=kinds[0] if len(kinds) == 1 else None,
        kinds=tuple(kinds) if len(kinds) > 1 else (),
        at_least=amount("min"),
        at_most=amount("max"),
        text=text("text") or "",
        merchant=text("merchant"),
        account_kinds=query.ACCOUNTS[group] if group else (),
    )
    return Parsed(wanted, tuple(errors))


@dataclass(frozen=True, slots=True)
class ContextView:
    owner: str | None
    currency: str
    kind: str  # one of `context.KINDS`; "" when it cannot be read
    name: str
    errors: tuple[str, ...]


def context_view(params: Mapping[str, str]) -> ContextView:
    """`owner`, `currency` (euros when left out) and exactly one of `category`, `merchant` and
    `account`, which says what the context is of."""
    errors: list[str] = []
    currency = _currency(params, errors)
    given = [(kind, name) for kind in context.KINDS if (name := _text(params, kind))]
    if len(given) != 1:
        errors.append("Ask for exactly one of category, merchant and account.")
    kind, name = given[0] if len(given) == 1 else ("", "")
    return ContextView(_text(params, "owner"), currency, kind, name, tuple(errors))


def _currency(params: Mapping[str, str], errors: list[str]) -> str:
    """The currency a page is asked to show its figures in: euros when left out or unreadable."""
    currency = (_text(params, "currency") or networth.DEFAULT_CURRENCY).upper()
    if currency not in networth.CURRENCIES:
        errors.append(f"'{currency}' is not one of {', '.join(networth.CURRENCIES)}.")
        return networth.DEFAULT_CURRENCY
    return currency


def currency(params: Mapping[str, str]) -> str:
    """The currency the figures are shown in: the one asked for, or euros when it cannot be
    read (the page that shows figures says why)."""
    return _currency(params, [])


def holdings_view(params: Mapping[str, str]) -> HoldingsView:
    """`owner` and `currency` (euros when left out)."""
    errors: list[str] = []
    currency = _currency(params, errors)
    return HoldingsView(_text(params, "owner"), currency, tuple(errors))


def networth_view(
    params: Mapping[str, str], breakdowns: Sequence[str] = BREAKDOWNS
) -> NetWorthView:
    """`owner`, `currency` (euros when left out) and `by`: one of `breakdowns` (those of /app
    unless a page offers fewer), asset when left out or unreadable."""
    errors: list[str] = []
    currency = _currency(params, errors)
    by = _text(params, "by") or DEFAULT_BREAKDOWN
    if by not in breakdowns:
        errors.append(f"'{by}' is not {', '.join(breakdowns[:-1])} or {breakdowns[-1]}.")
        by = DEFAULT_BREAKDOWN
    return NetWorthView(_text(params, "owner"), currency, by, tuple(errors))


def month_view(params: Mapping[str, str]) -> MonthView:
    """`owner`, `currency` (euros when left out) and `month` (`2026-10`; the last complete
    one when left out; an unreadable one is an error)."""
    errors: list[str] = []
    currency = _currency(params, errors)
    asked = _text(params, "month")
    month = None
    if asked:
        try:
            month = date.fromisoformat(f"{asked}-01")
        except ValueError:
            errors.append(f"'{asked}' is not a month written as 2026-10.")
    return MonthView(_text(params, "owner"), currency, month, tuple(errors))


@dataclass(frozen=True, slots=True)
class PerformanceView:
    owner: str | None
    currency: str
    range: str  # one of `performance.RANGES`
    errors: tuple[str, ...]


def performance_view(params: Mapping[str, str]) -> PerformanceView:
    """`owner`, `currency` (euros when left out) and `range` (1m, 3m, ytd, 1y, 3y or all, as
    the overview offers them; the year to date when left out)."""
    errors: list[str] = []
    currency = _currency(params, errors)
    asked = (_text(params, "range") or performance.DEFAULT_RANGE).lower()
    if asked not in performance.RANGES:
        errors.append(f"'{asked}' is not one of {', '.join(performance.RANGES)}.")
        asked = performance.DEFAULT_RANGE
    return PerformanceView(_text(params, "owner"), currency, asked, tuple(errors))
