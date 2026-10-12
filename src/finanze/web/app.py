"""The web app: pages over the database, for this machine only unless a password is set
(D18).

Handlers stay thin: read the query string or the form, call a service, render. Every page
opens the database read-only; only the forms of a month's page (commit, reopen, category)
open it to write. Nothing is loaded from another site, and a header tells the browser so.

With a password (`web/auth.py`) every page but the one that asks for it wants a signed-in
browser, and only then may the app be reached under other names than this machine's.

Over a copy of the database (`read_only`) nothing opens it to write: the pages have no forms
but the ones that sign in and out, and what the forms would send is refused.
"""

from __future__ import annotations

import csv
import io
import secrets
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from finanze import (
    cashflow,
    categories,
    classification,
    importing,
    monthclose,
    months,
    networth,
    overview,
    portfolio,
    query,
)
from finanze.classification import UnknownInstrumentError
from finanze.errors import ConfigError, ExtractionError
from finanze.models import Kind
from finanze.storage import db, originals, store
from finanze.storage.money import PrecisionError
from finanze.validation import ReconciliationError
from finanze.web import auth, forms

HOST = "127.0.0.1"  # where the app is served unless `finanze.yaml` names another address
_HOSTS = (HOST, "localhost")
_SESSION = "finanze_session"  # the cookie of a signed-in browser
_STATIC = "/static/"  # with `/login`, all a browser that is not signed in may ask for
_HERE = Path(__file__).parent
_HEADERS = {
    "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}
_COPY = "This is a copy: changes are made on the machine that holds the data."
_PAGE = 200  # transactions shown at once
_UPLOADS = 200  # files taken by one import: a year of monthly statements of every source
# What stops an import: nothing is stored, and the page says why.
_REFUSED = (
    ExtractionError,
    ReconciliationError,
    ConfigError,
    UnknownInstrumentError,
    monthclose.MonthCloseError,
    PrecisionError,
    store.UnknownCategoryError,
    store.AccountMismatchError,
)
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


def _everywhere(request: Request) -> dict[str, object]:
    """What every page needs for the button that signs out, and to say so in place of its
    forms when it shows a copy of the database."""
    state = request.app.state
    copy = _COPY if state.read_only else ""
    return {
        "signing": state.access is not None,
        "token": state.token,
        "copy": copy,
        "importing": state.imports is not None,
    }


_templates = Jinja2Templates(directory=_HERE / "templates", context_processors=[_everywhere])


def _money(amount: Decimal) -> str:
    """`-1,234.50`: two decimals to read; the stored amount is not changed."""
    return f"{amount:,.2f}"


def _signed(amount: Decimal) -> str:
    """`+1,234.50`: a gain or a loss, which its sign tells apart."""
    return f"{amount:+,.2f}"


def _exact(number: Decimal) -> str:
    """`1,234.5`: a quantity or a price as it is stored, never rounded, without the zeros
    that say nothing."""
    written = f"{number:,f}"
    return written.rstrip("0").rstrip(".") if "." in written else written


_templates.env.filters["money"] = _money
_templates.env.filters["signed"] = _signed
_templates.env.filters["exact"] = _exact
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


class _SignedInOnly:
    """Sends a browser that is not signed in to the page that asks for the password."""

    def __init__(
        self, app: ASGIApp, access: auth.Access | None, clock: Callable[[], datetime]
    ) -> None:
        self.app = app
        self.access = access
        self.clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.access is None or scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path = request.url.path
        session = request.cookies.get(_SESSION, "")
        known = self.access.known(session, self.clock())
        if known or path == "/login" or path.startswith(_STATIC):
            await self.app(scope, receive, send)
            return
        response: Response
        if request.method in ("GET", "HEAD"):
            asked = path + (f"?{request.url.query}" if request.url.query else "")
            response = RedirectResponse(f"/login?next={quote(asked, safe='')}", 303)
        else:
            response = PlainTextResponse("sign in first\n", status_code=401)
        await response(scope, receive, send)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Rules:
    """Where the month page saves a remembered choice of category (plan 5.4)."""

    shared: Path  # the rules both people share; each person's own file sits next to it
    people: monthclose.People  # what recognises each person as a counterparty

    def of(self, owner: str) -> monthclose.Remember:
        everyone = [pattern for patterns in self.people.values() for pattern in patterns]
        others = [p for name, patterns in self.people.items() if name != owner for p in patterns]
        own = classification.personal_file(self.shared, owner)
        return monthclose.Remember(self.shared, own, everyone, others)


def create_app(  # noqa: PLR0913  # the database, a clock, and one argument per key of `serve`
    database: Path,
    clock: Callable[[], datetime] = _utc_now,
    rules: Rules | None = None,
    *,
    imports: importing.Setup | None = None,
    names: Sequence[str] = (),
    password: str = "",
    read_only: bool = False,
) -> Starlette:
    """The app over `database`, whose schema is already up to date.

    Without `rules` a category can only be chosen for the rows shown, not remembered.
    `imports` is what an import reads besides the statements: with it the app has the page
    that takes them, unless the database is a copy.
    `password` is the digest `auth.digest` wrote: with it every page wants a signed-in
    browser. `names` are what other devices call this machine; without a password there
    are no other devices, so they are refused (D18). `read_only` is for a copy of the
    database, which may lie where nothing can be written: no page offers to change it, and
    nothing sent does.
    """
    if names and not password:
        msg = "the app is not served under other names without a password"
        raise ValueError(msg)
    access = auth.Access(password) if password else None
    hosts = (*_HOSTS, *names)
    commit, reopen, categorize = (_copy,) * 3 if read_only else (_commit, _reopen, _categorize)
    category = _copy if read_only else _add_category
    app = Starlette(
        routes=[
            Route("/", _overview, name="overview"),
            Route("/networth", _networth, name="networth"),
            Route("/networth.json", _networth_json, name="networth_json"),
            Route("/holdings", _holdings, name="holdings"),
            Route("/cashflow", _cashflow, name="cashflow"),
            Route("/import", _import_page, name="import"),
            Route("/import", _import, methods=["POST"]),
            Route("/months", _months, name="months"),
            Route("/months/{owner}/{month}", _month, name="month"),
            Route("/months/{owner}/{month}/commit", commit, methods=["POST"], name="commit"),
            Route("/months/{owner}/{month}/reopen", reopen, methods=["POST"], name="reopen"),
            Route(
                "/months/{owner}/{month}/categorize",
                categorize,
                methods=["POST"],
                name="categorize",
            ),
            Route("/statements/{number:int}", _statement, name="statement"),
            Route("/statements/{number:int}/original", _original, name="original"),
            Route("/months/{owner}/{month}/category", category, methods=["POST"], name="category"),
            Route("/transactions", _transactions, name="transactions"),
            Route("/transactions.csv", _transactions_csv, name="transactions_csv"),
            Route("/login", _login_page, name="login"),
            Route("/login", _login, methods=["POST"]),
            Route("/logout", _logout, methods=["POST"], name="logout"),
            Mount("/static", StaticFiles(directory=_HERE / "static"), name="static"),
        ],
        middleware=[
            # A page of another site cannot reach the app under a name of its own.
            Middleware(TrustedHostMiddleware, allowed_hosts=list(hosts)),
            Middleware(_OwnAssetsOnly),
            Middleware(_SignedInOnly, access=access, clock=clock),
        ],
        exception_handlers={
            query.QueryError: _unknown_name,
            monthclose.NothingStagedError: _unknown_name,
        },
    )
    app.state.database = database
    app.state.clock = clock
    app.state.rules = rules
    app.state.imports = None if read_only else imports
    app.state.access = access
    app.state.read_only = read_only
    app.state.hosts = hosts
    # Sent back by every form of these pages: a page of another site cannot read it, so it
    # cannot make the browser change anything here.
    app.state.token = secrets.token_urlsafe(32)
    return app


def _overview(request: Request) -> Response:
    asked = forms.holdings_view(request.query_params)  # the same two fields: whose, and in what
    errors = list(asked.errors)
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
        try:
            shown = overview.run(conn, owner=asked.owner, currency=asked.currency, top=overview.TOP)
        except networth.MissingRateError as exc:
            errors.append(_sentence(exc))
            shown = None
    context = {
        "people": people,
        "owner": asked.owner,
        "asked": asked,
        "currencies": networth.CURRENCIES,
        "errors": errors,
        "shown": shown,
    }
    return _templates.TemplateResponse(request, "overview.html", context)


@dataclass(frozen=True, slots=True)
class _Imported:
    """What an import staged, as its page says it."""

    found: importing.Read
    staged: monthclose.Staged


def _import_form(
    request: Request, error: str = "", done: _Imported | None = None, status: int = 200
) -> Response:
    setup = request.app.state.imports
    if setup is None:
        raise HTTPException(404)
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
    context = {
        "people": people,
        "owner": _owner(request),
        "owners": list(setup.people),
        "error": error,
        "done": done,
    }
    return _templates.TemplateResponse(request, "import.html", context, status_code=status)


def _import_page(request: Request) -> Response:
    return _import_form(request)


async def _import(request: Request) -> Response:
    """Stage the statements sent for one person; what does not reconcile stores nothing."""
    _own_origin(request)
    state = request.app.state
    if state.imports is None:
        raise HTTPException(404)
    async with request.form(max_files=_UPLOADS) as form:
        _own_token(request, str(form.get("token", "")))
        owner = str(form.get("owner", ""))
        sent = [part for part in form.getlist("file") if isinstance(part, UploadFile)]
        uploads = [(Path(part.filename).name, await part.read()) for part in sent if part.filename]
    if owner not in state.imports.people:
        error = f"{owner or 'Nobody'} is nobody the config names."
        return await run_in_threadpool(_import_form, request, error, None, 400)
    if not uploads:
        error = "Choose at least one file."
        return await run_in_threadpool(_import_form, request, error, None, 400)

    def stage() -> _Imported:
        with tempfile.TemporaryDirectory() as folder:
            files = _saved(Path(folder), uploads)
            found = importing.read(owner, files, state.imports)
            conn = db.connect(state.database)
            try:
                now = state.clock()
                staged = importing.stage(conn, state.database, found, state.imports, now)
            finally:
                conn.close()
        return _Imported(found, staged)

    try:
        done = await run_in_threadpool(stage)
    except _REFUSED as exc:
        error = f"Nothing was imported: {exc}."
        return await run_in_threadpool(_import_form, request, error, None, 409)
    return await run_in_threadpool(_import_form, request, "", done)


def _saved(folder: Path, uploads: Sequence[tuple[str, bytes]]) -> list[Path]:
    """Write each upload under the name it came with. Files that share a name each go in a
    numbered folder of their own, which is how the import tells them apart."""
    names = [name for name, _ in uploads]
    files: list[Path] = []
    for number, (name, content) in enumerate(uploads, start=1):
        path = folder / str(number) / name if names.count(name) > 1 else folder / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        files.append(path)
    return files


def _months(request: Request) -> Response:
    owner = _owner(request)
    with db.reading(request.app.state.database) as conn:
        query.check_names(conn, query.Filter(owner=owner))
        lines = months.listing(conn, owner)
        people = query.people(conn)
    shown = [person for person in people if owner in (None, person.name)]
    context = {"people": people, "shown": shown, "owner": owner, "lines": lines}
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
            "choices": query.choices(conn, owner),
            "can_remember": request.app.state.rules is not None,
        }
    return _templates.TemplateResponse(request, "month.html", context, status_code=status)


async def _sent(request: Request) -> dict[str, list[str]]:
    """The fields of a form of these pages, each with all its values; anything else is refused."""
    _own_origin(request)
    try:
        body = (await request.body()).decode()
    except UnicodeDecodeError:
        raise HTTPException(400) from None
    fields = parse_qs(body, keep_blank_values=True)
    _own_token(request, fields.get("token", [""])[-1])
    return fields


def _own_origin(request: Request) -> None:
    # These pages ask for no referrer, so the browser says `Origin: null` for their own forms:
    # that says nothing either way, and the token is what decides.
    origin = request.headers.get("origin", "null")
    if origin != "null" and urlsplit(origin).hostname not in request.app.state.hosts:
        raise HTTPException(403)


def _own_token(request: Request, sent: str) -> None:
    # bytes: any text can be compared
    if not secrets.compare_digest(sent.encode(), request.app.state.token.encode()):
        raise HTTPException(403)


async def _copy(request: Request) -> Response:
    """What a copy answers to a form that would change it, in place of the three handlers
    below: 403, as the request is understood and nobody may make it here. Signing in and the
    token are still asked for first, so the answer tells a stranger nothing."""
    await _sent(request)
    return PlainTextResponse(f"{_COPY}\n", status_code=403)


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
        error = f"Not committed: {exc}."
        return await run_in_threadpool(_month_page, request, owner, month, error, 409)
    return RedirectResponse(request.url_for("month", owner=owner, month=f"{month:%Y-%m}"), 303)


async def _categorize(request: Request) -> Response:
    fields = await _sent(request)
    owner, month = _which(request)
    state = request.app.state
    wanted = fields.get("remember", [""])[-1] == "rule" and state.rules is not None

    def categorize() -> None:
        conn = db.connect(state.database)
        try:
            monthclose.categorize(
                conn,
                owner=owner,
                month=month,
                ids=fields.get("id", []),
                category=fields.get("category", [""])[-1],
                now=state.clock(),
                remember=state.rules.of(owner) if wanted else None,
                change=fields.get("change", [""])[-1] == "1",
            )
        finally:
            conn.close()

    try:
        await run_in_threadpool(categorize)
    except (monthclose.MonthCloseError, store.UnknownCategoryError) as exc:
        if isinstance(exc, monthclose.NothingStagedError):
            raise
        error = f"No category was set: {exc}."
        return await run_in_threadpool(_month_page, request, owner, month, error, 409)
    return RedirectResponse(request.url_for("month", owner=owner, month=f"{month:%Y-%m}"), 303)


async def _add_category(request: Request) -> Response:
    """One more category in the taxonomy, from the page of the month that needs it."""
    fields = await _sent(request)
    owner, month = _which(request)
    state = request.app.state
    if state.rules is None:  # nowhere to write it down for the next import
        raise HTTPException(404)
    side, _, group = fields.get("under", [""])[-1].partition(":")

    def add() -> None:
        conn = db.connect(state.database)
        try:
            monthclose.add_category(
                conn,
                state.rules.shared.with_name(categories.FILE),
                name=fields.get("name", [""])[-1],
                side=side,
                group=group,
            )
        finally:
            conn.close()

    try:
        await run_in_threadpool(add)
    except (monthclose.MonthCloseError, ConfigError) as exc:
        error = f"No category was added: {exc}."
        return await run_in_threadpool(_month_page, request, owner, month, error, 409)
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


@dataclass(frozen=True, slots=True)
class _Part:
    """One series of the net-worth chart: the total, or what one asset or person adds to it."""

    name: str
    slot: int  # which colour it wears, the same whatever else is shown; 0 for the total
    values: tuple[Decimal, ...]

    def before(self, days: int) -> Decimal | None:
        """What it was worth `days` before the last day; None when that is before the first."""
        return networth.before(self.values, days)


def _parts(result: networth.NetWorth, by: str, people: tuple[query.Person, ...]) -> list[_Part]:
    """The total first, then its parts in the order the chart stacks them, from the bottom up:
    each keeps its colour whoever is looked at."""
    parts = [_Part("Net worth", 0, result.total)]
    if by == "asset":
        # Neighbours in the stack wear neighbouring colours: those are the pairs the
        # palette was checked for.
        slots = {asset: n for n, asset in enumerate(networth.STACK, 1)}
    elif by == "owner":
        slots = {person.name: n for n, person in enumerate(people, 1)}
    else:
        return parts
    stacked = [_Part(name, slots[name], values) for name, values in result.by(by).items()]
    return parts + sorted(stacked, key=lambda part: part.slot)


def _valued(
    request: Request, asked: forms.NetWorthView, *, checked: bool = True
) -> tuple[networth.NetWorth | None, tuple[query.Person, ...], tuple[networth.Check, ...], str]:
    """Net worth as asked for, with the statements' own figures if `checked`, or the reason
    there is none to show."""
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
        try:
            result = networth.run(conn, owner=asked.owner, currency=asked.currency)
            checks = networth.checks(conn, owner=asked.owner) if checked else ()
        except networth.MissingRateError as exc:
            return None, people, (), _sentence(exc)
        return result, people, checks, ""


def _networth(request: Request) -> Response:
    asked = forms.networth_view(request.query_params)
    result, people, checks, error = _valued(request, asked)
    context = {
        "people": people,
        "owner": asked.owner,
        "asked": asked,
        # The view shown, not the one asked for: a field that could not be read is unset.
        "source": request.url_for("networth_json").include_query_params(
            currency=asked.currency, by=asked.by, **({"owner": asked.owner} if asked.owner else {})
        ),
        "currencies": networth.CURRENCIES,
        "breakdowns": forms.BREAKDOWNS,
        "errors": [*asked.errors, *([error] if error else [])],
        "result": result,
        "parts": _parts(result, asked.by, people) if result and result.days else [],
        "checks": checks,
    }
    return _templates.TemplateResponse(request, "networth.html", context)


def _networth_json(request: Request) -> Response:
    """What the chart of the page at the same address draws. Amounts are text, to the cent."""
    asked = forms.networth_view(request.query_params)
    result, people, _, error = _valued(request, asked, checked=False)
    if asked.errors or result is None:
        return JSONResponse({"errors": [*asked.errors, *([error] if error else [])]}, 400)
    lines = _parts(result, asked.by, people) if result.days else []
    return JSONResponse(
        {
            "currency": result.currency,
            "first": result.days[0].isoformat() if result.days else None,
            "series": [
                {"name": part.name, "slot": part.slot, "values": [str(v) for v in part.values]}
                for part in lines
            ],
        }
    )


def _holdings(request: Request) -> Response:
    asked = forms.holdings_view(request.query_params)
    errors = list(asked.errors)
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
        try:
            held = portfolio.run(conn, owner=asked.owner, currency=asked.currency)
        except networth.MissingRateError as exc:
            errors.append(_sentence(exc))
            held = None
    context = {
        "people": people,
        "owner": asked.owner,
        "asked": asked,
        "currencies": networth.CURRENCIES,
        "errors": errors,
        "held": held,
    }
    return _templates.TemplateResponse(request, "holdings.html", context)


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


def _stored(request: Request) -> tuple[months.Statement, Path | None]:
    """The statement a page is about, and its original when that is kept here."""
    with db.reading(request.app.state.database) as conn:
        statement = months.statement(conn, request.path_params["number"])
    if statement is None:
        raise HTTPException(404)
    folder = originals.folder_of(request.app.state.database)
    return statement, originals.find(folder, statement.sha256, statement.filename)


def _statement(request: Request) -> Response:
    statement, original = _stored(request)
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
    context = {"people": people, "owner": None, "statement": statement, "kept": bool(original)}
    return _templates.TemplateResponse(request, "statement.html", context)


def _original(request: Request) -> Response:
    """The file as it was imported, under the name it came with."""
    statement, original = _stored(request)
    if original is None:
        raise HTTPException(404)
    name = Path(statement.filename).name
    return FileResponse(original, filename=name, media_type="application/octet-stream")


def _transactions(request: Request) -> Response:
    parsed = forms.transactions_filter(request.query_params)
    wanted, errors = parsed.wanted, list(parsed.errors)
    with db.reading(request.app.state.database) as conn:
        query.check_names(conn, query.Filter(owner=wanted.owner))
        try:
            result = query.run(conn, wanted)
        except query.QueryError as exc:  # an account or category the form no longer offers
            errors.append(_sentence(exc))
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


def _next(asked: str) -> str:
    """Where to go once signed in: a page of this app, never an address elsewhere."""
    return asked if asked.startswith("/") and not asked.startswith(("//", "/\\")) else "/"


def _login_form(request: Request, then: str, error: str = "", status: int = 200) -> Response:
    if request.app.state.access is None:
        raise HTTPException(404)
    context = {"then": _next(then), "error": error}
    return _templates.TemplateResponse(request, "login.html", context, status_code=status)


def _login_page(request: Request) -> Response:
    return _login_form(request, request.query_params.get("next", "/"))


async def _login(request: Request) -> Response:
    fields = await _sent(request)
    state = request.app.state
    then = fields.get("next", ["/"])[-1]
    if state.access is None:
        raise HTTPException(404)
    password = fields.get("password", [""])[-1]
    try:
        # Off the event loop: working a digest out takes a moment, on purpose.
        session = await run_in_threadpool(state.access.enter, password, state.clock())
    except auth.TooSoonError as exc:
        return _login_form(request, then, _sentence(exc), 429)
    if session is None:
        return _login_form(request, then, "That is not the password.", 401)
    response = RedirectResponse(_next(then), 303)
    response.set_cookie(
        _SESSION,
        session,
        max_age=int(auth.LIFETIME.total_seconds()),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    return response


async def _logout(request: Request) -> Response:
    await _sent(request)
    access = request.app.state.access
    if access is None:
        raise HTTPException(404)
    access.leave(request.cookies.get(_SESSION, ""))
    response = RedirectResponse("/login", 303)
    response.delete_cookie(_SESSION)
    return response


def _owner(request: Request) -> str | None:
    """Whose view this is: `?owner=Name`, or everyone's without it."""
    return request.query_params.get("owner") or None


def _sentence(exc: Exception) -> str:
    """What went wrong, as the page says it."""
    message = str(exc)
    return f"{message[0].upper()}{message[1:]}."


def _unknown_name(_: Request, exc: Exception) -> Response:
    return PlainTextResponse(f"{exc}\n", status_code=404)
