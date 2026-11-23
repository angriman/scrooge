"""The JSON of the pages, for a frontend of its own (`/app`): the same services as the pages
of `web/app.py`, and what they return as `web/wire.py` writes it.

Like the pages, every route opens the database read-only, and nothing here writes yet. A
query field that cannot be read is a 400 with `errors`, one sentence each; a figure that
cannot be worked out (a rate that is missing) leaves it `null` and says why in `errors`, with
everything else the route shows.

The figures are in the currency the address names, else in the config's: the cookie of the
pages' header is theirs, and a frontend of its own keeps its own choice.
"""

from __future__ import annotations

import secrets
import sqlite3
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from scrooge import months, networth, overview, portfolio, query, recap, staging
from scrooge.models import Kind
from scrooge.storage import db, originals, store
from scrooge.web import forms, wire

_DIGEST = 12  # how much of a logo's digest its address carries, to change when the image does


def logos(request: Request, conn: sqlite3.Connection) -> dict[str, str]:
    """Symbol -> the address of its logo, which changes with the image."""
    return {
        symbol: str(request.url_for("logo", symbol=symbol).include_query_params(v=digest[:_DIGEST]))
        for symbol, digest in store.logo_digests(conn).items()
    }


def _asked(request: Request) -> dict[str, str]:
    params = dict(request.query_params)
    params.setdefault("currency", request.app.state.currency)
    return params


def _refused(errors: tuple[str, ...]) -> Response:
    return JSONResponse({"errors": list(errors)}, 400)


def _session(request: Request) -> Response:
    """What every screen needs: who there is, what the figures can be shown in, and what this
    app lets a browser do. `token` goes with every change sent, once there are any."""
    state = request.app.state
    with db.reading(state.database) as conn:
        people = query.people(conn)
    return JSONResponse(
        {
            "people": [wire.person(person) for person in people],
            "currencies": list(networth.CURRENCIES),
            "currency": state.currency,
            "read_only": state.read_only,
            "signing": state.access is not None,
            # Whether a category chosen can be remembered as a rule for the next import.
            "rules": state.rules is not None,
            "token": state.token,
        }
    )


def _overview(request: Request) -> Response:
    """The first page's figures for `owner` (everyone without it) in `currency`."""
    asked = forms.networth_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    errors: list[str] = []
    with db.reading(request.app.state.database) as conn:
        shown = wire.unless_rate_missing(
            errors,
            lambda: overview.run(
                conn, owner=asked.owner, currency=asked.currency, top=overview.TOP
            ),
            None,
        )
        checks = wire.unless_rate_missing(
            errors, lambda: networth.checks(conn, owner=asked.owner), ()
        )
        found = logos(request, conn)
    return JSONResponse(
        {
            "overview": None if shown is None else wire.summary(shown),
            "checks": [wire.check(check) for check in checks],
            "logos": found,
            "errors": errors,
        }
    )


def _networth(request: Request) -> Response:
    """Net worth day by day for `owner` in `currency`, cut by `by` (total, asset or owner)."""
    asked = forms.networth_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    errors: list[str] = []
    with db.reading(request.app.state.database) as conn:
        worth = wire.unless_rate_missing(
            errors, lambda: networth.run(conn, owner=asked.owner, currency=asked.currency), None
        )
    series = None if worth is None else wire.series(worth, asked.by)
    return JSONResponse({"series": series, "errors": errors})


def _month(request: Request) -> Response:
    """One month for `owner` in `currency`: `month` (`2026-10`), else the last one every
    account's statements cover. `month` is null when nothing is committed."""
    asked = forms.month_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    with db.reading(request.app.state.database) as conn:
        try:
            shown = recap.run(conn, owner=asked.owner, currency=asked.currency, month=asked.month)
        except networth.MissingRateError as exc:
            return JSONResponse({"month": None, "errors": [wire.sentence(exc)]})
    return JSONResponse({"month": None if shown is None else wire.month(shown), "errors": []})


def _attention(request: Request) -> Response:
    """What waits for `owner`, or for everyone: statements staged and not committed, accounts
    whose statements end well before the person's others, and lines of statements whose
    figures differ from those worked out here."""
    owner = request.query_params.get("owner") or None
    errors: list[str] = []
    with db.reading(request.app.state.database) as conn:
        people = [p for p in query.people(conn) if owner in (None, p.name)]
        lines = months.coverage(conn, owner)
        try:
            checks = networth.checks(conn, owner=owner)
        except networth.MissingRateError as exc:
            errors.append(wire.sentence(exc))
            checks = ()
    return JSONResponse(
        {
            "staged": [{"owner": p.name, "count": p.staged} for p in people if p.staged],
            "behind": [
                wire.account(line)
                for line, late in zip(lines, months.behind(lines), strict=True)
                if late
            ],
            "differ": sum(1 for check in checks if check.difference),
            "errors": errors,
        }
    )


def _holdings(request: Request) -> Response:
    """What `owner`, or everyone, holds, in `currency`: `holdings` is null when nothing is
    committed, or a rate is missing (`errors` says which)."""
    asked = forms.holdings_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    today = request.app.state.clock().date()
    with db.reading(request.app.state.database) as conn:
        try:
            held = portfolio.run(conn, owner=asked.owner, currency=asked.currency, today=today)
        except networth.MissingRateError as exc:
            return JSONResponse({"holdings": None, "logos": {}, "errors": [wire.sentence(exc)]})
        found = logos(request, conn)
    shown = wire.holdings(held) if held.day else None
    return JSONResponse({"holdings": shown, "logos": found, "errors": []})


def _statements(request: Request) -> Response:
    """Every account a source feeds, of `owner` or of everyone: how far its statements go, what
    it holds now in `currency`, the runs of days they cover and the statements themselves; the
    day every monthly account is covered to; and the statements' own balances against those
    worked out here."""
    asked = forms.holdings_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    state = request.app.state
    folder = originals.folder_of(state.database)
    errors: list[str] = []
    with db.reading(state.database) as conn:
        query.check_names(conn, query.Filter(owner=asked.owner))
        lines = months.coverage(conn, asked.owner)
        tracks = {
            (t.owner, t.account, t.source): t.spans for t in months.timeline(conn, asked.owner)
        }
        files = months.by_account(conn, asked.owner)
        try:
            balances = networth.balances(conn, networth.value(conn, asked.owner), asked.currency)
            checks = networth.checks(conn, owner=asked.owner)
        except networth.MissingRateError as exc:
            errors.append(wire.sentence(exc))
            balances, checks = {}, ()

    def listed(statement: months.Statement) -> wire.JSON:
        kept = originals.find(folder, statement.sha256, statement.filename)
        address = str(request.url_for("original", number=statement.id)) if kept else None
        return wire.statement(statement, address)

    accounts = [
        wire.statements_account(
            line,
            late,
            balances.get((line.owner, line.account)),
            tracks.get((line.owner, line.account, line.source), ()),
            [
                listed(s)
                for s in files.get((line.owner, line.account), ())
                if s.source == line.source
            ],
        )
        for line, late in zip(lines, months.behind(lines), strict=True)
    ]
    return JSONResponse(
        {
            "currency": asked.currency,
            "covered": wire.day(months.covered(lines)),
            "accounts": accounts,
            "checks": [wire.check(check) for check in checks],
            "errors": errors,
        }
    )


_PAGE = 100  # rows sent at once, unless fewer or more (up to `_MOST`) are asked for
_MOST = 500


def _count(params: dict[str, str], name: str, default: int, most: int) -> int:
    text = params.get(name, "")
    return min(int(text), most) if text.isdecimal() and len(text) <= 6 else default


def _transactions(request: Request) -> Response:
    """The committed rows that match the filter of the transactions page, newest first,
    `limit` of them from `offset`; what all that match add up to, in all and by category; how
    often each comes back; and what the filters can be set to."""
    params = dict(request.query_params)
    parsed = forms.transactions_filter(params)
    if parsed.errors:
        return _refused(parsed.errors)
    wanted = parsed.wanted
    with db.reading(request.app.state.database) as conn:
        try:
            result = query.run(conn, wanted)
        except query.QueryError as exc:
            return _refused((wire.sentence(exc),))
        every = query.recurring(query.run(conn, query.Filter(owner=wanted.owner)).rows)
        found = query.choices(conn, wanted.owner)
    offset = _count(params, "offset", 0, len(result.rows))
    limit = _count(params, "limit", _PAGE, _MOST)
    newest = result.rows[::-1][offset : offset + limit]
    return JSONResponse(
        {
            "count": len(result.rows),
            "offset": offset,
            "totals": [
                {"currency": s.currency, "count": s.count, "total": wire.number(s.total)}
                for s in result.totals
            ],
            "by_category": [
                {
                    "name": c.name,
                    "currency": c.currency,
                    "count": c.count,
                    "total": wire.number(c.total),
                }
                for c in query.by_category(result.rows)
            ],
            "rows": [wire.transaction(row, every.get(row.id)) for row in newest],
            "choices": wire.choices(found),
            "kinds": [kind.value for kind in Kind],
            "errors": [],
        }
    )


def _writable(request: Request, token: str) -> None:
    """Refuse a change sent from another site, without the token of these pages, or to a copy
    that is only shown here."""
    state = request.app.state
    origin = request.headers.get("origin", "null")
    if origin != "null" and urlsplit(origin).hostname not in state.hosts:
        raise HTTPException(403)
    if not secrets.compare_digest(token.encode(), state.token.encode()):
        raise HTTPException(403)
    if state.read_only:
        raise HTTPException(403)


async def _categorize(request: Request) -> Response:
    """Give committed rows of one person another category, `{"owner", "ids", "category",
    "remember"}`: remembered as a rule for the next imports when asked and when rules are
    kept."""
    _writable(request, request.headers.get("x-token", ""))
    try:
        sent = await request.json()
    except ValueError:
        sent = None
    if not isinstance(sent, dict) or not isinstance(sent.get("ids"), list):
        return _refused(("Send the owner, the ids of the rows and the category as JSON.",))
    state = request.app.state
    owner, category = str(sent.get("owner", "")), str(sent.get("category", ""))
    ids = [str(row) for row in sent.get("ids", [])]
    remember = sent.get("remember") is True and state.rules is not None

    def change() -> int:
        conn = db.connect(state.database)
        try:
            return staging.categorize(
                conn,
                owner=owner,
                ids=ids,
                category=category,
                now=state.clock(),
                remember=state.rules.of(owner) if remember else None,
                change=True,
            )
        finally:
            conn.close()

    try:
        changed = await run_in_threadpool(change)
    except (staging.StagingError, store.UnknownCategoryError) as exc:
        return JSONResponse({"changed": 0, "errors": [wire.sentence(exc)]}, 409)
    return JSONResponse({"changed": changed, "errors": []})


routes = [
    Route("/session", _session, name="api_session"),
    Route("/overview", _overview, name="api_overview"),
    Route("/networth", _networth, name="api_networth"),
    Route("/month", _month, name="api_month"),
    Route("/attention", _attention, name="api_attention"),
    Route("/holdings", _holdings, name="api_holdings"),
    Route("/statements", _statements, name="api_statements"),
    Route("/transactions", _transactions, name="api_transactions"),
    Route("/transactions/category", _categorize, methods=["POST"], name="api_categorize"),
]
