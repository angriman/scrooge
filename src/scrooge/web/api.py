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

import sqlite3

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from scrooge import months, networth, overview, portfolio, query, recap
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


routes = [
    Route("/session", _session, name="api_session"),
    Route("/overview", _overview, name="api_overview"),
    Route("/networth", _networth, name="api_networth"),
    Route("/month", _month, name="api_month"),
    Route("/attention", _attention, name="api_attention"),
    Route("/holdings", _holdings, name="api_holdings"),
    Route("/statements", _statements, name="api_statements"),
]
