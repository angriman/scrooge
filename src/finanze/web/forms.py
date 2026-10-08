"""Query strings into what the services take. Nothing here touches the database."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from finanze import networth, query
from finanze.cashflow import Grouping
from finanze.models import Kind


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


BREAKDOWNS = ("total", "asset", "owner")  # how the net-worth page cuts its one line


@dataclass(frozen=True, slots=True)
class NetWorthView:
    owner: str | None
    currency: str
    by: str  # one of BREAKDOWNS
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


def transactions_filter(params: Mapping[str, str]) -> Parsed:
    """`from`, `to`, `booking`, `owner`, `account`, `category`, `kind`, `min`, `max`, `text`.

    `from` and `to` are a day, a month or a year, as for `finanze query`. An empty field is
    the same as a missing one.
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
        if number is not None and not number.is_finite():
            errors.append(f"'{value}' is not an amount like 1234.56.")
            return None
        return None if number is None else abs(number)

    kind = text("kind")
    if kind is not None and kind not in set(Kind):
        errors.append(f"'{kind}' is not a kind of transaction.")
        kind = None
    wanted = query.Filter(
        first=_day(params, "from", 0, errors),
        last=_day(params, "to", 1, errors),
        booking=text("booking") is not None,
        owner=text("owner"),
        account=text("account"),
        category=text("category"),
        kind=None if kind is None else Kind(kind),
        at_least=amount("min"),
        at_most=amount("max"),
        text=text("text") or "",
    )
    return Parsed(wanted, tuple(errors))


def networth_view(params: Mapping[str, str]) -> NetWorthView:
    """`owner`, `currency` (euros when left out) and `by` (total, asset or owner)."""
    errors: list[str] = []
    currency = (_text(params, "currency") or networth.DEFAULT_CURRENCY).upper()
    if currency not in networth.CURRENCIES:
        errors.append(f"'{currency}' is not one of {', '.join(networth.CURRENCIES)}.")
        currency = networth.DEFAULT_CURRENCY
    by = _text(params, "by") or BREAKDOWNS[0]
    if by not in BREAKDOWNS:
        errors.append(f"'{by}' is not total, asset or owner.")
        by = BREAKDOWNS[0]
    return NetWorthView(_text(params, "owner"), currency, by, tuple(errors))
