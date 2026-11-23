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

from scrooge import months, networth, overview, query, recap
from scrooge.storage import db, store
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


routes = [
    Route("/session", _session, name="api_session"),
    Route("/overview", _overview, name="api_overview"),
    Route("/networth", _networth, name="api_networth"),
    Route("/month", _month, name="api_month"),
    Route("/attention", _attention, name="api_attention"),
]
