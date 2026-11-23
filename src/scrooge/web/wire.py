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

from scrooge import months, networth, overview, portfolio, query, recap

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


def _position(position: portfolio.Position) -> JSON:
    return {
        "owner": position.owner,
        "account": position.account,
        "quantity": number(position.quantity),
        "price": number(position.price),
        "price_day": day(position.price_day),
        "price_is_trade": position.price_is_trade,
        "value_native": number(position.value_native),
        "value": number(position.value),
        "cost_native": number(position.cost_native),
        "gain_native": number(position.gain_native),
        "gain_percent": number(position.gain_percent),
    }


def holdings(held: portfolio.Portfolio) -> JSON:
    """What is held on `day`: the totals, each instrument with its positions account by
    account, how it is spread (`portfolio.SPREADS`), and the bills by maturity."""
    positions: dict[tuple[str, str, str], list[portfolio.Position]] = {}
    for position in held.positions:
        key = (position.symbol, position.currency, position.asset)
        positions.setdefault(key, []).append(position)
    return {
        "currency": held.currency,
        "day": day(held.day),
        "through": day(held.through),
        "total": number(held.total),
        "cost": number(held.cost),
        "gain": number(held.gain),
        "return_percent": number(held.return_percent),
        "unknown_cost": held.unknown_cost,
        "accounts": len({(p.owner, p.account) for p in held.positions}),
        "holdings": [
            {
                "symbol": h.symbol,
                "name": h.name,
                "asset": h.asset,
                "currency": h.currency,
                "value": number(h.value),
                "share": number(h.share),
                "cost": number(h.cost),
                "gain": number(h.gain),
                "return_percent": number(h.return_percent),
                "positions": [
                    _position(p)
                    for p in sorted(
                        positions[h.symbol, h.currency, h.asset],
                        key=lambda p: (-p.value, p.owner, p.account),
                    )
                ],
            }
            for h in held.holdings
        ],
        "spread": {
            by: [
                {"name": s.name, "value": number(s.value), "share": number(s.share)}
                for s in held.spread(by)
            ]
            for by in portfolio.SPREADS
        },
        "ladder_day": day(held.ladder_day),
        "ladder": [
            {
                "symbol": rung.position.symbol,
                "name": rung.position.name,
                "owner": rung.position.owner,
                "account": rung.position.account,
                "currency": rung.position.currency,
                "maturity": day(rung.position.maturity),
                "days": rung.days,
                "face": number(rung.position.quantity),
                "value_native": number(rung.value_native),
                "value": number(rung.value),
                "interest": number(rung.interest),
            }
            for rung in held.ladder
        ],
        "pension_cash": number(held.pension_cash),
        "notes": list(held.notes),
    }


def statement(file: months.Statement, original: str | None) -> JSON:
    """One statement file; `original` is the address its file can be downloaded from, when it
    is kept."""
    return {
        "id": file.id,
        "filename": file.filename,
        "label": file.label,
        "first": day(file.first),
        "last": day(file.last),
        "checked": file.checked,
        "transactions": file.transactions,
        "committed": file.committed_at is not None,
        "imported": file.imported_at[:10],
        "original": original,
    }


def statements_account(
    line: months.Coverage,
    late: bool,
    balance: Decimal | None,
    spans: Sequence[months.Span],
    files: Sequence[JSON],
) -> JSON:
    """An account a source feeds: how far it is committed and staged, whether it is behind, what
    it holds now, the runs of days its statements cover, and the statements themselves."""
    return {
        **account(line),
        "source": line.source,
        "staged": day(line.staged),
        "behind": late,
        "monthly": months.monthly(line),
        "balance": number(balance),
        "spans": [
            {"first": day(span.first), "last": day(span.last), "committed": span.committed}
            for span in spans
        ],
        "statements": list(files),
    }


_CHANGEABLE = frozenset({"income", "expense", "transfer"})  # `store.CHANGEABLE`, for a row


def transaction(row: query.Row, every: str | None) -> JSON:
    """One committed row; `every` says how often it comes back ("monthly", "yearly"), when it
    does; `changeable` whether its category can be changed."""
    return {
        "id": row.id,
        "owner": row.owner,
        "account": row.account,
        "date": day(row.date),
        "booking_date": day(row.booking_date),
        "amount": number(row.amount),
        "currency": row.currency,
        "description": row.description,
        "counterparty": row.counterparty,
        "kind": row.kind.value,
        "category": row.category,
        "category_parent": row.category_parent,
        "file": row.file,
        "recurring": every,
        "changeable": row.category is not None and row.kind.value in _CHANGEABLE,
    }


def _headed(groups: Sequence[tuple[str, tuple[str, ...]]]) -> list[JSON]:
    return [{"heading": heading, "names": list(names)} for heading, names in groups]


def choices(found: query.Choices) -> JSON:
    """What the filters can be set to, and the categories a row can be given."""
    return {
        "accounts": list(found.accounts),
        "categories": _headed(found.categories),
        "leaves": _headed(found.leaves),
    }
