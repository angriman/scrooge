"""The web app: pages over the database, for this machine only (D18).

Handlers stay thin: read the query string, call a service, render. Every request opens the
database read-only. Nothing is loaded from another site, and a header tells the browser so.
"""

from __future__ import annotations

import csv
import io
import secrets
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from finanze import cashflow, monthclose, months, query
from finanze.models import Kind
from finanze.storage import db
from finanze.validation import ReconciliationError
from finanze.web import forms

HOST = "127.0.0.1"  # the only address the app is ever served on
_HOSTS = [HOST, "localhost"]
_HERE = Path(__file__).parent
_HEADERS = {
    "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}
_PAGE = 200  # transactions shown at once
_CSV = (
    "date",
    "booking_date",
    "owner",
    "account",
    "amount",
    "currency",
    "kind",
    "category",
    "category_parent",
    "counterparty",
    "description",
    "counterparty_owner",
    "id",
)
_templates = Jinja2Templates(directory=_HERE / "templates")


def _money(amount: Decimal) -> str:
    """`-1,234.50`: two decimals to read; the stored amount is not changed."""
    return f"{amount:,.2f}"


_templates.env.filters["money"] = _money
_templates.env.globals["days"] = cashflow.days


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


def _utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(database: Path, clock: Callable[[], datetime] = _utc_now) -> Starlette:
    """The app over `database`, whose schema is already up to date."""
    app = Starlette(
        routes=[
            Route("/", _overview, name="overview"),
            Route("/cashflow", _cashflow, name="cashflow"),
            Route("/months", _months, name="months"),
            Route("/months/{owner}/{month}", _month, name="month"),
            Route("/months/{owner}/{month}/commit", _commit, methods=["POST"], name="commit"),
            Route("/months/{owner}/{month}/reopen", _reopen, methods=["POST"], name="reopen"),
            Route("/transactions", _transactions, name="transactions"),
            Route("/transactions.csv", _transactions_csv, name="transactions_csv"),
            Mount("/static", StaticFiles(directory=_HERE / "static"), name="static"),
        ],
        middleware=[
            # A page of another site cannot reach the app under a name of its own.
            Middleware(TrustedHostMiddleware, allowed_hosts=_HOSTS),
            Middleware(_OwnAssetsOnly),
        ],
        exception_handlers={
            query.QueryError: _unknown_name,
            monthclose.NothingStagedError: _unknown_name,
        },
    )
    app.state.database = database
    app.state.clock = clock
    # Sent back by every form of these pages: a page of another site cannot read it, so it
    # cannot make the browser change anything here.
    app.state.token = secrets.token_urlsafe(32)
    return app


def _overview(request: Request) -> Response:
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
        owner = _owner(request)
        query.check_names(conn, query.Filter(owner=owner))
    shown = [person for person in people if owner in (None, person.name)]
    context = {"people": people, "shown": shown, "owner": owner}
    return _templates.TemplateResponse(request, "overview.html", context)


def _months(request: Request) -> Response:
    owner = _owner(request)
    with db.reading(request.app.state.database) as conn:
        query.check_names(conn, query.Filter(owner=owner))
        lines = months.listing(conn, owner)
        context = {"people": query.people(conn), "owner": owner, "lines": lines}
    return _templates.TemplateResponse(request, "months.html", context)


def _which(request: Request) -> tuple[str, date]:
    """The person and the month a month page is about."""
    try:
        month = date.fromisoformat(f"{request.path_params['month']}-01")
    except ValueError:
        raise HTTPException(404) from None
    return request.path_params["owner"], month


def _month(request: Request) -> Response:
    return _month_page(request, *_which(request))


def _month_page(
    request: Request, owner: str, month: date, error: str = "", status: int = 200
) -> Response:
    with db.reading(request.app.state.database) as conn:
        context = {
            "people": query.people(conn),
            "owner": owner,
            "month": months.checklist(conn, owner, month),
            "error": error,
            "token": request.app.state.token,
        }
    return _templates.TemplateResponse(request, "month.html", context, status_code=status)


async def _sent(request: Request) -> dict[str, str]:
    """The fields of a form of these pages; anything else is refused."""
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).hostname not in _HOSTS:
        raise HTTPException(403)
    body = (await request.body()).decode()
    fields = {name: values[-1] for name, values in parse_qs(body, keep_blank_values=True).items()}
    if not secrets.compare_digest(fields.get("token", ""), request.app.state.token):
        raise HTTPException(403)
    return fields


async def _commit(request: Request) -> Response:
    await _sent(request)
    owner, month = _which(request)
    state = request.app.state

    def commit() -> None:
        conn = db.connect(state.database)
        try:
            now = state.clock()
            monthclose.commit_with_copy(conn, state.database, owner=owner, month=month, now=now)
        finally:
            conn.close()

    try:
        await run_in_threadpool(commit)
    except (monthclose.UncategorizedRowsError, ReconciliationError) as exc:
        # Nothing was committed, and nothing here can override it: the page says why.
        return await run_in_threadpool(_month_page, request, owner, month, str(exc), 409)
    return RedirectResponse(request.url_for("month", owner=owner, month=f"{month:%Y-%m}"), 303)


async def _reopen(request: Request) -> Response:
    await _sent(request)
    owner, month = _which(request)

    def reopen() -> None:
        conn = db.connect(request.app.state.database)
        try:
            monthclose.reopen(conn, owner=owner, month=month)
        finally:
            conn.close()

    await run_in_threadpool(reopen)
    return RedirectResponse(request.url_for("month", owner=owner, month=f"{month:%Y-%m}"), 303)


def _cashflow(request: Request) -> Response:
    asked = forms.cashflow_range(request.query_params)
    with db.reading(request.app.state.database) as conn:
        result = cashflow.run(
            conn, first=asked.first, last=asked.last, owner=asked.owner, grouping=asked.grouping
        )
        people = query.people(conn)
    context = {
        "people": people,
        "owner": asked.owner,
        "asked": asked,
        "given": request.query_params,
        "groupings": list(cashflow.Grouping),
        "result": result,
        "sides": (
            (cashflow.INCOME, "Income"),
            (cashflow.EXPENSES, "Expenses"),
            (cashflow.INVESTING, "Investing"),
        ),
    }
    return _templates.TemplateResponse(request, "cashflow.html", context)


def _transactions(request: Request) -> Response:
    parsed = forms.transactions_filter(request.query_params)
    wanted, errors = parsed.wanted, list(parsed.errors)
    with db.reading(request.app.state.database) as conn:
        query.check_names(conn, query.Filter(owner=wanted.owner))
        try:
            result = query.run(conn, wanted)
        except query.QueryError as exc:  # an account or category the form no longer offers
            message = str(exc)
            errors.append(f"{message[0].upper()}{message[1:]}.")
            result = query.Result((), (), (), ())
        people = query.people(conn)
        choices = query.choices(conn, wanted.owner)
    newest_first = result.rows[::-1]
    pages = max(1, -(-len(newest_first) // _PAGE))
    asked = request.query_params.get("page", "")
    page = min(int(asked), pages) if asked.isdecimal() and int(asked) else 1
    context = {
        "people": people,
        "owner": wanted.owner,
        "wanted": wanted,
        "given": request.query_params,
        "errors": errors,
        "choices": choices,
        "kinds": list(Kind),
        "result": result,
        "rows": newest_first[(page - 1) * _PAGE : page * _PAGE],
        "page": page,
        "pages": pages,
    }
    return _templates.TemplateResponse(request, "transactions.html", context)


def _transactions_csv(request: Request) -> Response:
    """Every row the same address shows as a page, with its exact amount."""
    parsed = forms.transactions_filter(request.query_params)
    if parsed.errors:
        return PlainTextResponse("\n".join(parsed.errors) + "\n", status_code=400)
    with db.reading(request.app.state.database) as conn:
        rows = query.run(conn, parsed.wanted).rows
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(_CSV)
    writer.writerows([getattr(row, column) for column in _CSV] for row in rows)
    headers = {"content-disposition": 'attachment; filename="transactions.csv"'}
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)


def _owner(request: Request) -> str | None:
    """Whose view this is: `?owner=Name`, or everyone's without it."""
    return request.query_params.get("owner") or None


def _unknown_name(_: Request, exc: Exception) -> Response:
    return PlainTextResponse(f"{exc}\n", status_code=404)
