"""The web app: pages over the database, for this machine only unless a password is set
(D18).

Handlers stay thin: read the query string or the form, call a service, render. Every page
opens the database read-only; only the forms (the import page's, a statement's page, a
month's change of a category) open it to write. Nothing is loaded from another site, and a
header tells the browser so.

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
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import URL, State, UploadFile
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
    marketdata,
    months,
    networth,
    overview,
    portfolio,
    query,
    staging,
)
from finanze.classification import UnknownInstrumentError, UnknownMaturityError
from finanze.errors import ConfigError, ExtractionError
from finanze.models import Kind
from finanze.prices import MaturitySource, PriceError
from finanze.storage import db, originals, store
from finanze.storage.money import PrecisionError
from finanze.validation import ReconciliationError
from finanze.web import auth, forms

HOST = "127.0.0.1"  # where the app is served unless `finanze.yaml` names another address
_HOSTS = (HOST, "localhost")
_SESSION = "finanze_session"  # the cookie of a signed-in browser
# The cookie of the currency chosen in the header: every page that converts shows its figures
# in it, unless its address names another. A session cookie: the choice is kept while the
# browser is open, and a new visit opens in euros (decided 2026-10-22).
_CURRENCY = "finanze_currency"
# The pages that convert, and have the header's menu. The others show every amount in its own
# currency (decided 2026-10-21).
_CONVERTING = ("/", "/holdings")
# The colour of each kind of asset, the same in the chart, the ring and their tables.
# Neighbours in the stack wear neighbouring colours: those are the pairs the palette was
# checked for.
_SLOTS = {asset: n for n, asset in enumerate(networth.STACK, 1)}
# The space between two slices of the ring, in hundredths of its circle; a slice not twice
# that wide has none.
_GAP = Decimal("0.6")
_STATIC = "/static/"  # with `/login`, all a browser that is not signed in may ask for
_HERE = Path(__file__).parent
_HEADERS = {
    "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}
_COPY = "This is a copy: changes are made on the machine that holds the data."
_PAGE = 200  # transactions shown at once
_STALE = (
    "Nothing was imported: this page was loaded before the app was last started. "
    "Choose the files again and send them from here."
)
_UPLOADS = 200  # files taken by one import: a year of monthly statements of every source
# How long the files of an import that stopped on bills without a maturity are kept, for the
# button that looks them up to import them again; in memory, like the sessions.
_HELD = timedelta(hours=1)
# What stops an import: nothing is stored, and the page says why.
_REFUSED = (
    ExtractionError,
    ReconciliationError,
    ConfigError,
    UnknownInstrumentError,
    staging.StagingError,
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
        "importing": not state.read_only,
        "converting": request.url.path in _CONVERTING,
        "currencies": networth.CURRENCIES,
        "currency": forms.currency(_viewed(request)),
    }


def _viewed(request: Request) -> dict[str, str]:
    """The query of the page, with the currency chosen in the header if it names none."""
    params = dict(request.query_params)
    remembered = request.cookies.get(_CURRENCY, "")
    if "currency" not in params and remembered in networth.CURRENCIES:
        params["currency"] = remembered
    return params


_templates = Jinja2Templates(directory=_HERE / "templates", context_processors=[_everywhere])


def _money(amount: Decimal) -> str:
    """`-1,234.50`: two decimals to read; the stored amount is not changed."""
    return f"{amount:,.2f}"


def _signed(amount: Decimal) -> str:
    """`+1,234.50`: a gain or a loss, which its sign tells apart."""
    return f"{amount:+,.2f}"


def _short(amount: Decimal) -> str:
    """`850`, `12.3k`, `1.2M`: an amount at a glance, half up; the exact one is beside it."""
    whole = amount.quantize(Decimal(1), ROUND_HALF_UP)
    if abs(whole) < 1000:
        return f"{whole.copy_abs() if whole == 0 else whole:f}"
    thousands = (amount / 1000).quantize(Decimal("0.1"), ROUND_HALF_UP)
    if abs(thousands) < 1000:
        return f"{thousands:f}k"
    return f"{(amount / 1_000_000).quantize(Decimal('0.1'), ROUND_HALF_UP):,f}M"


def _exact(number: Decimal) -> str:
    """`1,234.5`: a quantity or a price as it is stored, never rounded, without the zeros
    that say nothing."""
    written = f"{number:,f}"
    return written.rstrip("0").rstrip(".") if "." in written else written


_templates.env.filters["money"] = _money
_templates.env.filters["signed"] = _signed
_templates.env.filters["short"] = _short
_templates.env.filters["exact"] = _exact
_templates.env.filters["plain"] = lambda number: format(number, "f")
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
    """Where a remembered choice of category is saved as a rule (plan 5.4)."""

    shared: Path  # the rules both people share; each person's own file sits next to it
    people: staging.People  # what recognises each person as a counterparty

    def of(self, owner: str) -> staging.Remember:
        everyone = [pattern for patterns in self.people.values() for pattern in patterns]
        others = [p for name, patterns in self.people.items() if name != owner for p in patterns]
        own = classification.personal_file(self.shared, owner)
        return staging.Remember(self.shared, own, everyone, others)


def create_app(  # noqa: PLR0913  # the database, a clock, and one argument per key of `serve`
    database: Path,
    clock: Callable[[], datetime] = _utc_now,
    rules: Rules | None = None,
    *,
    imports: importing.Setup | None = None,
    names: Sequence[str] = (),
    password: str = "",
    read_only: bool = False,
    maturities: MaturitySource | None = None,
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
    `maturities` is where the import page looks up the T-bills an import stopped on: the one
    thing a page asks the network for (decided 2026-11-03). Without it the page only says
    which bills to add to `instruments.yaml`.
    """
    if names and not password:
        msg = "the app is not served under other names without a password"
        raise ValueError(msg)
    access = auth.Access(password) if password else None
    hosts = (*_HOSTS, *names)

    def writing(
        handler: Callable[[Request], Awaitable[Response]],
    ) -> Callable[..., Awaitable[Response]]:
        return _copy if read_only else handler

    app = Starlette(
        routes=[
            Route("/", _overview, name="overview"),
            Route("/networth", _networth, name="networth"),
            Route("/networth.json", _networth_json, name="networth_json"),
            Route("/holdings", _holdings, name="holdings"),
            Route("/cashflow", _cashflow, name="cashflow"),
            Route("/currency", _choose_currency, name="currency"),
            Route("/import", _import_page, name="import"),
            Route("/import", _import, methods=["POST"]),
            Route("/import/maturities", writing(_look_up), methods=["POST"], name="look_up"),
            Route("/import/save", writing(_save), methods=["POST"], name="save"),
            Route("/import/commit", writing(_commit), methods=["POST"], name="commit_draft"),
            Route(
                "/import/category", writing(_new_category), methods=["POST"], name="add_category"
            ),
            Route("/months", _months, name="months"),
            Route("/months/{owner}/{month}", _month, name="month"),
            Route(
                "/months/{owner}/{month}/categorize",
                writing(_change),
                methods=["POST"],
                name="categorize",
            ),
            Route(
                "/months/{owner}/{month}/category",
                writing(_month_category),
                methods=["POST"],
                name="category",
            ),
            Route("/statements/{number:int}", _statement, name="statement"),
            Route("/statements/{number:int}/original", _original, name="original"),
            Route(
                "/statements/{number:int}/discard",
                writing(_discard),
                methods=["POST"],
                name="discard",
            ),
            Route(
                "/statements/{number:int}/uncommit",
                writing(_uncommit),
                methods=["POST"],
                name="uncommit",
            ),
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
            staging.NothingStagedError: _unknown_name,
        },
    )
    app.state.database = database
    app.state.clock = clock
    app.state.rules = rules
    app.state.imports = None if read_only else imports
    app.state.maturities = maturities
    app.state.held = {}
    app.state.access = access
    app.state.read_only = read_only
    app.state.hosts = hosts
    # Sent back by every form of these pages: a page of another site cannot read it, so it
    # cannot make the browser change anything here.
    app.state.token = secrets.token_urlsafe(32)
    return app


@dataclass(frozen=True, slots=True)
class _Arc:
    """One slice of the ring of what is held: a dash along a circle measured as 100."""

    part: overview.Part
    slot: int
    dash: Decimal  # how long it is drawn: its share, less the gap after it
    gap: Decimal  # the rest of the circle
    offset: Decimal  # where it starts, as a dash offset: the shares before it, below zero


def _ring(held: Sequence[overview.Part]) -> list[_Arc]:
    """The slices of the ring, clockwise from the top, in the order the chart stacks them."""
    arcs = []
    start = Decimal(0)
    for part in held:
        share = part.share or Decimal(0)  # every part held has one: their sum is above zero
        dash = share - _GAP if share > 2 * _GAP and len(held) > 1 else share
        arcs.append(_Arc(part, _SLOTS[part.name], dash, 100 - dash, -start))
        start += share
    return arcs


def _overview(request: Request) -> Response:
    asked = forms.networth_view(_viewed(request))
    errors = list(asked.errors)
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
        try:
            shown = overview.run(conn, owner=asked.owner, currency=asked.currency, top=overview.TOP)
        except networth.MissingRateError as exc:
            errors.append(_sentence(exc))
            shown = None
        # A statement valued in a currency without a rate leaves out the brokers' figures,
        # not the page's own.
        try:
            checks = networth.checks(conn, owner=asked.owner)
        except networth.MissingRateError as exc:
            errors.append(_sentence(exc))
            checks = ()
    context = {
        "people": people,
        "owner": asked.owner,
        "asked": asked,
        "errors": errors,
        "shown": shown,
        # The view shown, not the one asked for: a field that could not be read is unset.
        "source": request.url_for("networth_json").include_query_params(
            currency=asked.currency, by=asked.by, **({"owner": asked.owner} if asked.owner else {})
        ),
        "breakdowns": forms.BREAKDOWNS,
        "parts": _parts(shown.worth, asked.by, people) if shown and shown.day else [],
        "slots": _SLOTS,
        "ring": _ring(shown.held) if shown else [],
        "checks": checks,
    }
    return _templates.TemplateResponse(request, "overview.html", context)


def _networth(request: Request) -> Response:
    """The page of the chart is the first page now: an old bookmark leads there."""
    return RedirectResponse(request.url_for("overview").replace(query=request.url.query))


def _import_form(  # noqa: PLR0913  # what the page shows after each of the forms sent to it
    request: Request,
    owner: str | None = None,
    error: str = "",
    done: importing.Read | None = None,
    status: int = 200,
    *,
    undated: _Undated | None = None,
    notice: str = "",
) -> Response:
    """The page of the import: what was just uploaded, and everything staged for one person."""
    state = request.app.state
    if state.read_only:  # a copy is shown, not worked on
        raise HTTPException(404)
    committed = request.query_params.get("committed", "")
    with db.reading(state.database) as conn:
        people = query.people(conn)
        owners = list(state.imports.people) if state.imports else [p.name for p in people]
        owner = owner or _owner(request) or next(iter(owners), "")
        if owner not in owners:
            raise HTTPException(404)
        context = {
            "people": people,
            "owner": owner,
            "owners": list(state.imports.people) if state.imports else [],
            "error": error,
            "undated": undated,
            "notice": notice,
            "done": done,
            "draft": months.draft(conn, owner),
            "choices": query.choices(conn, owner),
            "can_remember": state.rules is not None,
            "committed": int(committed) if committed.isdecimal() else None,
        }
    return _templates.TemplateResponse(request, "import.html", context, status_code=status)


def _import_page(request: Request) -> Response:
    return _import_form(request)


async def _import(request: Request) -> Response:
    """Stage the statements sent for one person; a file that fails stores nothing of itself
    and stops no other."""
    _own_origin(request)
    state = request.app.state
    if state.imports is None:
        raise HTTPException(404)
    async with request.form(max_files=_UPLOADS) as form:
        token = str(form.get("token", ""))
        owner = str(form.get("owner", ""))
        sent = [part for part in form.getlist("file") if isinstance(part, UploadFile)]
        uploads = [(Path(part.filename).name, await part.read()) for part in sent if part.filename]
    try:
        _own_token(request, token)
    except HTTPException:
        # Most often a page left open while the app was started again, which gave it a new
        # token: the page sent back has it, and says what to do.
        return await run_in_threadpool(_import_form, request, None, _STALE, None, 403)
    if owner not in state.imports.people:
        error = f"{owner or 'Nobody'} is nobody the config names."
        return await run_in_threadpool(_import_form, request, None, error, None, 400)
    if not uploads:
        error = "Choose at least one file."
        return await run_in_threadpool(_import_form, request, owner, error, None, 400)
    return await _staged(request, owner, uploads)


@dataclass(frozen=True, slots=True)
class _Held:
    """The files of an import that stopped on bills without a maturity."""

    owner: str
    uploads: list[tuple[str, bytes]]
    bills: tuple[str, ...]
    until: datetime


@dataclass(frozen=True, slots=True)
class _Undated:
    """Bills an import stopped on, and the files kept to import again once they are dated
    ("" when nothing can look them up)."""

    bills: tuple[str, ...]
    held: str


async def _staged(
    request: Request, owner: str, uploads: list[tuple[str, bytes]], notice: str = ""
) -> Response:
    """Read and stage `uploads` for `owner`, and the page that says what became of them."""
    state = request.app.state

    def stage() -> importing.Read:
        with tempfile.TemporaryDirectory() as folder:
            files = _saved(Path(folder), uploads)
            found = importing.read(owner, files, state.imports)
            conn = db.connect(state.database)
            try:
                return importing.stage(conn, state.database, found, state.imports, state.clock())
            finally:
                conn.close()

    try:
        done = await run_in_threadpool(stage)
    except UnknownMaturityError as exc:
        held = _hold(state, _Held(owner, uploads, exc.bills, state.clock() + _HELD))
        undated = _Undated(exc.bills, held if state.maturities else "")
        error = f"Nothing was imported: {exc}."
        return await run_in_threadpool(
            _import_form, request, owner, error, None, 409, undated=undated, notice=notice
        )
    except _REFUSED as exc:
        error = f"Nothing was imported: {exc}."
        return await run_in_threadpool(
            _import_form, request, owner, error, None, 409, notice=notice
        )
    return await run_in_threadpool(_import_form, request, owner, "", done, notice=notice)


def _hold(state: State, held: _Held) -> str:
    """Keep `held` for the button that looks its bills up; what has waited too long goes."""
    now = state.clock()
    for key in [key for key, kept in state.held.items() if kept.until <= now]:
        del state.held[key]
    key = secrets.token_urlsafe(16)
    state.held[key] = held
    return key


async def _look_up(request: Request) -> Response:
    """Look up the bills an import stopped on, write down when they mature, and import the
    same files again."""
    _own_origin(request)
    state = request.app.state
    if state.imports is None or state.maturities is None:
        raise HTTPException(404)
    async with request.form() as form:
        token, key = str(form.get("token", "")), str(form.get("held", ""))
    try:
        _own_token(request, token)
    except HTTPException:
        return await run_in_threadpool(_import_form, request, None, _STALE, None, 403)
    held = state.held.get(key)
    if held is None or held.until <= state.clock():
        error = "The files of that import are no longer kept: choose them again."
        return await run_in_threadpool(_import_form, request, None, error, None, 409)
    undated = _Undated(held.bills, key)
    try:
        looked = await run_in_threadpool(marketdata.maturities, state.maturities, held.bills)
    except PriceError as exc:
        error = f"TreasuryDirect could not tell: {exc}. Try again later, or add the maturity "
        error += "to instruments.yaml."
        return await run_in_threadpool(
            _import_form, request, held.owner, error, None, 502, undated=undated
        )
    if looked.unknown:
        error = f"TreasuryDirect knows no bill {', '.join(looked.unknown)}: add its maturity "
        error += "to instruments.yaml."
        return await run_in_threadpool(
            _import_form, request, held.owner, error, None, 409, undated=undated
        )
    try:
        classification.add_maturities(state.imports.instruments, looked.found)
    except ConfigError as exc:
        return await run_in_threadpool(
            _import_form, request, held.owner, f"{exc}.", None, 409, undated=undated
        )
    del state.held[key]
    days = "; ".join(f"{bill} matures on {day}" for bill, day in looked.found.items())
    notice = f"{days} (TreasuryDirect), written in instruments.yaml."
    return await _staged(request, held.owner, held.uploads, notice)


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


_BEHIND = 35  # days an account may end before the newest day committed for its person
# Pension certificates come once or twice a year: such an account is never behind.
_NOT_MONTHLY = frozenset({"axa", "servisa"})


@dataclass(frozen=True, slots=True)
class _Account:
    """How far an account is committed, and whether it lags behind the person's others."""

    coverage: months.Coverage
    behind: bool


def _accounts(lines: Sequence[months.Coverage]) -> list[_Account]:
    newest: dict[str, date] = {}
    for line in lines:
        if line.committed:
            newest[line.owner] = max(newest.get(line.owner, line.committed), line.committed)
    return [
        _Account(
            line,
            line.committed is not None
            and line.source not in _NOT_MONTHLY
            and (newest[line.owner] - line.committed).days > _BEHIND,
        )
        for line in lines
    ]


def _months(request: Request) -> Response:
    owner = _owner(request)
    with db.reading(request.app.state.database) as conn:
        query.check_names(conn, query.Filter(owner=owner))
        lines = months.listing(conn, owner)
        accounts = _accounts(months.coverage(conn, owner))
        people = query.people(conn)
    shown = [person for person in people if owner in (None, person.name)]
    context = {
        "people": people,
        "shown": shown,
        "owner": owner,
        "lines": lines,
        "coverage": accounts,
        "behind": _BEHIND,
    }
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


class _Unsaved(Exception):
    """A category chosen on the draft could not be given."""


def _to_import(request: Request, owner: str, **flags: int) -> Response:
    return RedirectResponse(
        request.url_for("import").include_query_params(owner=owner, **flags), 303
    )


def _apply(state: State, owner: str, fields: dict[str, list[str]]) -> str:
    """Give every category chosen on the draft, together; "" when all went in, else why not.

    A counterparty's choice (`group:N`) goes to its rows (`ids:N`), as a rule when
    `remember:N` says so, except to a row chosen on its own (`category:<id>`), which keeps its
    choice. A row whose category was chosen again is left alone. Rows going to the same
    category, all waiting or all already categorized, are one choice.
    """
    own, groups = _filled(fields, "category:"), _filled(fields, "group:")
    if not own and not groups:
        return ""
    conn = db.connect(state.database)
    try:
        rows = {row.id: row for row in months.draft(conn, owner).rows}
        members = {number: fields.get(f"ids:{number}", []) for number in groups}
        wanted = [*own, *(row_id for ids in members.values() for row_id in ids)]
        if not wanted or any(row_id not in rows for row_id in wanted):
            return "No category was set: those rows are not staged any more: load the page again."
        together: dict[tuple[str, bool], list[str]] = {}
        for row_id, category in own.items():
            if category != rows[row_id].category:
                together.setdefault((category, bool(rows[row_id].category)), []).append(row_id)
        choices = [
            staging.Choice(ids, category, change=change)
            for (category, change), ids in together.items()
        ]
        for number, category in groups.items():
            ids = [row_id for row_id in members[number] if row_id not in own]
            rule = fields.get(f"remember:{number}", [""])[-1] == "rule"
            if ids:
                choices.append(staging.Choice(ids, category, rule=rule))
        staging.categorize_all(
            conn,
            owner=owner,
            choices=choices,
            now=state.clock(),
            remember=None if state.rules is None else state.rules.of(owner),
        )
    except (staging.StagingError, store.UnknownCategoryError) as exc:
        return f"No category was set: {exc}."
    finally:
        conn.close()
    return ""


def _filled(fields: dict[str, list[str]], prefix: str) -> dict[str, str]:
    """The fields named `prefix` and something, by that something, without the empty ones."""
    return {
        name.removeprefix(prefix): values[-1].strip()
        for name, values in fields.items()
        if name.startswith(prefix) and values[-1].strip()
    }


async def _save(request: Request) -> Response:
    """Apply the categories chosen on the draft, and stay on the page."""
    fields = await _sent(request)
    owner = fields.get("owner", [""])[-1]
    error = await run_in_threadpool(_apply, request.app.state, owner, fields)
    if error:
        return await run_in_threadpool(_import_form, request, owner, error, None, 409)
    return _to_import(request, owner)


async def _commit(request: Request) -> Response:
    """Apply the categories chosen, then commit what is staged for the person."""
    fields = await _sent(request)
    owner = fields.get("owner", [""])[-1]
    state = request.app.state

    def commit() -> int:
        error = _apply(state, owner, fields)
        if error:
            raise _Unsaved(error)
        conn = db.connect(state.database)
        try:
            files, _ = staging.commit_with_copy(
                conn, state.database, owner=owner, now=state.clock()
            )
        finally:
            conn.close()
        return len(files)

    try:
        count = await run_in_threadpool(commit)
    except _Unsaved as exc:
        return await run_in_threadpool(_import_form, request, owner, str(exc), None, 409)
    except (staging.UncategorizedRowsError, ReconciliationError) as exc:
        # Nothing was committed, and nothing here can override it: the page says why.
        error = f"Not committed: {exc}."
        return await run_in_threadpool(_import_form, request, owner, error, None, 409)
    return _to_import(request, owner, committed=count)


def _choose(state: State, owner: str, fields: dict[str, list[str]]) -> str:
    """Give one category to the rows of a form; "" when it went in, else why not."""
    wanted = fields.get("remember", [""])[-1] == "rule" and state.rules is not None
    conn = db.connect(state.database)
    try:
        staging.categorize(
            conn,
            owner=owner,
            ids=fields.get("id", []),
            category=fields.get("category", [""])[-1],
            now=state.clock(),
            remember=state.rules.of(owner) if wanted else None,
            change=fields.get("change", [""])[-1] == "1",
        )
    except (staging.StagingError, store.UnknownCategoryError) as exc:
        return f"No category was set: {exc}."
    finally:
        conn.close()
    return ""


async def _change(request: Request) -> Response:
    """Another category for committed rows, from a month's page."""
    fields = await _sent(request)
    owner, month = _which(request)
    error = await run_in_threadpool(_choose, request.app.state, owner, fields)
    if error:
        return await run_in_threadpool(_month_page, request, owner, month, error, 409)
    return RedirectResponse(request.url_for("month", owner=owner, month=f"{month:%Y-%m}"), 303)


def _added(state: State, fields: dict[str, list[str]]) -> str:
    """One more category in the taxonomy; "" when it was added, else why not."""
    side, _, group = fields.get("under", [""])[-1].partition(":")
    conn = db.connect(state.database)
    try:
        staging.add_category(
            conn,
            state.rules.shared.with_name(categories.FILE),
            name=fields.get("name", [""])[-1],
            side=side,
            group=group,
        )
    except (staging.StagingError, ConfigError) as exc:
        return f"No category was added: {exc}."
    finally:
        conn.close()
    return ""


async def _new_category(request: Request) -> Response:
    """One more category, from the import page."""
    fields = await _sent(request)
    owner = fields.get("owner", [""])[-1]
    if request.app.state.rules is None:  # nowhere to write it down for the next import
        raise HTTPException(404)
    error = await run_in_threadpool(_added, request.app.state, fields)
    if error:
        return await run_in_threadpool(_import_form, request, owner, error, None, 409)
    return _to_import(request, owner)


async def _month_category(request: Request) -> Response:
    """One more category, from the page of a month."""
    fields = await _sent(request)
    owner, month = _which(request)
    if request.app.state.rules is None:
        raise HTTPException(404)
    error = await run_in_threadpool(_added, request.app.state, fields)
    if error:
        return await run_in_threadpool(_month_page, request, owner, month, error, 409)
    return RedirectResponse(request.url_for("month", owner=owner, month=f"{month:%Y-%m}"), 303)


@dataclass(frozen=True, slots=True)
class _Part:
    """One series of the net-worth chart: the total, or what one asset or person adds to it."""

    name: str
    slot: int  # which colour it wears, the same whatever else is shown; 0 for the total
    values: tuple[Decimal, ...]


def _parts(result: networth.NetWorth, by: str, people: tuple[query.Person, ...]) -> list[_Part]:
    """The total first, then its parts in the order the chart stacks them, from the bottom up:
    each keeps its colour whoever is looked at."""
    parts = [_Part("Net worth", 0, result.total)]
    if by == "asset":
        slots = _SLOTS
    elif by == "owner":
        slots = {person.name: n for n, person in enumerate(people, 1)}
    else:
        return parts
    stacked = [_Part(name, slots[name], values) for name, values in result.by(by).items()]
    return parts + sorted(stacked, key=lambda part: part.slot)


def _networth_json(request: Request) -> Response:
    """What the chart of the first page draws, for the view of its address. Amounts are text,
    to the cent."""
    asked = forms.networth_view(_viewed(request))
    error = ""
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
        try:
            result = networth.run(conn, owner=asked.owner, currency=asked.currency)
        except networth.MissingRateError as exc:
            error = _sentence(exc)
    if asked.errors or error:
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
    asked = forms.holdings_view(_viewed(request))
    errors = list(asked.errors)
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
        try:
            today = request.app.state.clock().date()
            held = portfolio.run(conn, owner=asked.owner, currency=asked.currency, today=today)
        except networth.MissingRateError as exc:
            errors.append(_sentence(exc))
            held = None
    context = {
        "people": people,
        "owner": asked.owner,
        "asked": asked,
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


def _statement(request: Request, error: str = "", status: int = 200) -> Response:
    statement, original = _stored(request)
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
    context = {
        "people": people,
        "owner": None,
        "statement": statement,
        "kept": bool(original),
        "error": error,
    }
    return _templates.TemplateResponse(request, "statement.html", context, status_code=status)


async def _remove(request: Request, action: Callable[..., object]) -> Response:
    """Discard a staged statement or take back a committed one, then go to its person's
    import page, where it is staged."""
    await _sent(request)
    state = request.app.state

    def act() -> tuple[str, str]:
        with db.reading(state.database) as conn:
            statement = months.statement(conn, request.path_params["number"])
        if statement is None:
            raise HTTPException(404)
        conn = db.connect(state.database)
        try:
            action(conn, owner=statement.owner, file_id=statement.id)
        except staging.StagingError as exc:
            return statement.owner, f"Nothing was changed: {exc}."
        finally:
            conn.close()
        return statement.owner, ""

    owner, error = await run_in_threadpool(act)
    if error:
        return await run_in_threadpool(_statement, request, error, 409)
    return _to_import(request, owner)


async def _discard(request: Request) -> Response:
    return await _remove(request, staging.discard)


async def _uncommit(request: Request) -> Response:
    return await _remove(request, staging.uncommit)


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


def _choose_currency(request: Request) -> Response:
    """Remembers the currency chosen in the header until the browser is closed and shows the
    page it was chosen on again, in that currency: without one its address names."""
    asked = request.query_params.get("currency", "").strip().upper()
    if asked not in networth.CURRENCIES:
        said = f"'{asked}' is not one of {', '.join(networth.CURRENCIES)}.\n"
        return PlainTextResponse(said, status_code=400)
    back = URL(_next(request.query_params.get("back", "/"))).remove_query_params("currency")
    response = RedirectResponse(str(back), 303)
    response.set_cookie(
        _CURRENCY,
        asked,  # no max-age: forgotten when the browser closes (decided 2026-10-22)
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


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
