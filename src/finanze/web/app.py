"""The web app: pages over the database, for this machine only (D18).

Handlers stay thin: read the query string, call a service, render. Every request opens the
database read-only. Nothing is loaded from another site, and a header tells the browser so.
"""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from finanze import query
from finanze.storage import db

HOST = "127.0.0.1"  # the only address the app is ever served on
_HOSTS = [HOST, "localhost"]
_HERE = Path(__file__).parent
_HEADERS = {
    "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}
_templates = Jinja2Templates(directory=_HERE / "templates")


class _OwnAssetsOnly:
    """Adds the headers that keep a page from loading or sending anything elsewhere."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def add_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] += [(k.encode(), v.encode()) for k, v in _HEADERS.items()]
            await send(message)

        await self.app(scope, receive, add_headers)


def create_app(database: Path) -> Starlette:
    """The app over `database`, whose schema is already up to date."""
    app = Starlette(
        routes=[
            Route("/", _overview, name="overview"),
            Mount("/static", StaticFiles(directory=_HERE / "static"), name="static"),
        ],
        middleware=[
            # A page of another site cannot reach the app under a name of its own.
            Middleware(TrustedHostMiddleware, allowed_hosts=_HOSTS),
            Middleware(_OwnAssetsOnly),
        ],
        exception_handlers={query.QueryError: _unknown_name},
    )
    app.state.database = database
    return app


def _overview(request: Request) -> Response:
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
        owner = _owner(request)
        query.check_names(conn, query.Filter(owner=owner))
    shown = [person for person in people if owner in (None, person.name)]
    context = {"people": people, "shown": shown, "owner": owner}
    return _templates.TemplateResponse(request, "overview.html", context)


def _owner(request: Request) -> str | None:
    """Whose view this is: `?owner=Name`, or everyone's without it."""
    return request.query_params.get("owner") or None


def _unknown_name(_: Request, exc: Exception) -> Response:
    return PlainTextResponse(f"{exc}\n", status_code=404)
