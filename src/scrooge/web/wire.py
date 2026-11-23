"""What the services return, as the JSON of `/api` (`web/api.py`). Nothing here reads the
database or knows about HTTP.

An amount, a share or a quantity is text, written as it is (`"1234.50"`, never a float, which
would lose the cents); a day is ISO text (`"2026-09-30"`); what is not known is `null`.
Nothing is laid out here: no colours, no shapes, no rounding for the eye. Those are the
frontend's.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from typing import TypeVar

from scrooge import months, networth, overview, query, recap

JSON = dict[str, object]
_T = TypeVar("_T")
_F = TypeVar("_F")


def number(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def sentence(exc: Exception) -> str:
    """What went wrong, as a page says it."""
    message = str(exc)
    return f"{message[0].upper()}{message[1:]}."


def unless_rate_missing(errors: list[str], compute: Callable[[], _T], fallback: _F) -> _T | _F:
    """What `compute` returns; when a currency has no stored rate, `fallback`, and the reason
    is added to `errors`."""
    try:
        return compute()
    except networth.MissingRateError as exc:
        errors.append(sentence(exc))
        return fallback


def person(shown: query.Person) -> JSON:
    return {
        "name": shown.name,
        "committed": shown.committed,
        "first": day(shown.first),
        "last": day(shown.last),
        "staged": shown.staged,
        "transactions": shown.transactions,
    }


def _part(part: overview.Part) -> JSON:
    return {
        "name": part.name,
        "value": number(part.value),
        "share": number(part.share),
        "then": [number(value) for value in part.then],
    }


def _holding(holding: overview.Holding) -> JSON:
    return {
        "symbol": holding.symbol,
        "name": holding.name,
        "asset": holding.asset,
        "value": number(holding.value),
        "share": number(holding.share),
    }


def _change(change: overview.Change) -> JSON:
    return {
        "days": change.days,
        "before": number(change.before),
        "amount": number(change.amount),
        "percent": number(change.percent),
    }


def summary(shown: overview.Overview) -> JSON:
    """The first page's figures; the days behind them are `series`'."""
    rest = shown.rest
    return {
        "currency": shown.currency,
        "day": day(shown.day),
        "through": day(shown.through),
        "total": number(shown.total),
        "changes": [_change(change) for change in shown.changes],
        "assets": [_part(part) for part in shown.assets],
        "held": [_part(part) for part in shown.held],
        "people": [_part(part) for part in shown.people],
        "top": [_holding(holding) for holding in shown.top],
        "rest": None
        if rest is None
        else {"count": rest.count, "value": number(rest.value), "share": number(rest.share)},
        "securities": _part(shown.securities),
    }


def check(found: networth.Check) -> JSON:
    return {
        "owner": found.owner,
        "accounts": found.accounts,
        "day": day(found.day),
        "label": found.label,
        "currency": found.currency,
        "printed": number(found.printed),
        "derived": number(found.derived),
        "difference": number(found.difference),
    }


def _days_of(values: Sequence[Decimal]) -> dict[str, str | None]:
    """The days on which `values` are not zero, by their index from the first day."""
    return {str(index): number(value) for index, value in enumerate(values) if value}


def series(worth: networth.NetWorth, by: str) -> JSON:
    """Net worth day by day from `first`, and its parts by `by` ("asset" in the order of
    `networth.STACK`, "owner" by name, "total" none). `arrived` is what came into view on the
    days anything did (`networth.NetWorth.arrivals`), by the index of the day."""
    parts = worth.by(by) if by != "total" else {}
    came = worth.by(by, arrivals=True) if by != "total" else {}
    if by == "asset":
        names: Sequence[str] = [name for name in networth.STACK if name in parts]
    else:
        names = sorted(parts)
    return {
        "currency": worth.currency,
        "first": day(worth.days[0]) if worth.days else None,
        "total": [number(value) for value in worth.total] if worth.days else [],
        "arrived": _days_of(worth.arrived) if worth.days else {},
        "parts": [
            {
                "name": name,
                "values": [number(value) for value in parts[name]],
                "arrived": _days_of(came.get(name, ())),
            }
            for name in names
        ],
        "notes": list(worth.notes),
    }


def _percent(fraction: Decimal | None) -> str | None:
    """A part of a whole sent as a fraction (`0.372`), in percent (`"37.200"`)."""
    return number(None if fraction is None else fraction * 100)


def _month(first: date | None) -> str | None:
    return None if first is None else f"{first:%Y-%m}"


def account(line: months.Coverage) -> JSON:
    """An account a source feeds, and the last day committed of it."""
    return {
        "owner": line.owner,
        "account": line.account,
        "label": line.label,
        "committed": day(line.committed),
    }


def month(shown: recap.Recap) -> JSON:
    """One month at a glance: how net worth moved over it, what was saved, what was spent by
    category against the usual month; rates in percent."""
    return {
        "currency": shown.currency,
        "month": _month(shown.month),
        "first": day(shown.month),
        "last": day(shown.last),
        "over": shown.over,
        "before": number(shown.before),
        "after": number(shown.after),
        "moved": number(shown.moved),
        "income": number(shown.income),
        "spent": number(shown.spent),
        "arrived": number(shown.arrived),
        "saved": number(shown.saved),
        "other": number(shown.other),
        "rate": _percent(shown.rate),
        "rate_year": _percent(shown.rate_year),
        "categories": [
            {
                "name": c.name,
                "spent": number(c.spent),
                "usual": number(c.usual),
                "against_usual": number(c.against_usual),
            }
            for c in shown.categories
        ],
        "missing": [account(line) for line in shown.missing],
        "earlier": _month(shown.earlier),
        "later": _month(shown.later),
    }
