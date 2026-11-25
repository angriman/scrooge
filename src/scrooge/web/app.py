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

import base64
import csv
import hashlib
import io
import re
import secrets
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TypeVar
from urllib.parse import parse_qs, quote, urlsplit

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.convertors import Convertor, register_url_convertor
from starlette.datastructures import URL, State, UploadFile
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from scrooge import (
    cashflow,
    categories,
    classification,
    importing,
    income,
    marketdata,
    months,
    networth,
    overview,
    portfolio,
    query,
    report,
    spending,
    staging,
    together,
)
from scrooge.classification import UnknownMaturityError
from scrooge.errors import ConfigError
from scrooge.extractors.common import month_end
from scrooge.models import Kind
from scrooge.prices import MaturitySource, PriceError
from scrooge.storage import db, originals, store
from scrooge.storage import logos as store_logos
from scrooge.validation import ReconciliationError
from scrooge.web import api, auth, forms, settings_api, shapes, wire
from scrooge.web import uploads as uploads_

HOST = "127.0.0.1"  # where the app is served unless `scrooge.yaml` names another address
_HOSTS = (HOST, "localhost")
_SESSION = "scrooge_session"  # the cookie of a signed-in browser
# The cookie of the currency chosen in the header: every page that converts shows its figures
# in it, unless its address names another. A session cookie: the choice is kept while the
# browser is open, and a new visit opens in euros (decided 2026-10-22).
_CURRENCY = "scrooge_currency"
# The pages that convert, and have the header's menu. The others show every amount in its own
# currency (decided 2026-10-21).
_CONVERTING = ("/", "/holdings", "/analysis")
# The colour of each kind of asset, the same in the chart, the ring and their tables.
# Neighbours in the stack wear neighbouring colours: those are the pairs the palette was
# checked for.
_SLOTS = {asset: n for n, asset in enumerate(networth.STACK, 1)}
_COLOURS = 7  # the series colours there are
_REST = _COLOURS + 1  # grey, for what is folded together
# The space between two slices of the ring, in hundredths of its circle; a slice not twice
# that wide has none.
_GAP = Decimal("0.6")
_TENTH = Decimal("0.1")  # a slice's share of its ring, in percent
# The logo of a holding is the user's own image, kept in the database (decided 2026-11-07).
# It is never fetched, and a holding without one wears its letters.
# A logo opened on its own runs nothing and loads nothing: an SVG may hold a script.
_LOGO_HEADERS = {
    "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "cache-control": "private, max-age=86400",
}
# The holdings page's map of positions and line of bills, in the units of their SVG.
_MAP = (Decimal(1000), Decimal(420))
_LINE = (Decimal(1000), Decimal(90))
_APART = Decimal(64)  # the least room between two days written under the line
_STATIC = "/static/"  # with `/login`, all a browser that is not signed in may ask for
_APP = "/app"  # where the frontend of its own is served, with the headers it needs
_API = "/api/"  # the JSON of the pages (`web/api.py`): not signed in, it is told so, not sent on
# The frontend of its own, built by `make web` from frontend/ (git-ignored): `serve` gives it to
# `create_app`, at /app.
SPA = Path(__file__).parent / "spa"
_INLINE = re.compile(r"<script>(.*?)</script>", re.DOTALL)  # a script written in a page
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
_CSV_CATEGORY = ("category", "category_parent")  # keys, written by their labels
_CSV_TEXT = ("counterparty", "description")  # words of the statement, not the app's own
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


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
    """The query of the page, with the currency chosen in the header if it names none, and the
    config's otherwise."""
    params = dict(request.query_params)
    remembered = request.cookies.get(_CURRENCY, "")
    if "currency" not in params:
        chosen = remembered in networth.CURRENCIES
        params["currency"] = remembered if chosen else request.app.state.currency
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


def _short_signed(amount: Decimal) -> str:
    """`+12.3k`, `-850`: a change at a glance."""
    return (
        f"+{_short(amount)}" if amount.quantize(Decimal(1), ROUND_HALF_UP) > 0 else _short(amount)
    )


def _exact(number: Decimal) -> str:
    """`1,234.5`: a quantity or a price as it is stored, never rounded, without the zeros
    that say nothing."""
    written = f"{number:,f}"
    return written.rstrip("0").rstrip(".") if "." in written else written


_templates.env.filters["money"] = _money
_templates.env.filters["signed"] = _signed
_templates.env.filters["short"] = _short
_templates.env.filters["short_signed"] = _short_signed
_templates.env.filters["exact"] = _exact
_templates.env.filters["plain"] = lambda number: format(number, "f")
_templates.env.globals["days"] = cashflow.days


class _OwnAssetsOnly:
    """Adds the headers that keep a page from loading or sending anything elsewhere: those of
    the frontend's own under /app, `_HEADERS` everywhere else."""

    def __init__(self, app: ASGIApp, headers: Mapping[str, str]) -> None:
        self.app = app
        self.pages = [(k.encode(), v.encode()) for k, v in _HEADERS.items()]
        self.frontend = [(k.encode(), v.encode()) for k, v in headers.items()]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        headers = self.frontend if path == _APP or path.startswith(f"{_APP}/") else self.pages

        async def add_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] += headers
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
        if path.startswith(_API):
            response = JSONResponse({"errors": ["Sign in first."]}, 401)
        elif request.method in ("GET", "HEAD"):
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
    currency: str = networth.DEFAULT_CURRENCY,
    currencies: Sequence[str] = networth.DEFAULT_COLUMNS,
    frozen: Sequence[networth.FrozenAccount] = (),
    benchmarks: Sequence[str] = (),
    spa: Path | None = None,
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
    `currency` is what the figures are shown in until another is chosen on a page.
    `currencies` are those the month-end table of net worth shows side by side.
    `frozen` names the accounts whose money cannot be reached (a rent deposit): with the
    pensions, net worth sets them apart from what is liquid.
    `benchmarks` are the symbols of what the investments' returns are set beside.
    `spa` is the folder of the frontend built from frontend/, served at /app: every address
    there that is not one of its files is its page. Without it there is no /app.
    """
    if names and not password:
        msg = "the app is not served under other names without a password"
        raise ValueError(msg)
    access = auth.Access(password) if password else None
    hosts = (*_HOSTS, *names)
    shell = _shell(spa) if spa is not None else None

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
            Route("/analysis", _analysis, name="analysis"),
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
            Route("/statements/{number:id}", _statement, name="statement"),
            Route("/statements/{number:id}/original", _original, name="original"),
            Route("/logos/{symbol}", _logo, name="logo"),
            Route("/holdings/logo", writing(_set_logo), methods=["POST"], name="set_logo"),
            Route(
                "/statements/{number:id}/discard",
                writing(_discard),
                methods=["POST"],
                name="discard",
            ),
            Route(
                "/statements/{number:id}/uncommit",
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
            Mount(f"{_API}settings", routes=settings_api.routes),  # before the rest of /api
            Mount(_API.rstrip("/"), routes=api.routes),
            *(
                []
                if spa is None
                else [Route("/app", _spa), Route("/app/{path:path}", _spa, name="spa")]
            ),
        ],
        middleware=[
            # A page of another site cannot reach the app under a name of its own.
            Middleware(TrustedHostMiddleware, allowed_hosts=list(hosts)),
            Middleware(_OwnAssetsOnly, headers=_headers(shell)),
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
    # The category of the rows no rule matched, which wait there to be given one; "": none.
    app.state.unmatched = "" if imports is None else imports.unmatched
    app.state.maturities = maturities
    app.state.currency = currency
    app.state.currencies = tuple(currencies)
    app.state.frozen = tuple(frozen)
    app.state.benchmarks = tuple(benchmarks)
    app.state.held = {}
    app.state.access = access
    app.state.read_only = read_only
    app.state.hosts = hosts
    app.state.spa = spa
    app.state.shell = shell
    # Sent back by every form of these pages: a page of another site cannot read it, so it
    # cannot make the browser change anything here.
    app.state.token = secrets.token_urlsafe(32)
    return app


def _shell(spa: Path) -> bytes | None:
    """The page of the frontend at /app, None until it is built."""
    page = spa / "index.html"
    return page.read_bytes() if page.is_file() else None


def _headers(shell: bytes | None) -> dict[str, str]:
    """The headers of the frontend at /app: `_HEADERS`, letting the scripts of `shell` run.
    Its page starts itself with one written in it, which the policy allows by its digest and
    nothing else written in a page. Its elements carry style attributes (where a chart places a
    mark, where a menu opens), which it allows too; a stylesheet still comes only from here.
    The server's own pages need neither, and keep `_HEADERS`."""
    if shell is None:
        return _HEADERS
    digests = [
        "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
        for script in _INLINE.findall(shell.decode())
    ]
    if not digests:
        return _HEADERS
    policy = (
        f"default-src 'self'; script-src 'self' {' '.join(digests)}; "
        "style-src 'self'; style-src-attr 'unsafe-inline'; frame-ancestors 'none'"
    )
    return {**_HEADERS, "content-security-policy": policy}


def _spa(request: Request) -> Response:
    """A file of the frontend, or its page for any other address under /app."""
    state = request.app.state
    if state.shell is None:
        msg = "The frontend at /app is not built: run 'make web'.\n"
        return PlainTextResponse(msg, status_code=404)
    asked = request.path_params.get("path", "")
    root = state.spa.resolve()
    found = (root / asked).resolve()
    if asked and found.is_relative_to(root) and found.is_file():
        return FileResponse(found)
    return HTMLResponse(state.shell)


@dataclass(frozen=True, slots=True)
class _Slice:
    """A part of a ring other than the overview's: a category spent on, a source of income."""

    name: str
    value: Decimal
    share: Decimal | None  # of the ring, in percent


@dataclass(frozen=True, slots=True)
class _Arc:
    """One slice of a ring: a dash along a circle measured as 100."""

    part: overview.Part | _Slice
    slot: int
    dash: Decimal  # how long it is drawn: its share, less the gap after it
    gap: Decimal  # the rest of the circle
    offset: Decimal  # where it starts, as a dash offset: the shares before it, below zero


def _ring(held: Sequence[overview.Part | _Slice], slots: Mapping[str, int] = _SLOTS) -> list[_Arc]:
    """The slices of the ring, clockwise from the top, in the order the chart stacks them."""
    arcs = []
    start = Decimal(0)
    for part in held:
        share = part.share or Decimal(0)  # every part held has one: their sum is above zero
        dash = share - _GAP if share > 2 * _GAP and len(held) > 1 else share
        arcs.append(_Arc(part, slots[part.name], dash, 100 - dash, -start))
        start += share
    return arcs


_Listed = TypeVar("_Listed", overview.Holding, portfolio.Holding)


@dataclass(frozen=True, slots=True)
class _Segment:
    """One part of a bar cut in shares: a person's of net worth."""

    part: overview.Part
    slot: int
    x: Decimal  # where it starts, in hundredths of the bar
    width: Decimal  # its share


def _split(parts: Sequence[overview.Part], slots: Mapping[str, int]) -> list[_Segment]:
    """The parts side by side in one bar, each as wide as its share; no bar when a share is
    missing or below zero, which a bar cannot show."""
    if any(part.share is None or part.share < 0 for part in parts):
        return []
    segments = []
    start = Decimal(0)
    for part in parts:
        width = part.share or Decimal(0)
        segments.append(_Segment(part, slots[part.name], start, width))
        start += width
    return segments


def _lengths(top: Sequence[_Listed]) -> dict[_Listed, Decimal]:
    """How long each holding's bar is drawn, in hundredths of the largest's."""
    most = max((holding.value for holding in top), default=Decimal(0))
    if most <= 0:
        return dict.fromkeys(top, Decimal(0))
    return {
        holding: (max(holding.value, Decimal(0)) * 100 / most).quantize(_TENTH) for holding in top
    }


def _person_slots(people: Sequence[query.Person]) -> dict[str, int]:
    """The colour of each person, the same in the chart and the bar of the first page."""
    return {person.name: n for n, person in enumerate(people, 1)}


def _overview(request: Request) -> Response:
    asked = forms.networth_view(_viewed(request), forms.PAGE_BREAKDOWNS)
    errors = list(asked.errors)
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
        logos = api.logos(request, conn)
        shown = wire.unless_rate_missing(
            errors,
            lambda: overview.run(
                conn, owner=asked.owner, currency=asked.currency, top=overview.TOP
            ),
            None,
        )
        # A statement valued in a currency without a rate leaves out the brokers' figures,
        # not the page's own.
        checks = wire.unless_rate_missing(
            errors, lambda: networth.checks(conn, owner=asked.owner), ()
        )
        lines = together.lines(conn, asked.owner)
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
        "breakdowns": forms.PAGE_BREAKDOWNS,
        "parts": _parts(shown.worth, asked.by, people) if shown and shown.day else [],
        "slots": _SLOTS,
        "ring": _ring(shown.held) if shown else [],
        "lengths": _lengths(shown.top) if shown else {},
        "person_slots": _person_slots(people),
        "split": _split(shown.people, _person_slots(people)) if shown else [],
        "logos": logos,
        "checks": checks,
        # The accounts a statement of which is probably missing, as /months marks them.
        "behind": [line for line, late in zip(lines, months.behind(lines), strict=True) if late],
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
        choices = query.choices(conn, owner)
        context = {
            "people": people,
            "owner": owner,
            "owners": list(state.imports.people) if state.imports else [],
            "error": error,
            "undated": undated,
            "notice": notice,
            "done": done,
            "draft": months.draft(conn, owner),
            "choices": choices,
            "label": choices.label,
            "can_remember": state.rules is not None,
            "committed": _whole(committed),
        }
    return _templates.TemplateResponse(request, "import.html", context, status_code=status)


_DIGITS = 18  # an id or a count has no more: SQLite's integers hold 19, Python reads far more


class _IdConvertor(Convertor[int]):
    """`{number:id}`: a stored id. Longer than any the database holds is no route, so a
    page is not found, and the number is never read as an integer."""

    regex = rf"[0-9]{{1,{_DIGITS}}}"

    def convert(self, value: str) -> int:
        return int(value)

    def to_string(self, value: int) -> str:
        return str(value)


register_url_convertor("id", _IdConvertor())


def _whole(text: str) -> int | None:
    """A whole number from a query string; None when it is not one or too long to be asked."""
    return int(text) if text.isdecimal() and len(text) <= _DIGITS else None


def _import_page(request: Request) -> Response:
    return _import_form(request)


async def _import(request: Request) -> Response:
    """Stage the statements sent for one person; a file that fails stores nothing of itself
    and stops no other."""
    _own_origin(request)
    state = request.app.state
    if state.imports is None:
        raise HTTPException(404)
    async with request.form(max_files=uploads_.UPLOADS) as form:
        token = str(form.get("token", ""))
        owner = str(form.get("owner", ""))
        sent = [part for part in form.getlist("file") if isinstance(part, UploadFile)]
        uploads = [
            (uploads_.name(part.filename), await part.read()) for part in sent if part.filename
        ]
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

    try:
        done = await run_in_threadpool(uploads_.stage, state, owner, uploads)
    except UnknownMaturityError as exc:
        held = uploads_.hold(
            state, uploads_.Held(owner, uploads, exc.bills, state.clock() + uploads_.HELD)
        )
        undated = _Undated(exc.bills, held if state.maturities else "")
        error = f"Nothing was imported: {exc}."
        return await run_in_threadpool(
            _import_form, request, owner, error, None, 409, undated=undated, notice=notice
        )
    except uploads_.REFUSED as exc:
        error = f"Nothing was imported: {exc}."
        return await run_in_threadpool(
            _import_form, request, owner, error, None, 409, notice=notice
        )
    return await run_in_threadpool(_import_form, request, owner, "", done, notice=notice)


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


_WIDE = Decimal(1000)  # the timeline of statements, in the units of its SVG
_CLOSED = "Closed accounts"  # the group of the timeline, after the people's


@dataclass(frozen=True, slots=True)
class _Account:
    """How far an account is committed, and whether it lags behind the person's others."""

    coverage: months.Coverage
    behind: bool

    @property
    def certificate(self) -> bool:
        return self.coverage.source in months.NOT_MONTHLY

    @property
    def closed(self) -> date | None:
        return self.coverage.closed


def _accounts(lines: Sequence[months.Coverage]) -> list[_Account]:
    return [_Account(line, late) for line, late in zip(lines, months.behind(lines), strict=True)]


@dataclass(frozen=True, slots=True)
class _Run:
    """Days an account has statements for, and where they are drawn on the timeline."""

    span: months.Span
    band: shapes.Band


@dataclass(frozen=True, slots=True)
class _Row:
    track: months.Track
    behind: bool
    runs: list[_Run]
    closed: date | None  # the day the account was closed; None while it is open


@dataclass(frozen=True, slots=True)
class _Timeline:
    """The statements of every account along the months, a row each, a group per person and
    the closed accounts last."""

    first: date
    last: date
    width: Decimal
    ticks: list[shapes.Tick]
    groups: list[tuple[str, list[_Row]]]


def _timeline(
    tracks: Sequence[months.Track], accounts: Sequence[_Account], today: date
) -> _Timeline | None:
    """The months from the first a statement covers to the end of this one, or of the last
    covered if later; None without statements."""
    spans = [span for track in tracks for span in track.spans]
    if not spans:
        return None
    first = min(span.first for span in spans).replace(day=1)
    last = month_end(max(today, *(span.last for span in spans)))
    known = {(a.coverage.owner, a.coverage.account, a.coverage.source): a for a in accounts}
    groups: dict[str, list[_Row]] = {}
    closed: list[_Row] = []
    for track in tracks:
        found = shapes.bands([(span.first, span.last) for span in track.spans], first, last, _WIDE)
        runs = [_Run(span, band) for span, band in zip(track.spans, found, strict=True)]
        account = known[track.owner, track.account, track.source]
        row = _Row(track, account.behind, runs, account.closed)
        (closed if row.closed else groups.setdefault(track.owner, [])).append(row)
    if closed:
        groups[_CLOSED] = closed
    ticks = shapes.months_axis(first, last, _WIDE)
    return _Timeline(first, last, _WIDE, ticks, list(groups.items()))


def _months(request: Request) -> Response:
    owner = _owner(request)
    with db.reading(request.app.state.database) as conn:
        query.check_names(conn, query.Filter(owner=owner))
        lines = months.listing(conn, owner)
        # A broker's currencies are one account, as at /app/accounts (`together`).
        rows = together.rows(conn, owner)
        accounts = _accounts([row.line for row in rows])
        tracks = [months.Track(r.line.owner, r.line.account, r.line.source, r.spans) for r in rows]
        people = query.people(conn)
    today = request.app.state.clock().date()
    shown = [person for person in people if owner in (None, person.name)]
    context = {
        "people": people,
        "shown": shown,
        "owner": owner,
        "lines": lines,
        "coverage": accounts,
        "timeline": _timeline(tracks, accounts, today),
        "behind": months.BEHIND,
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
        choices = query.choices(conn, owner)
        context = {
            "people": query.people(conn),
            "owner": owner,
            "month": months.checklist(conn, owner, month),
            "error": error,
            "choices": choices,
            "label": choices.label,
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
            if store.category_key(conn, category) != rows[row_id].category:
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
        slots = _person_slots(people)
    else:
        return parts
    stacked = [_Part(name, slots[name], values) for name, values in result.by(by).items()]
    return parts + sorted(stacked, key=lambda part: part.slot)


def _networth_json(request: Request) -> Response:
    """What the chart of the first page draws, for the view of its address. Amounts are text,
    to the cent."""
    asked = forms.networth_view(_viewed(request), forms.PAGE_BREAKDOWNS)
    noted: list[str] = []
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
        result = wire.unless_rate_missing(
            noted, lambda: networth.run(conn, owner=asked.owner, currency=asked.currency), None
        )
    if asked.errors or result is None:
        return JSONResponse({"errors": [*asked.errors, *noted]}, 400)
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


def _lenses(
    held: portfolio.Portfolio, people: Sequence[query.Person], owner: str | None
) -> list[tuple[str, list[shapes.Piece]]]:
    """What is held cut by kind, by currency, by account and (everyone's) by person."""
    cuts: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for position in held.positions:
        account = position.account if owner else f"{position.account} ({position.owner})"
        cuts["currency"][position.currency] += position.value
        cuts["account"][account] += position.value
    lenses = [("Kind", shapes.pieces(held.by_asset, _SLOTS))]
    for title, cut in (("Currency", cuts["currency"]), ("Account", cuts["account"])):
        ranked = sorted(cut, key=lambda name: -cut[name])
        slots = {name: n for n, name in enumerate(ranked, 1)}
        shown, others = dict(cut), None
        if len(ranked) > _COLOURS:  # one colour each for the largest, the rest grey together
            rest = ranked[_COLOURS - 1 :]
            others = f"{len(rest)} others"
            shown = {name: cut[name] for name in ranked[: _COLOURS - 1]}
            shown[others] = sum((cut[name] for name in rest), Decimal(0))
            slots[others] = _REST
        lenses.append((title, shapes.pieces(shown, slots, last=others)))
    if owner is None and len(people) > 1:
        by_owner: dict[str, Decimal] = defaultdict(Decimal)
        for position in held.positions:
            by_owner[position.owner] += position.value
        lenses.append(("Person", shapes.pieces(by_owner, _person_slots(people))))
    return lenses


def _holdings(request: Request, notice: str = "", status: int = 200) -> Response:
    asked = forms.holdings_view(_viewed(request))
    errors = [notice] if notice else []
    errors.extend(asked.errors)
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
        logos = api.logos(request, conn)
        today = request.app.state.clock().date()
        held = wire.unless_rate_missing(
            errors,
            lambda: portfolio.run(conn, owner=asked.owner, currency=asked.currency, today=today),
            None,
        )
        # Bills wear their own icon; every other holding is asked for a logo it has not got.
        without = (
            store_logos.missing(conn, [h.symbol for h in held.holdings if h.asset != "Bills"])
            if held and held.positions
            else []
        )
    context: dict[str, object] = {
        "people": people,
        "owner": asked.owner,
        "asked": asked,
        "errors": errors,
        "held": held,
        "slots": _SLOTS,
        "logos": logos,
        "without_logo": without,
        "map": {"width": _MAP[0], "height": _MAP[1]},
        "line": {"width": _LINE[0], "height": _LINE[1]},
    }
    if held and held.positions:
        holdings = held.holdings
        gains = sorted(
            holdings,
            key=lambda h: (h.return_percent is None, -(h.return_percent or 0), h.symbol),
        )
        rungs = held.ladder
        # The bills that mature on the same day are one bar.
        due: dict[date, list[portfolio.Rung]] = defaultdict(list)
        for rung in rungs:
            if rung.position.maturity is not None:
                due[rung.position.maturity].append(rung)
        marks, _ = shapes.line_of_days(
            list(due),
            [sum((rung.value for rung in same), Decimal(0)) for same in due.values()],
            held.ladder_day or today,
            *_LINE,
        )
        context |= {
            "holdings": holdings,
            "rects": shapes.squarify([h.value for h in holdings], *_MAP),
            "lengths": _lengths(holdings),
            "gains": list(
                zip(gains, shapes.reaches([h.return_percent for h in gains]), strict=True)
            ),
            "lenses": _lenses(held, people, asked.owner),
            "accounts": len({(p.owner, p.account) for p in held.positions}),
            "due": list(zip(due.items(), shapes.spaced(marks, _APART), strict=True)),
            "next": next(
                (rung for rung in rungs if rung.days is not None and rung.days >= 0), None
            ),
        }
    return _templates.TemplateResponse(request, "holdings.html", context, status_code=status)


# The bars of the analysis page: the plot is _WIDTH by _HEIGHT, labels outside it. Each part
# of a stack wears the colour of its place in it.
_WIDTH, _HEIGHT = Decimal(940), Decimal(220)
_INCOME_SLOTS = {group: n for n, group in enumerate(income.GROUPS, 1)}
_OTHERS_SLOT = 7  # the last colour of the palette: the categories not shown on their own
_LABELS = 12  # periods named under the bars, at most


@dataclass(frozen=True, slots=True)
class _Block:
    part: str
    slot: int
    value: Decimal
    y: Decimal
    height: Decimal


@dataclass(frozen=True, slots=True)
class _Column:
    name: str  # the period's
    total: Decimal
    share: Decimal | None  # where the line passes, if one is drawn
    x: Decimal
    width: Decimal
    blocks: tuple[_Block, ...]
    named: bool  # its period is written under it
    minor: bool  # one of every other period written: left out on a phone


@dataclass(frozen=True, slots=True)
class _Bars:
    columns: tuple[_Column, ...]
    top: Decimal  # the amount at the top of the plot
    zero: Decimal  # where zero is drawn
    shares: tuple[str, ...]  # the line: the points of each unbroken run


def _bars(
    periods: Sequence[tuple[str, Mapping[str, Decimal], Decimal | None]],
    slots: Mapping[str, int],
) -> _Bars:
    """Each period (its name, its parts, a share) a column of its parts from zero up, and
    what is below zero hanging under it; the shares a line across, 0 at the bottom of the
    plot and 100% at its top."""
    highs = [sum((v for v in parts.values() if v > 0), Decimal(0)) for _, parts, _ in periods]
    lows = [sum((v for v in parts.values() if v < 0), Decimal(0)) for _, parts, _ in periods]
    top, bottom = max(highs, default=Decimal(0)), min(lows, default=Decimal(0))
    scale = _HEIGHT / ((top - bottom) or 1)
    step = _WIDTH / max(len(periods), 1)
    every = -(-len(periods) // _LABELS)  # name one period in so many

    def y(amount: Decimal) -> Decimal:
        return ((top - amount) * scale).quantize(Decimal("0.1"))

    columns: list[_Column] = []
    shares: list[str] = []
    run: list[str] = []  # the points of the line's run so far
    for n, (name, parts, share) in enumerate(periods):
        blocks: list[_Block] = []
        up = down = Decimal(0)
        for part, value in parts.items():
            start = up if value >= 0 else down
            end = start + value
            blocks.append(
                _Block(part, slots[part], value, y(max(start, end)), abs(y(end) - y(start)))
            )
            up, down = (end, down) if value >= 0 else (up, end)
        x = (step * n + step * Decimal("0.15")).quantize(Decimal("0.1"))
        width = (step * Decimal("0.7")).quantize(Decimal("0.1"))
        total = sum(parts.values(), Decimal(0))
        # Counted from the newest, which is always named.
        named = (len(periods) - 1 - n) % every == 0
        minor = named and (len(periods) - 1 - n) // every % 2 == 1
        columns.append(_Column(name, total, share, x, width, tuple(blocks), named, minor))
        if share is None:
            if run:
                shares.append(" ".join(run))
            run = []
        else:
            middle = (step * n + step / 2).quantize(Decimal("0.1"))
            run.append(f"{middle},{((1 - share) * _HEIGHT).quantize(Decimal('0.1'))}")
    if run:
        shares.append(" ".join(run))
    return _Bars(tuple(columns), top, y(Decimal(0)), tuple(shares))


def _spending_slots(parts: Sequence[str]) -> dict[str, int]:
    """The largest categories in the palette's order, the others in its last colour, what
    was paid back in the colour of the text."""
    special = {spending.OTHERS: _OTHERS_SLOT, spending.PAID_BACK: 0}
    return {part: special.get(part, n) for n, part in enumerate(parts, 1)}


def _slices(values: Sequence[tuple[str, Decimal]]) -> tuple[list[_Slice], list[_Slice]]:
    """The parts above zero as slices of a ring, each its share of their sum, and those
    below zero, which a ring cannot show."""
    above = [(name, value) for name, value in values if value > 0]
    whole = sum((value for _, value in above), Decimal(0))
    shares = portfolio.shared_out([value * 100 / whole for _, value in above], _TENTH)
    slices = [
        _Slice(name, value, share) for (name, value), share in zip(above, shares, strict=True)
    ]
    return slices, [_Slice(name, value, None) for name, value in values if value < 0]


def _spent_parts(spent: spending.Spending) -> list[tuple[str, Decimal]]:
    """What the ring of spending shows: its largest categories, then the others together;
    what was paid back is below zero."""
    largest = {name for name in spent.parts if name not in (spending.OTHERS, spending.PAID_BACK)}
    parts = [(c.name, c.spent) for c in spent.categories if c.name in largest]
    rest = [c for c in spent.categories if c.name not in largest]
    if rest:
        parts.append((spending.OTHERS, sum((c.spent for c in rest), Decimal(0))))
    if spent.paid_back:
        parts.append((spending.PAID_BACK, -spent.paid_back))
    return parts


def _income_parts(earned: income.Income) -> list[tuple[str, Decimal]]:
    totals = dict.fromkeys(income.GROUPS, Decimal(0))
    for source in earned.sources:
        totals[source.group] += source.total
    return [(group, total) for group, total in totals.items() if total]


def _analysis(request: Request) -> Response:
    asked = forms.analysis_view(_viewed(request))
    errors = list(asked.errors)
    with db.reading(request.app.state.database) as conn:
        people = query.people(conn)
        shown = wire.unless_rate_missing(
            errors,
            lambda: report.run(
                conn,
                first=asked.first,
                last=asked.last,
                owner=asked.owner,
                grouping=asked.grouping,
                currency=asked.currency,
            ),
            None,
        )
        label = store.category_labels(conn)
    context: dict[str, object] = {
        "people": people,
        "owner": asked.owner,
        "asked": asked,
        "label": label,
        "errors": errors,
        "given": request.query_params,
        "groupings": list(cashflow.Grouping),
        "sides": (
            (cashflow.INCOME, "Income"),
            (cashflow.EXPENSES, "Expenses"),
            (cashflow.INVESTING, "Investing"),
        ),
        "shown": shown,
        "plot": {"width": _WIDTH, "height": _HEIGHT},
    }
    if shown:
        earned, spent = shown.income, shown.spending
        slots = _spending_slots(spent.parts)
        spent_ring, spent_out = _slices(_spent_parts(spent))
        income_ring, income_out = _slices(_income_parts(earned))
        context |= {
            "spent_slots": slots,
            "spent_bars": _bars([(p.name, p.parts, None) for p in spent.periods], slots),
            "spent_ring": _ring(spent_ring, slots),
            "spent_out": spent_out,
            "categories": {category.name: category for category in spent.categories},
            "slots": _INCOME_SLOTS,
            "bars": _bars([(p.name, p.groups, p.employer) for p in earned.periods], _INCOME_SLOTS),
            "income_ring": _ring(income_ring, _INCOME_SLOTS),
            "income_out": income_out,
        }
    return _templates.TemplateResponse(request, "analysis.html", context)


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


def _logo(request: Request) -> Response:
    """The logo of a symbol, as it was kept: the image and nothing else."""
    with db.reading(request.app.state.database) as conn:
        logo = store.logo(conn, request.path_params["symbol"])
    if logo is None:
        raise HTTPException(404)
    return Response(logo.image, media_type=logo.media_type, headers=_LOGO_HEADERS)


async def _set_logo(request: Request) -> Response:
    """Keep the image sent as the logo of a holding that has none."""
    _own_origin(request)
    async with request.form(max_files=1) as form:
        token = str(form.get("token", ""))
        symbol = str(form.get("symbol", ""))
        part = form.get("file")
        image = await part.read() if isinstance(part, UploadFile) else b""
        name = Path(part.filename).name if isinstance(part, UploadFile) and part.filename else ""
    _own_token(request, token)
    state = request.app.state

    def keep() -> str:
        conn = db.connect(state.database)
        try:
            store_logos.add(conn, symbol, image, name, state.clock())
        except store_logos.LogoError as exc:
            return f"No logo was kept: {exc}."
        finally:
            conn.close()
        return ""

    error = await run_in_threadpool(keep)
    if error:
        return await run_in_threadpool(_holdings, request, error, 409)
    return RedirectResponse(
        str(
            request.url_for("holdings").include_query_params(
                **{key: value for key, value in request.query_params.items() if key == "owner"}
            )
        ),
        303,
    )


def _transactions(request: Request) -> Response:
    parsed = forms.transactions_filter(request.query_params)
    wanted, errors = parsed.wanted, list(parsed.errors)
    with db.reading(request.app.state.database) as conn:
        query.check_names(conn, query.Filter(owner=wanted.owner))
        try:
            result = query.run(conn, wanted)
        except query.QueryError as exc:  # an account or category the form no longer offers
            errors.append(wire.sentence(exc))
            result = query.Result((), (), (), ())
        people = query.people(conn)
        choices = query.choices(conn, wanted.owner)
        category = wanted.category and query.category_key(conn, wanted.category)
    newest_first = result.rows[::-1]
    pages = max(1, -(-len(newest_first) // _PAGE))
    asked = request.query_params.get("page", "")
    # a number too long to read is past the last page, as any number past it is
    page = pages if len(asked) > _DIGITS and asked.isdecimal() else min(_whole(asked) or 1, pages)
    context = {
        "people": people,
        "owner": wanted.owner,
        "wanted": wanted,
        "given": request.query_params,
        "errors": errors,
        "choices": choices,
        "label": choices.label,
        "category": category,
        "kinds": list(Kind),
        "result": result,
        "rows": newest_first[(page - 1) * _PAGE : page * _PAGE],
        "page": page,
        "pages": pages,
    }
    return _templates.TemplateResponse(request, "transactions.html", context)


def _csv_cell(column: str, value: object) -> object:
    """A spreadsheet runs a cell of statement text that begins like a formula (the payer
    types the reference), so one quote before it keeps it text; amounts stay numbers."""
    if column in _CSV_TEXT and isinstance(value, str) and value.startswith(_FORMULA):
        return "'" + value
    return value


def _csv_value(row: query.Row, column: str, label: categories.Labels) -> object:
    """A row's value in `column`, a category by its label, as people read it."""
    value = getattr(row, column)
    return label(value) if column in _CSV_CATEGORY and value else value


def _transactions_csv(request: Request) -> Response:
    """Every row the same address shows as a page, with its exact amount."""
    parsed = forms.transactions_filter(request.query_params)
    if parsed.errors:
        return PlainTextResponse("\n".join(parsed.errors) + "\n", status_code=400)
    with db.reading(request.app.state.database) as conn:
        rows = query.run(conn, parsed.wanted).rows
        label = store.category_labels(conn)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(_CSV)
    writer.writerows(
        [_csv_cell(column, _csv_value(row, column, label)) for column in _CSV] for row in rows
    )
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
        return _login_form(request, then, wire.sentence(exc), 429)
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


def _unknown_name(request: Request, exc: Exception) -> Response:
    if request.url.path.startswith(_API):
        return JSONResponse({"errors": [wire.sentence(exc)]}, 404)
    return PlainTextResponse(f"{exc}\n", status_code=404)
