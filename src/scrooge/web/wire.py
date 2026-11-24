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

from scrooge import (
    attention,
    holds,
    importing,
    months,
    networth,
    overview,
    paidby,
    payouts,
    performance,
    portfolio,
    query,
    recap,
    recurring,
    report,
    savings,
)
from scrooge.context import Context

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


def person(shown: query.Person, covered: date | None = None) -> JSON:
    """`covered`: the day up to which every monthly account of theirs is committed; None when
    not known, or when none of their accounts reports monthly."""
    return {
        "name": shown.name,
        "committed": shown.committed,
        "first": day(shown.first),
        "last": day(shown.last),
        "staged": shown.staged,
        "transactions": shown.transactions,
        "covered": day(covered),
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
    `networth.STACK`, "liquidity" frozen first, which moves least, "owner" by name, "total"
    none). `arrived` is what came into view on the days anything did
    (`networth.NetWorth.arrivals`), by the index of the day."""
    parts = worth.by(by) if by != "total" else {}
    came = worth.by(by, arrivals=True) if by != "total" else {}
    if by == "asset":
        names: Sequence[str] = [name for name in networth.STACK if name in parts]
    elif by == "liquidity":
        names = [name for name in reversed(networth.LIQUIDITY) if name in parts]
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
        "work": number(shown.work),
        "rate_salary": _percent(shown.rate_salary),
        "rate_salary_year": _percent(shown.rate_salary_year),
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


def account_holds(held: holds.Holds | None) -> JSON | None:
    """What an account holds by part, beside its balance (`statements_account`): its cash in
    each currency, as it is and converted, its securities together, and its balance at the last
    month-ends. None when its balance is not known."""
    if held is None:
        return None
    return {
        "cash": [
            {
                "asset": c.asset,
                "currency": c.currency,
                "amount": number(c.amount),
                "converted": number(c.converted),
            }
            for c in held.cash
        ],
        "securities": number(held.securities),
        "months": [{"day": day(m.day), "balance": number(m.balance)} for m in held.months],
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


def _pending(row: months.Pending) -> JSON:
    return {
        "id": row.id,
        "date": day(row.date),
        "account": row.account,
        "amount": number(row.amount),
        "currency": row.currency,
        "counterparty": row.counterparty,
        "description": row.description,
        "category": row.category or None,
        "side": row.side or None,
        # Whether a category can be given here: it has none yet, or one a rule or a choice gave.
        "choosable": row.waiting or row.changeable,
        "statement": row.statement,
    }


def draft(staged: months.Draft) -> JSON:
    """What is staged for one person: its statements, every row with the category it has,
    the waiting rows by counterparty (`groups`), what they add up to, and what stands in the
    way of committing them (`gaps`, `waiting`)."""
    return {
        "files": [
            {
                "id": file.id,
                "label": file.label,
                "filename": file.filename,
                "first": day(file.first),
                "last": day(file.last),
                "checked": file.checked,
                "transactions": file.transactions,
            }
            for file in staged.files
        ],
        "rows": [_pending(row) for row in staged.rows],
        "groups": [
            {"name": group.name, "incoming": group.incoming, "ids": [r.id for r in group.rows]}
            for group in staged.groups
        ],
        "totals": [
            {
                "side": line.side or None,
                "category": line.category or None,
                "currency": line.currency,
                "count": line.count,
                "total": number(line.total),
            }
            for line in staged.totals
        ],
        # What stands in the way of committing: only another statement settles a gap, and a
        # category the rows that wait for one.
        "gaps": list(staged.gaps),
        "waiting": len(staged.uncategorized),
    }


def imported(done: importing.Read) -> JSON:
    """What became of each file of an import, and what the import found on the way."""
    staged = done.staged
    return {
        "files": [
            {
                "name": file.name,
                "label": file.label,
                "problem": file.problem or None,
                "staged": file.staged,
                "nothing_new": file.nothing_new,
            }
            for file in done.files
        ],
        "certificates": [
            {
                "account": certificate.account,
                "filename": certificate.filename,
                "balance": number(certificate.stated.balance),
                "currency": certificate.stated.currency,
                "as_of": day(certificate.stated.as_of),
                "adds_up": certificate.adds_up,
            }
            for certificate in done.certificates
        ],
        "unreconciled": list(done.month.unreconciled),
        "already": staged.already if staged else 0,
        "differences": list(staged.differences) if staged else [],
        "later": staged.later if staged else 0,
    }


def _totals(totals: savings.Totals | None) -> JSON | None:
    if totals is None:
        return None
    return {
        "income": number(totals.income),
        "spent": number(totals.spent),
        "saved": number(totals.saved),
        "rate": _percent(totals.rate),
        "from_work": _percent(totals.from_work),
        "work": number(totals.work),
        "rate_salary": _percent(totals.rate_salary),
    }


def cashflow_report(shown: report.Report) -> JSON:
    """The cash flow of a range: what was saved, where the money went and where it came from,
    period by period and in all, converted into one currency; and income and expenses in each
    currency as they were paid."""
    spent, earned, kept, flow = shown.spending, shown.income, shown.savings, shown.cashflow
    # The same periods, by name: one of them may leave out those it has nothing for.
    spent_in = {period.name: period.parts for period in spent.periods}
    earned_in = {period.name: period.groups for period in earned.periods}
    return {
        "currency": spent.currency,
        "totals": _totals(kept.totals),
        "year_before": _totals(kept.year_before),
        "periods": [
            {
                "name": period.name,
                "first": day(period.first),
                "last": day(period.last),
                **(_totals(period.totals) or {}),
                "rate_12": _percent(period.rate),
                "rate_salary_12": _percent(period.rate_salary),
                "spent_by": {
                    name: number(value) for name, value in spent_in.get(period.name, {}).items()
                },
                "earned_by": {
                    name: number(value) for name, value in earned_in.get(period.name, {}).items()
                },
            }
            for period in kept.periods
        ],
        "parts": list(spent.parts),
        "months": spent.months,
        "paid_back": number(spent.paid_back),
        "categories": [
            {
                "name": category.name,
                "count": category.count,
                "spent": number(category.spent),
                "share": _percent(category.share),
                "monthly": number(category.monthly),
                "usual": number(category.usual),
                "year_before": number(category.year_before),
                "leaves": [
                    {"name": leaf.name, "count": leaf.count, "spent": number(leaf.spent)}
                    for leaf in category.leaves
                ],
            }
            for category in spent.categories
        ],
        "merchants": [
            {
                "name": merchant.name,
                "category": merchant.category,
                "count": merchant.count,
                "spent": number(merchant.spent),
                "first": day(merchant.first),
                "last": day(merchant.last),
            }
            for merchant in spent.merchants
        ],
        "other_merchants": {"count": spent.other_merchants, "spent": number(spent.other_spent)},
        "sources": [
            {"group": s.group, "name": s.leaf, "count": s.count, "total": number(s.total)}
            for s in earned.sources
        ],
        "as_paid": [
            {
                "period": balance.period,
                "currency": balance.currency,
                "income": number(balance.income),
                "expenses": number(balance.expenses),
                "investing": number(balance.investing),
            }
            for balance in flow.balances
        ],
        "as_paid_totals": [
            {
                "currency": line.currency,
                "side": line.side,
                "category": line.category,
                "count": line.count,
                "total": number(line.total),
            }
            for line in flow.totals
        ],
        "through": day(kept.through),
        "paid_by": _paid_by(shown.paid_by),
    }


def _paid_by(shown: paidby.PaidBy | None) -> JSON | None:
    """Who paid what the household spent: each person, before and after settling up, each
    category split between them, and the net of what they paid each other."""
    if shown is None:
        return None
    return {
        "spent": number(shown.spent),
        "people": [
            {
                "name": payer.name,
                "spent": number(payer.spent),
                "share": _percent(payer.share),
                "settled": number(payer.settled),
                "settled_share": _percent(payer.settled_share),
            }
            for payer in shown.people
        ],
        "categories": [
            {
                "name": split.name,
                "spent": number(split.spent),
                "by": {name: number(amount) for name, amount in split.by.items()},
            }
            for split in shown.categories
        ],
        "moves": [
            {"sender": move.sender, "receiver": move.receiver, "amount": number(move.amount)}
            for move in shown.moves
        ],
    }


def _paid(paid: payouts.Paid) -> JSON:
    return {
        "dividends": number(paid.dividends),
        "interest": number(paid.interest),
        "tax": number(paid.tax),
    }


def payouts_report(shown: payouts.Payouts) -> JSON:
    """What investments paid over a range, in one currency: in all and against the same
    months a year before, month by month, and holding by holding; and over the twelve months
    up to the last day committed, whatever the range. Tax withheld is negative."""
    return {
        "currency": shown.currency,
        "first": day(shown.first),
        "last": day(shown.last),
        "through": day(shown.through),
        "totals": {**_paid(shown.totals), "net": number(shown.totals.net)},
        "year_before": None
        if shown.year_before is None
        else {**_paid(shown.year_before), "net": number(shown.year_before.net)},
        "trailing": {**_paid(shown.trailing), "net": number(shown.trailing.net)},
        "months": [{"month": month.name, **_paid(month.paid)} for month in shown.months],
        "holdings": [
            {
                "key": holding.key,
                "name": holding.name,
                "symbol": holding.symbol,
                "account": holding.account,
                "owner": holding.owner,
                **_paid(holding.paid),
                "count": holding.count,
                "last": day(holding.last),
            }
            for holding in shown.holdings
        ],
    }


def month_end(end: networth.MonthEnd) -> JSON:
    """Net worth at a month's end, by currency: its total, how it moved since the month
    before (`networth.by_month`), and what of it is liquid and what frozen."""
    return {
        "month": f"{end.month:%Y-%m}",
        "day": day(end.day),
        "partial": end.partial,
        "values": {
            currency: {
                "total": number(figure.total),
                "change": number(figure.change),
                "percent": number(figure.percent),
                "liquid": number(figure.liquid),
                "frozen": number(figure.frozen),
            }
            for currency, figure in end.values.items()
        },
    }


def recurring_payment(shown: recurring.Payment) -> JSON:
    found = shown.found
    return {
        "id": found.id,
        "name": found.name,
        "owner": found.owner,
        "account": found.account,
        "every": found.every,
        "amount": number(-found.latest.amount),
        "currency": found.latest.currency,
        "previous": number(None if found.previous is None else -found.previous),
        "count": len(found.rows),
        "first": day(found.first),
        "last": day(found.last),
        "next": day(found.next),
        "monthly": number(shown.monthly),
        "alone": shown.alone,
    }


def recurring_payments(shown: recurring.Recurring) -> JSON:
    """The recurring payments, as amounts paid (positive), and what the active ones cost a
    month."""
    return {
        "currency": shown.currency,
        "through": day(shown.through),
        "monthly": number(shown.monthly),
        "active": [recurring_payment(each) for each in shown.active],
        "ended": [recurring_payment(each) for each in shown.ended],
        "errors": [sentence(exc) for exc in shown.errors],
    }


def transactions_context(shown: Context, balance: Decimal | None) -> JSON:
    """`shown` as `/api/transactions/context` sends it; `balance` is an account's, now."""
    return {
        "kind": shown.kind,
        "name": shown.name,
        "currency": shown.currency,
        "earned": shown.earned,
        "months": [
            {"month": _month(month.first), "amount": number(month.amount)} for month in shown.months
        ],
        "usual": number(shown.usual),
        "this_month": number(shown.this_month),
        "through": day(shown.through),
        "partial": shown.partial,
        "merchants": None
        if shown.merchants is None
        else [
            {"name": merchant.name, "amount": number(merchant.amount), "count": merchant.count}
            for merchant in shown.merchants
        ],
        "balance": number(balance),
    }


def needs_a_look(
    currency: str,
    unmatched: str,
    unsorted: Sequence[attention.Uncategorised],
    changed: attention.Changes | None,
) -> JSON:
    """The rows still in the category `unmatched` (null: there is none), per person, and the
    recurring payments that went up or are new, what changed in `currency` (as paid,
    positive; null when a rate is missing); `recurring` is null when they were not asked for."""
    return {
        "currency": currency,
        "unmatched": unmatched or None,
        "uncategorised": [{"owner": each.owner, "count": each.count} for each in unsorted],
        "recurring": None
        if changed is None
        else [
            {
                "id": each.found.id,
                "name": each.found.name,
                "account": each.found.account,
                "owner": each.found.owner,
                "every": each.found.every,
                "kind": each.kind,
                "change": number(each.change),
                "since": day(each.since),
                "alone": each.alone,
            }
            for each in changed.changes
        ],
    }


def _measured(measured: performance.Measured) -> JSON:
    return {
        "start": number(measured.start),
        "end": number(measured.end),
        "net_flows": number(measured.net_flows),
        "gain": number(measured.gain),
        "twr": number(measured.twr),
        "mwr": number(measured.mwr),
        "mwr_a_year": measured.a_year,
        "twr_since": day(measured.since),
    }


def performance_report(shown: performance.Performance) -> JSON:
    """What the investment accounts returned over a range, in one currency: in all and account
    by account, and day by day (week by week over a long range) what they were worth against
    the money put in. `twr` is in percent over the range, from `twr_since` when that is later
    than `first`; `mwr` in percent a year when `mwr_a_year`, over the range otherwise."""
    return {
        "currency": shown.currency,
        "range": shown.range,
        "first": day(shown.first),
        "last": day(shown.last),
        "totals": None if shown.totals is None else _measured(shown.totals),
        "series": [
            {
                "day": point.day.isoformat(),
                "value": number(point.value),
                "invested": number(point.invested),
            }
            for point in shown.points
        ],
        "accounts": [
            {
                "owner": holder.owner,
                "name": holder.name,
                "accounts": list(holder.accounts),
                **_measured(holder.measured),
            }
            for holder in shown.holders
        ],
    }
