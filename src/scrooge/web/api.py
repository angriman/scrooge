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
from collections.abc import Callable
from datetime import date
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from scrooge import (
    analysis,
    categories,
    classification,
    context,
    marketdata,
    months,
    networth,
    overview,
    payouts,
    portfolio,
    query,
    recap,
    recurring,
    report,
    staging,
)
from scrooge.classification import UnknownMaturityError
from scrooge.errors import ConfigError
from scrooge.models import Kind
from scrooge.prices import PriceError
from scrooge.storage import db, originals, store
from scrooge.validation import ReconciliationError
from scrooge.web import forms, wire
from scrooge.web import uploads as uploads_

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
    """What every screen needs: who there is and up to which day each one's statements are
    all in, what the figures can be shown in, and what this app lets a browser do. `token` goes
    with every change sent, once there are any."""
    state = request.app.state
    with db.reading(state.database) as conn:
        people = query.people(conn)
        lines = months.coverage(conn)
    return JSONResponse(
        {
            "people": [
                wire.person(p, months.covered([line for line in lines if line.owner == p.name]))
                for p in people
            ],
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


async def _sent(request: Request) -> dict[str, object] | None:
    """The JSON object a change sends, once it is known to come from these pages (`_writable`
    with its `X-Token`); None when it is no JSON object."""
    _writable(request, request.headers.get("x-token", ""))
    try:
        sent = await request.json()
    except ValueError:
        return None
    return sent if isinstance(sent, dict) else None


async def _categorize(request: Request) -> Response:
    """Give committed rows of one person another category, `{"owner", "ids", "category",
    "remember"}`: remembered as a rule for the next imports when asked and when rules are
    kept."""
    sent = await _sent(request)
    listed = None if sent is None else sent.get("ids")
    if sent is None or not isinstance(listed, list):
        return _refused(("Send the owner, the ids of the rows and the category as JSON.",))
    state = request.app.state
    owner, category = str(sent.get("owner", "")), str(sent.get("category", ""))
    ids = [str(row) for row in listed]
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


def _importable(request: Request) -> list[str]:
    """Who statements can be imported for: the people the config names. Nobody over a copy."""
    imports = request.app.state.imports
    if imports is None:
        raise HTTPException(404)
    return list(imports.people)


def _staged(request: Request) -> Response:
    """What is staged for one person (the first one importable without `owner`), what a row
    can be given, and what this app can do about it."""
    state = request.app.state
    owners = _importable(request)
    owner = request.query_params.get("owner") or next(iter(owners), "")
    if owner not in owners:
        return _refused((f"{owner} is nobody the config names.",))
    with db.reading(state.database) as conn:
        staged = months.draft(conn, owner)
        found = query.choices(conn, owner)
    return JSONResponse(
        {
            "owners": owners,
            "owner": owner,
            **wire.draft(staged),
            "choices": wire.choices(found),
            # What a new expense category can go under.
            "under": list(found.groups),
            "rules": state.rules is not None,
            "lookup": state.maturities is not None,
            "errors": [],
        }
    )


def _undated(
    request: Request, owner: str, uploads: list[tuple[str, bytes]], bills: tuple[str, ...]
) -> wire.JSON:
    state = request.app.state
    held = uploads_.Held(owner, uploads, bills, state.clock() + uploads_.HELD)
    return {"bills": list(bills), "held": uploads_.hold(state, held) if state.maturities else None}


async def _stage(
    request: Request, owner: str, uploads: list[tuple[str, bytes]], notice: str | None = None
) -> Response:
    """Stage `uploads` for `owner`, and say what became of each file."""
    state = request.app.state
    try:
        done = await run_in_threadpool(uploads_.stage, state, owner, uploads)
    except UnknownMaturityError as exc:
        return JSONResponse(
            {
                "errors": [f"Nothing was imported: {wire.sentence(exc)}"],
                "undated": _undated(request, owner, uploads, exc.bills),
                "notice": notice,
            },
            409,
        )
    except uploads_.REFUSED as exc:
        return JSONResponse(
            {"errors": [f"Nothing was imported: {wire.sentence(exc)}"], "notice": notice}, 409
        )
    return JSONResponse({"imported": wire.imported(done), "notice": notice, "errors": []})


async def _upload(request: Request) -> Response:
    """Stage the statements sent (`file`, as many as there are) for one person (`owner`):
    a file that fails stores nothing of itself and stops no other."""
    _writable(request, request.headers.get("x-token", ""))
    owners = _importable(request)
    async with request.form(max_files=uploads_.UPLOADS) as form:
        owner = str(form.get("owner", ""))
        sent = [part for part in form.getlist("file") if isinstance(part, UploadFile)]
        uploads = [
            (uploads_.name(part.filename), await part.read()) for part in sent if part.filename
        ]
    if owner not in owners:
        return _refused((f"{owner or 'Nobody'} is nobody the config names.",))
    if not uploads:
        return _refused(("Choose at least one file.",))
    return await _stage(request, owner, uploads)


async def _look_up(request: Request) -> Response:
    """Look up when the bills an import stopped on mature, write it down, and import the same
    files again: `{"held"}`, the key the import gave."""
    sent = await _sent(request)
    state = request.app.state
    _importable(request)
    if state.maturities is None:
        raise HTTPException(404)
    key = str((sent or {}).get("held", ""))
    held = state.held.get(key)
    if held is None or held.until <= state.clock():
        return JSONResponse(
            {"errors": ["The files of that import are no longer kept: choose them again."]}, 409
        )
    # What failed keeps the files, and the button that tries again.
    again = {"bills": list(held.bills), "held": key}
    try:
        looked = await run_in_threadpool(marketdata.maturities, state.maturities, held.bills)
    except PriceError as exc:
        message = f"TreasuryDirect could not tell: {wire.sentence(exc)} Try again later, or add "
        return JSONResponse(
            {"errors": [message + "the maturity to instruments.yaml."], "undated": again}, 502
        )
    if looked.unknown:
        unknown = ", ".join(looked.unknown)
        message = f"TreasuryDirect knows no bill {unknown}: add its maturity to instruments.yaml."
        return JSONResponse({"errors": [message], "undated": again}, 409)
    try:
        classification.add_maturities(state.imports.instruments, looked.found)
    except ConfigError as exc:
        return JSONResponse({"errors": [wire.sentence(exc)], "undated": again}, 409)
    state.held.pop(key, None)
    days = "; ".join(f"{bill} matures on {day}" for bill, day in looked.found.items())
    return await _stage(
        request, held.owner, held.uploads, f"{days} (TreasuryDirect), written in instruments.yaml."
    )


class _Refused(Exception):
    """What was sent cannot be done; its message says why."""


def _choices(sent: dict[str, object], staged: months.Draft) -> list[staging.Choice]:
    """The categories chosen for staged rows, `[{"ids", "category", "rule"}]`, as choices that
    give them: rows without one are given it, those with one have it changed."""
    rows = {row.id: row for row in staged.rows}
    chosen = sent.get("choices", [])
    if not isinstance(chosen, list) or not all(isinstance(c, dict) for c in chosen):
        raise _Refused("Send the choices as a list of rows and their category.")
    made: list[staging.Choice] = []
    for choice in chosen:
        listed = choice.get("ids")
        ids = [str(row) for row in listed] if isinstance(listed, list) else []
        if not ids or any(row not in rows for row in ids):
            raise _Refused("Those rows are not staged any more: load the page again.")
        category, rule = str(choice.get("category", "")), choice.get("rule") is True
        waiting = [row for row in ids if rows[row].waiting]
        having = [row for row in ids if not rows[row].waiting and rows[row].category != category]
        if waiting:
            made.append(staging.Choice(waiting, category, rule=rule))
        if having:
            made.append(staging.Choice(having, category, rule=rule, change=True))
    return made


async def _categorize_staged(request: Request) -> Response:
    """Give the categories chosen to staged rows of one person, all of them or none, and with
    `"commit": true` then commit everything staged for that person:
    `{"owner", "choices": [{"ids", "category", "rule"}], "commit"}`."""
    sent = await _sent(request)
    owners = _importable(request)
    state = request.app.state
    if sent is None or str(sent.get("owner", "")) not in owners:
        return _refused(("Send whose rows they are and the categories chosen, as JSON.",))
    owner = str(sent["owner"])

    def apply() -> tuple[int, int | None]:
        conn = db.connect(state.database)
        try:
            made = _choices(sent, months.draft(conn, owner))
            remember = None if state.rules is None else state.rules.of(owner)
            changed = (
                staging.categorize_all(
                    conn, owner=owner, choices=made, now=state.clock(), remember=remember
                )
                if made
                else 0
            )
            if sent.get("commit") is not True:
                return changed, None
            files, _ = staging.commit_with_copy(
                conn, state.database, owner=owner, now=state.clock()
            )
            return changed, len(files)
        finally:
            conn.close()

    try:
        changed, committed = await run_in_threadpool(apply)
    except _Refused as exc:
        return JSONResponse({"errors": [str(exc)]}, 409)
    except (staging.UncategorizedRowsError, ReconciliationError) as exc:
        return JSONResponse({"errors": [f"Not committed: {wire.sentence(exc)}"]}, 409)
    except (staging.StagingError, store.UnknownCategoryError) as exc:
        return JSONResponse({"errors": [f"No category was set: {wire.sentence(exc)}"]}, 409)
    return JSONResponse({"changed": changed, "committed": committed, "errors": []})


async def _add_category(request: Request) -> Response:
    """One more category in the taxonomy, for every import from now on: `{"name", "side",
    "group"}`, an expense (`uscite`) under `group` or a group of its own, or income
    (`entrate`)."""
    sent = await _sent(request)
    state = request.app.state
    _importable(request)
    if state.rules is None:  # nowhere to write it down for the next import
        raise HTTPException(404)
    if sent is None:
        return _refused(("Send the name of the category and where it goes, as JSON.",))

    def add() -> str:
        conn = db.connect(state.database)
        try:
            added = staging.add_category(
                conn,
                state.rules.shared.with_name(categories.FILE),
                name=str(sent.get("name", "")),
                side=str(sent.get("side", "")),
                group=str(sent.get("group", "")),
            )
        finally:
            conn.close()
        return added.name

    try:
        name = await run_in_threadpool(add)
    except (staging.StagingError, ConfigError) as exc:
        return JSONResponse({"errors": [f"No category was added: {wire.sentence(exc)}"]}, 409)
    return JSONResponse({"name": name, "errors": []})


async def _remove(request: Request, action: Callable[..., object]) -> Response:
    """Remove a staged statement (`staging.discard`) or take back a committed one
    (`staging.uncommit`), which stages it again; says whose it was, for the import page."""
    await _sent(request)
    state = request.app.state
    number = request.path_params["number"]
    if number >= 2**63:  # no id SQLite holds
        return JSONResponse({"errors": ["There is no such statement."]}, 404)

    def act() -> str | None:
        with db.reading(state.database) as conn:
            statement = months.statement(conn, number)
        if statement is None:
            return None
        conn = db.connect(state.database)
        try:
            action(conn, owner=statement.owner, file_id=statement.id)
        finally:
            conn.close()
        return statement.owner

    try:
        owner = await run_in_threadpool(act)
    except staging.StagingError as exc:
        return JSONResponse({"errors": [f"Nothing was changed: {wire.sentence(exc)}"]}, 409)
    if owner is None:
        return JSONResponse({"errors": ["There is no such statement."]}, 404)
    return JSONResponse({"owner": owner, "errors": []})


def _ranges(through: date, start: int) -> list[dict[str, str]]:
    """The ranges offered, as `from` and `to` take them: the year up to the last day committed,
    the twelve months before it, each earlier year of the statements (two at most) and all of
    them, when they go back before this year."""
    twelve = analysis.add_months(through, -11)
    years = range(through.year - 1, max(start, through.year - 2) - 1, -1)
    return [
        {"label": "Year to date", "from": str(through.year), "to": ""},
        {"label": "Last 12 months", "from": f"{twelve:%Y-%m}", "to": ""},
        *({"label": str(year), "from": str(year), "to": str(year)} for year in years),
        *([{"label": "All", "from": str(start), "to": ""}] if start < through.year else []),
    ]


def _cashflow(request: Request) -> Response:
    """The cash flow of `from` to `to`, by `by` (month when left out), in `currency`: the year
    to date when no range is asked for."""
    asked = forms.analysis_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    errors: list[str] = []
    with db.reading(request.app.state.database) as conn:
        try:
            query.check_names(conn, query.Filter(owner=asked.owner))
        except query.QueryError as exc:
            return _refused((wire.sentence(exc),))
        through = analysis.through(conn, asked.owner)
        firsts = [p.first for p in query.people(conn) if p.first and asked.owner in (None, p.name)]
        first, last = asked.first, asked.last
        if first is None and last is None and through is not None:
            first = date(through.year, 1, 1)
        shown = wire.unless_rate_missing(
            errors,
            lambda: report.run(
                conn,
                first=first,
                last=last,
                owner=asked.owner,
                grouping=asked.grouping,
                currency=asked.currency,
            ),
            None,
        )
    return JSONResponse(
        {
            "report": None if shown is None else wire.cashflow_report(shown),
            "first": wire.day(first),
            "last": wire.day(last),
            "by": asked.grouping.value,
            "ranges": [] if through is None else _ranges(through, min(firsts).year),
            "errors": errors,
        }
    )


def _income(request: Request) -> Response:
    """What investments paid from `from` to `to`, in `currency`: the twelve months up to the
    last day committed when no range is asked for."""
    asked = forms.analysis_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    errors: list[str] = []
    with db.reading(request.app.state.database) as conn:
        try:
            query.check_names(conn, query.Filter(owner=asked.owner))
        except query.QueryError as exc:
            return _refused((wire.sentence(exc),))
        through = analysis.through(conn, asked.owner)
        firsts = [p.first for p in query.people(conn) if p.first and asked.owner in (None, p.name)]
        shown = wire.unless_rate_missing(
            errors,
            lambda: payouts.run(
                conn,
                first=asked.first,
                last=asked.last,
                owner=asked.owner,
                currency=asked.currency,
            ),
            None,
        )
        found = logos(request, conn)
    return JSONResponse(
        {
            "income": None if shown is None else wire.payouts_report(shown),
            "logos": found,
            "ranges": [] if through is None else _ranges(through, min(firsts).year),
            "errors": errors,
        }
    )


def _recurring(request: Request) -> Response:
    """The payments that come back at a steady pace for `owner`, or for everyone, and what
    they cost a month in `currency`."""
    asked = forms.analysis_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    with db.reading(request.app.state.database) as conn:
        try:
            query.check_names(conn, query.Filter(owner=asked.owner))
        except query.QueryError as exc:
            return _refused((wire.sentence(exc),))
        found = recurring.run(conn, owner=asked.owner, currency=asked.currency)
    return JSONResponse(wire.recurring_payments(found))


def _transactions_context(request: Request) -> Response:
    """Exactly one `category`, `merchant` or `account` of `owner`, month by month in
    `currency`: the last months up to the one of the last day committed, the usual month
    before it, and the merchants of a category or what an account holds now."""
    asked = forms.context_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    errors: list[str] = []
    empty = context.Context(asked.kind, asked.name, asked.currency, False, (), None, None, None)
    with db.reading(request.app.state.database) as conn:
        try:
            shown = wire.unless_rate_missing(
                errors,
                lambda: context.run(
                    conn, asked.kind, asked.name, owner=asked.owner, currency=asked.currency
                ),
                empty,
            )
        except query.QueryError as exc:
            return _refused((wire.sentence(exc),))
        held = None
        if asked.kind == "account":
            held = wire.unless_rate_missing(
                errors,
                lambda: context.balance(
                    conn, asked.name, owner=asked.owner, currency=asked.currency
                ),
                None,
            )
    return JSONResponse({**wire.transactions_context(shown, held), "errors": errors})


async def _discard(request: Request) -> Response:
    return await _remove(request, staging.discard)


async def _uncommit(request: Request) -> Response:
    return await _remove(request, staging.uncommit)


def _networth_months(request: Request) -> Response:
    """Net worth at the end of every month for `owner`, newest first, in each currency of the
    config's `currencies`, and in `currency` too when it is not one of them."""
    asked = forms.networth_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    configured: tuple[str, ...] = request.app.state.currencies
    currencies = [*configured, *(() if asked.currency in configured else (asked.currency,))]
    errors: list[str] = []
    with db.reading(request.app.state.database) as conn:
        valued = networth.value(conn, asked.owner)
        ends = wire.unless_rate_missing(
            errors, lambda: networth.month_ends(conn, valued, currencies), ()
        )
    return JSONResponse(
        {
            "currencies": currencies,
            "months": [wire.month_end(end) for end in ends],
            "errors": errors,
        }
    )


routes = [
    Route("/session", _session, name="api_session"),
    Route("/overview", _overview, name="api_overview"),
    Route("/networth", _networth, name="api_networth"),
    Route("/networth/months", _networth_months, name="api_networth_months"),
    Route("/month", _month, name="api_month"),
    Route("/attention", _attention, name="api_attention"),
    Route("/holdings", _holdings, name="api_holdings"),
    Route("/statements", _statements, name="api_statements"),
    Route("/transactions", _transactions, name="api_transactions"),
    Route("/transactions/category", _categorize, methods=["POST"], name="api_categorize"),
    Route("/transactions/context", _transactions_context, name="api_transactions_context"),
    Route("/cashflow", _cashflow, name="api_cashflow"),
    Route("/income", _income, name="api_income"),
    Route("/recurring", _recurring, name="api_recurring"),
    Route("/import", _staged, name="api_staged"),
    Route("/import", _upload, methods=["POST"], name="api_upload"),
    Route("/import/maturities", _look_up, methods=["POST"], name="api_look_up"),
    Route("/import/categories", _categorize_staged, methods=["POST"], name="api_categorize_staged"),
    Route("/import/category", _add_category, methods=["POST"], name="api_add_category"),
    Route("/statements/{number:int}/discard", _discard, methods=["POST"], name="api_discard"),
    Route("/statements/{number:int}/uncommit", _uncommit, methods=["POST"], name="api_uncommit"),
]
