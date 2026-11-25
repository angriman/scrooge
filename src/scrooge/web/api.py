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

import contextlib
import secrets
import sqlite3
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from urllib.parse import urlsplit

import yaml
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import State, UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from scrooge import (
    analysis,
    attention,
    categories,
    classification,
    closing,
    context,
    costs,
    holding_returns,
    holds,
    marketdata,
    merchants,
    months,
    networth,
    overview,
    payouts,
    pensions,
    performance,
    portfolio,
    query,
    recap,
    recurring,
    report,
    staging,
    suggestions,
    together,
    typed,
)
from scrooge.classification import UnknownMaturityError
from scrooge.errors import ConfigError
from scrooge.models import Kind, TypedHolding
from scrooge.storage import db, originals, store
from scrooge.storage import logos as store_logos
from scrooge.validation import ReconciliationError
from scrooge.web import forms, wire
from scrooge.web import uploads as uploads_

_DIGEST = 12  # how much of a logo's digest its address carries, to change when the image does
LANGUAGE_COOKIE = "scrooge_language"


def language_of(request: Request) -> str:
    """The UI language the request asks for: query `lang`, then cookie, else English."""
    asked = request.query_params.get("lang") or request.cookies.get(LANGUAGE_COOKIE) or ""
    asked = asked.strip().lower()
    return asked if asked in categories.LABELS else categories.LANGUAGE


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
        lines = together.lines(conn)
    return JSONResponse(
        {
            "people": [
                wire.person(p, months.covered([line for line in lines if line.owner == p.name]))
                for p in people
            ],
            "currencies": list(networth.CURRENCIES),
            "currency": state.currency,
            "languages": list(categories.LANGUAGES),
            "language": language_of(request),
            "read_only": state.read_only,
            "signing": state.access is not None,
            # Whether a category chosen can be remembered as a rule for the next import.
            "rules": state.rules is not None,
            # Whether missing closes, splits and exchange rates can be fetched from here.
            "prices": state.price_source is not None and not state.read_only,
            # Whose figures can be typed (`/api/accounts/typed`): the people the config names,
            # none over a copy or without the import page.
            "typing": [] if state.imports is None else list(state.imports.people),
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
            lambda: overview.run(conn, owner=asked.owner, currency=asked.currency),
            None,
        )
        checks = wire.unless_rate_missing(
            errors, lambda: networth.checks(conn, owner=asked.owner), ()
        )
    return JSONResponse(
        {
            "overview": None if shown is None else wire.summary(shown),
            "checks": [wire.check(check) for check in checks],
            "errors": errors,
        }
    )


def _networth(request: Request) -> Response:
    """Net worth day by day for `owner` in `currency`, cut by `by` (total, asset, owner or
    liquidity)."""
    asked = forms.networth_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    frozen: tuple[networth.FrozenAccount, ...] = request.app.state.frozen
    errors: list[str] = []
    with db.reading(request.app.state.database) as conn:
        if asked.by == "liquidity":
            errors += networth.frozen_notes(conn, frozen)
        worth = wire.unless_rate_missing(
            errors,
            lambda: networth.run(conn, owner=asked.owner, currency=asked.currency, frozen=frozen),
            None,
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
        label = store.category_labels(conn, language=language_of(request))
    month = None if shown is None else wire.month(shown, label)
    return JSONResponse({"month": month, "errors": []})


def _attention(request: Request) -> Response:
    """What waits for `owner`, or for everyone: statements staged and not committed, accounts
    whose statements end well before the person's others, lines of statements whose figures
    differ from those worked out here, rows still in the category of those no rule matched,
    and, with `recurring=1`, recurring payments that went up or started lately, in
    `currency`: they read every committed row, which only the first page needs."""
    asked = forms.holdings_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    owner = asked.owner
    errors: list[str] = []
    with db.reading(request.app.state.database) as conn:
        people = [p for p in query.people(conn) if owner in (None, p.name)]
        lines = together.lines(conn, owner)
        checks = wire.unless_rate_missing(errors, lambda: networth.checks(conn, owner=owner), ())
        unmatched: str = request.app.state.unmatched
        unsorted = attention.uncategorised(conn, owner, unmatched)
        label = store.category_labels(conn, language=language_of(request))
        changed = (
            attention.changes(conn, owner=owner, currency=asked.currency)
            if request.query_params.get("recurring") == "1"
            else None
        )
    errors += [] if changed is None else [wire.sentence(exc) for exc in changed.errors]
    return JSONResponse(
        {
            **wire.needs_a_look(asked.currency, unmatched, unsorted, changed, label),
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
    """What `owner`, or everyone, holds, in `currency`, and what each holding returned (one
    valuation serves both): `holdings` is null when nothing is committed, or a rate is missing
    (`errors` says which)."""
    asked = forms.holdings_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    today = request.app.state.clock().date()
    with db.reading(request.app.state.database) as conn:
        try:
            valued = networth.value(conn, asked.owner)
            # The 3a accounts are in the pensions (`/api/pensions`), one figure each.
            held = portfolio.positions_of(conn, valued, asked.currency, today, pensions=False)
            returned = holding_returns.run(conn, valued, held)
        except networth.MissingRateError as exc:
            return JSONResponse({"holdings": None, "logos": {}, "errors": [wire.sentence(exc)]})
        found = logos(request, conn)
    shown = wire.holdings(held, returned) if held.day else None
    return JSONResponse({"holdings": shown, "logos": found, "errors": []})


def _logos_now(request: Request) -> Response:
    with db.reading(request.app.state.database) as conn:
        found = logos(request, conn)
    return JSONResponse({"logos": found, "errors": []})


async def _set_logo(request: Request) -> Response:
    """Keep the image sent (`file`) as the logo of the instrument `symbol`, in place of any it
    had, and send every logo as it then is. Nothing is fetched: the image is the user's own."""
    _writable(request, request.headers.get("x-token", ""))
    try:
        async with request.form(max_files=1) as form:
            symbol = str(form.get("symbol", ""))
            part = form.get("file")
            image = await part.read() if isinstance(part, UploadFile) else b""
            name = (
                uploads_.name(part.filename)
                if isinstance(part, UploadFile) and part.filename
                else ""
            )
    except HTTPException as exc:
        return _refused((f"No logo was kept: {exc.detail}",))
    state = request.app.state

    def keep() -> None:
        conn = db.connect(state.database)
        try:
            store_logos.add(conn, symbol, image, name, state.clock())
        finally:
            conn.close()

    try:
        await run_in_threadpool(keep)
    except store_logos.LogoError as exc:
        return JSONResponse({"errors": [f"No logo was kept: {exc}."]}, 409)
    return _logos_now(request)


async def _remove_logo(request: Request) -> Response:
    """Forget the logo of the instrument `{"symbol"}`, and send every logo as it then is."""
    sent = await _sent(request)
    if sent is None:
        return _refused(('Send {"symbol": ...} as JSON.',))
    symbol = str(sent.get("symbol", ""))
    state = request.app.state

    def forget() -> None:
        conn = db.connect(state.database)
        try:
            store_logos.remove(conn, symbol)
        finally:
            conn.close()

    try:
        await run_in_threadpool(forget)
    except store_logos.LogoError as exc:
        return JSONResponse({"errors": [wire.sentence(exc)]}, 409)
    return _logos_now(request)


def _statements(request: Request) -> Response:
    """Every account a source feeds, of `owner` or of everyone: how far its statements go, what
    it holds now in `currency` (its cash by currency, its securities, its last month-ends:
    `holds`), the runs of days they cover and the statements themselves; the
    day every monthly account is covered to; and the statements' own balances against those
    worked out here. A broker's accounts in each currency are one account (`together`), with
    the names they are booked under (`parts`)."""
    asked = forms.holdings_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    state = request.app.state
    folder = originals.folder_of(state.database)
    errors: list[str] = []
    with db.reading(state.database) as conn:
        query.check_names(conn, query.Filter(owner=asked.owner))
        try:
            held = holds.of(conn, networth.value(conn, asked.owner), asked.currency)
            checks = networth.checks(conn, owner=asked.owner)
        except networth.MissingRateError as exc:
            errors.append(wire.sentence(exc))
            held, checks = {}, ()
        # A broker's currencies are one account here, and judged as one (`together`).
        rows = together.rows(conn, asked.owner, held)

    def listed(statement: months.Statement) -> wire.JSON:
        kept = originals.find(folder, statement.sha256, statement.filename)
        address = str(request.url_for("original", number=statement.id)) if kept else None
        return wire.statement(statement, address)

    def row(one: together.Row, late: bool) -> wire.JSON:
        shown = wire.statements_account(
            one.line,
            late,
            None if one.held is None else one.held.balance,
            one.spans,
            [listed(s) for s in one.statements],
        )
        return {**shown, "holds": wire.account_holds(one.held), "parts": list(one.parts)}

    lines = tuple(one.line for one in rows)
    accounts = [row(one, late) for one, late in zip(rows, months.behind(lines), strict=True)]
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


def _originals(
    request: Request, conn: sqlite3.Connection, numbers: Iterable[int | None]
) -> dict[int, str]:
    """Statement id -> the address its file is downloaded from, for those of `numbers` whose
    file is kept beside the database."""
    folder = originals.folder_of(request.app.state.database)
    kept: dict[int, str] = {}
    for number in {number for number in numbers if number is not None}:
        statement = months.statement(conn, number)
        if statement and originals.find(folder, statement.sha256, statement.filename):
            kept[number] = str(request.url_for("original", number=number))
    return kept


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
        found = query.choices(conn, wanted.owner, language=language_of(request))
        ranks = store.category_ranks(conn)
        offset = _count(params, "offset", 0, len(result.rows))
        limit = _count(params, "limit", _PAGE, _MOST)
        newest = result.rows[::-1][offset : offset + limit]
        kept = _originals(request, conn, (row.file for row in newest))
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
                    "key": c.name,
                    "name": found.label(c.name),
                    "currency": c.currency,
                    "count": c.count,
                    "total": wire.number(c.total),
                }
                for c in query.by_category(result.rows, ranks)
            ],
            "rows": [
                wire.transaction(
                    row,
                    every.get(row.id),
                    found.label,
                    None if row.file is None else kept.get(row.file),
                )
                for row in newest
            ],
            "choices": wire.choices(found),
            "kinds": [kind.value for kind in Kind],
            "errors": [],
        }
    )


def _origin_and_token(request: Request, token: str) -> None:
    """Refuse a request sent from another site or without the token of these pages."""
    state = request.app.state
    origin = request.headers.get("origin", "null")
    if origin != "null" and urlsplit(origin).hostname not in state.hosts:
        raise HTTPException(403)
    if not secrets.compare_digest(token.encode(), state.token.encode()):
        raise HTTPException(403)


def _writable(request: Request, token: str) -> None:
    """Refuse a change sent from another site, without the token of these pages, or to a copy
    that is only shown here."""
    _origin_and_token(request, token)
    if request.app.state.read_only:
        raise HTTPException(403)


async def _choose_language(request: Request) -> Response:
    """Remember the UI language chosen in a browser cookie (`{"language": "it"}`)."""
    _origin_and_token(request, request.headers.get("x-token", ""))
    try:
        sent = await request.json()
    except ValueError:
        sent = None
    if not isinstance(sent, dict):
        return _refused(('Send {"language": "en" | "it"} as JSON.',))
    chosen = str(sent.get("language", "")).strip().lower()
    if chosen not in categories.LABELS:
        return _refused((f"Unknown language: {chosen!r}.",))
    response = JSONResponse({"language": chosen, "errors": []})
    response.set_cookie(LANGUAGE_COOKIE, chosen, path="/", samesite="lax")
    return response


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
        found = query.choices(conn, owner, language=language_of(request))
        kept = _originals(request, conn, (file.id for file in staged.files))
    return JSONResponse(
        {
            "owners": owners,
            "owner": owner,
            **wire.draft(staged, found.label, kept),
            "choices": wire.choices(found),
            # What a new expense category can go under.
            "under": [found.label(group) for group in found.groups],
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
    try:
        async with request.form(max_files=uploads_.UPLOADS) as form:
            owner = str(form.get("owner", ""))
            sent = [part for part in form.getlist("file") if isinstance(part, UploadFile)]
            uploads = [
                (uploads_.name(part.filename), await part.read()) for part in sent if part.filename
            ]
    except HTTPException as exc:
        # A form the parser refuses, most often more files than one import takes.
        return _refused((f"Nothing was imported: {exc.detail}",))
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
    try:
        held, notice = await run_in_threadpool(uploads_.look_up, state, key)
    except uploads_.LookupRefusedError as exc:
        body: wire.JSON = {"errors": [exc.message]}
        if exc.held is not None:
            body["undated"] = {"bills": list(exc.held.bills), "held": key}
        return JSONResponse(body, exc.status)
    return await _stage(request, held.owner, held.uploads, notice)


class _RefusedError(Exception):
    """What was sent cannot be done; its message says why."""


def _choices(
    sent: dict[str, object], staged: months.Draft, conn: sqlite3.Connection
) -> list[staging.Choice]:
    """The categories chosen for staged rows, `[{"ids", "category", "rule"}]` (by label or key),
    as choices that give them: rows without one are given it, those with one have it
    changed."""
    rows = {row.id: row for row in staged.rows}
    chosen = sent.get("choices", [])
    if not isinstance(chosen, list) or not all(isinstance(c, dict) for c in chosen):
        raise _RefusedError("Send the choices as a list of rows and their category.")
    made: list[staging.Choice] = []
    for choice in chosen:
        listed = choice.get("ids")
        ids = [str(row) for row in listed] if isinstance(listed, list) else []
        if not ids or any(row not in rows for row in ids):
            raise _RefusedError("Those rows are not staged any more: load the page again.")
        category, rule = str(choice.get("category", "")), choice.get("rule") is True
        waiting = [row for row in ids if rows[row].waiting]
        key = store.category_key(conn, category)
        having = [row for row in ids if not rows[row].waiting and rows[row].category != key]
        if waiting:
            made.append(staging.Choice(waiting, category, rule=rule))
        if having:
            made.append(staging.Choice(having, category, rule=rule, change=True))
    return made


def rule_places(state: State) -> suggestions.Places | None:
    """Where the import reads its rules, and so where a rule offered goes; None when the app
    has nowhere to write one."""
    if state.rules is None:
        return None
    shared = list(state.imports.rules) if state.imports else [state.rules.shared]
    unmatched = state.imports.unmatched if state.imports else ""
    return suggestions.Places(shared, state.rules.people, unmatched)


def _offered(
    conn: sqlite3.Connection, state: State, owner: str, ids: list[str], language: str
) -> tuple[list[wire.JSON], str | None]:
    """The rules offered for the rows `ids` changed by hand, and why none could be, if so."""
    places = rule_places(state)
    if places is None or not ids:
        return [], None
    try:
        found = suggestions.suggest(conn, places, owner, ids)
    except (ConfigError, yaml.YAMLError) as exc:
        return [], f"No rule can be offered: {wire.sentence(exc)}"
    label = query.choices(conn, owner, language=language).label
    return [wire.suggestion(offered, label) for offered in found], None


async def _categorize_staged(request: Request) -> Response:
    """Give the categories chosen to staged rows of one person, all of them or none, and with
    `"commit": true` then commit everything staged for that person:
    `{"owner", "choices": [{"ids", "category", "rule"}], "commit"}`. Rows that had a category
    and were given another without a rule are answered with the rules to offer for them
    (`suggestions`, plan decision 56; `unoffered` says why there are none when the rules files
    cannot be read)."""
    sent = await _sent(request)
    owners = _importable(request)
    state = request.app.state
    if sent is None or str(sent.get("owner", "")) not in owners:
        return _refused(("Send whose rows they are and the categories chosen, as JSON.",))
    owner = str(sent["owner"])

    def apply() -> tuple[int, int | None, list[str]]:
        conn = db.connect(state.database)
        try:
            made = _choices(sent, months.draft(conn, owner), conn)
            remember = None if state.rules is None else state.rules.of(owner)
            changed = (
                staging.categorize_all(
                    conn, owner=owner, choices=made, now=state.clock(), remember=remember
                )
                if made
                else 0
            )
            by_hand = [row for c in made if c.change and not c.rule for row in c.ids]
            if sent.get("commit") is not True:
                return changed, None, by_hand
            files, _ = staging.commit_with_copy(
                conn, state.database, owner=owner, now=state.clock()
            )
            return changed, len(files), by_hand
        finally:
            conn.close()

    language = language_of(request)

    def offer(ids: list[str]) -> tuple[list[wire.JSON], str | None]:
        with db.reading(state.database) as conn:
            return _offered(conn, state, owner, ids, language)

    try:
        changed, committed, by_hand = await run_in_threadpool(apply)
    except _RefusedError as exc:
        return JSONResponse({"errors": [str(exc)]}, 409)
    except (staging.UncategorizedRowsError, ReconciliationError) as exc:
        return JSONResponse({"errors": [f"Not committed: {wire.sentence(exc)}"]}, 409)
    except (staging.StagingError, store.UnknownCategoryError) as exc:
        return JSONResponse({"errors": [f"No category was set: {wire.sentence(exc)}"]}, 409)
    offered, unoffered = await run_in_threadpool(offer, by_hand)
    return JSONResponse(
        {
            "changed": changed,
            "committed": committed,
            "suggestions": offered,
            "unoffered": unoffered,
            "errors": [],
        }
    )


async def _add_rule(request: Request) -> Response:
    """Add the rule offered for a row whose category was changed by hand, first in its file
    (plan decision 56): `{"owner", "id"}`, the `id` of a suggestion. Answers the rule added."""
    sent = await _sent(request)
    owners = _importable(request)
    state = request.app.state
    places = rule_places(state)
    if places is None:  # nowhere to write it down for the next import
        raise HTTPException(404)
    if sent is None or str(sent.get("owner", "")) not in owners:
        return _refused(("Send whose row it is and the id of the row, as JSON.",))
    owner, row = str(sent["owner"]), str(sent.get("id", ""))

    language = language_of(request)

    def add() -> wire.JSON:
        with db.reading(state.database) as conn:
            added = suggestions.add(conn, places, owner, row)
            return wire.suggestion(added, query.choices(conn, owner, language=language).label)

    try:
        rule = await run_in_threadpool(add)
    except (suggestions.SuggestionError, ConfigError, yaml.YAMLError) as exc:
        return JSONResponse({"errors": [f"No rule was added: {wire.sentence(exc)}"]}, 409)
    return JSONResponse({"rule": rule, "errors": []})


async def _add_category(request: Request) -> Response:
    """One more category in the taxonomy, for every import from now on: `{"name", "side",
    "group"}`, an expense (`expenses`) under `group` or a group of its own, or income
    (`income`)."""
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


async def _closing(request: Request, day: bool) -> Response:
    """Close an account (`day`: on the day sent, `{"owner", "account", "day"}`) or open it again
    (`{"owner", "account"}`), each of its currencies when it is a broker's, which the accounts
    page lists as one account (`closing.booked_as`). Closing is refused unless the account held
    nothing on the day (`closing`): the reason is sent as it is."""
    sent = await _sent(request)
    owner, account = (None, None) if sent is None else (sent.get("owner"), sent.get("account"))
    when = None
    if day and sent is not None:
        with contextlib.suppress(ValueError):  # no day, or not one: refused below
            when = date.fromisoformat(str(sent.get("day")))
    if not isinstance(owner, str) or not isinstance(account, str) or (day and when is None):
        sends = (
            "the owner, the account and the day (YYYY-MM-DD)"
            if day
            else "the owner and the account"
        )
        return _refused((f"Send {sends} as JSON.",))
    state = request.app.state

    def act() -> None:
        conn = db.connect(state.database)
        try:
            parts = closing.booked_as(conn, owner, account)
            if when is None:
                closing.reopen_all(conn, owner=owner, accounts=parts)
            else:
                closing.close_all(conn, owner=owner, accounts=parts, day=when)
        finally:
            conn.close()

    try:
        await run_in_threadpool(act)
    except staging.StagingError as exc:
        return JSONResponse({"errors": [wire.sentence(exc)]}, 409)
    return JSONResponse({"owner": owner, "account": account, "errors": []})


async def _close(request: Request) -> Response:
    return await _closing(request, True)


def _typed_where(sent: dict[str, object], owners: list[str]) -> typed.Where:
    owner, account = str(sent.get("owner", "")), str(sent.get("account", "")).strip()
    if owner not in owners:
        msg = f"{owner or 'nobody'} is nobody the config names"
        raise staging.StagingError(msg)
    if not account:
        msg = "say which account"
        raise staging.StagingError(msg)
    try:
        day = date.fromisoformat(str(sent.get("day", "")))
    except ValueError:
        msg = f"{sent.get('day')!r} is not a day like 2026-10-31"
        raise staging.StagingError(msg) from None
    return typed.Where(owner, account, day)


async def _type_figure(request: Request) -> Response:
    """Type what an account held at the end of a day where no statement says it, as
    `scrooge balance` and `scrooge position` do: `{"owner", "account", "day", "what"}` with
    `what` "balance" (`amount`, `currency`) or "position" (`symbol`, `quantity`, `currency`,
    and `tab` for an instrument not known yet); with `"remove": true` the figure typed for that
    day (of `symbol`, for a position) is removed instead. Refused as the command refuses it."""
    sent = await _sent(request)
    owners = _importable(request)
    if sent is None or sent.get("what") not in {"balance", "position"}:
        return _refused(('Send the owner, the account, the day and "what" as JSON.',))
    state = request.app.state
    instruments = state.imports.instruments
    position = sent["what"] == "position"
    today = state.clock().date()

    def act() -> str:
        where = _typed_where(sent, owners)
        symbol = str(sent.get("symbol", "")).strip()
        currency = str(sent.get("currency", "")).strip()
        conn = db.connect(state.database)
        try:
            if sent.get("remove") is True:
                if position:
                    symbol = typed.remove_position(conn, where, symbol, instruments)
                else:
                    typed.remove_balance(conn, where)
                return symbol
            if position:
                tab = sent.get("tab")
                holding = TypedHolding(
                    symbol,
                    typed.quantity(str(sent.get("quantity", ""))),
                    currency,
                    str(tab) if tab else None,
                )
                return typed.position(conn, where, holding, today, instruments).symbol
            typed.balance(conn, where, typed.amount(str(sent.get("amount", ""))), currency, today)
            return ""
        finally:
            conn.close()

    try:
        symbol = await run_in_threadpool(act)
    except uploads_.REFUSED as exc:
        return JSONResponse({"errors": [wire.sentence(exc)]}, 409)
    return JSONResponse({"symbol": symbol or None, "errors": []})


async def _reopen(request: Request) -> Response:
    return await _closing(request, False)


def _ranges(through: date, start: int) -> list[dict[str, str]]:
    """The ranges offered, as `from` and `to` take them: the year up to the last day committed,
    the twelve months before it, each earlier year of the statements (two at most) and all of
    them, when they go back before this year."""
    twelve = analysis.add_months(through, -11)
    years = range(through.year - 1, max(start, through.year - 2) - 1, -1)
    return [
        {
            "key": "ytd",
            "label": "Year to date",
            "from": str(through.year),
            "to": "",
            "range": "ytd",
        },
        {
            "key": "12m",
            "label": "Last 12 months",
            "from": f"{twelve:%Y-%m}",
            "to": "",
            "range": "1y",
        },
        *(
            {"key": str(year), "label": str(year), "from": str(year), "to": str(year), "range": ""}
            for year in years
        ),
        *(
            [{"key": "all", "label": "All", "from": str(start), "to": "", "range": "all"}]
            if start < through.year
            else []
        ),
    ]


# How many months before the last one committed each of `forms.PERIODS` starts, the last
# included: the month itself, two before it, and so on; the year to date and all of it apart.
_MONTHS_BACK = {"1m": 0, "3m": 2, "1y": 11, "3y": 35}


def _period_start(period: str | None, through: date | None, start: int | None) -> date | None:
    """The first day of `period` (one of `forms.PERIODS`), by whole months up to the last day
    committed, `through`; `start` is the year the statements start. None for no period."""
    if period is None or through is None:
        return None
    if period == "ytd":
        return date(through.year, 1, 1)
    if period == "all":
        return date(start, 1, 1) if start is not None else None
    return analysis.add_months(through, -_MONTHS_BACK[period])


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
        if first is None and last is None:
            first = _period_start(asked.range, through, min(firsts).year if firsts else None)
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
        label = store.category_labels(conn, language=language_of(request))
    return JSONResponse(
        {
            "report": None if shown is None else wire.cashflow_report(shown, label),
            "first": wire.day(first),
            "last": wire.day(last),
            "by": asked.grouping.value,
            "ranges": [] if through is None else _ranges(through, min(firsts).year),
            "errors": errors,
        }
    )


def _income(request: Request) -> Response:
    """What investments paid from `from` to `to`, in `currency`: the twelve months up to the
    last day committed when no range is asked for. Rows in a currency without a rate are left
    out, each such currency said in `errors`."""
    asked = forms.analysis_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    with db.reading(request.app.state.database) as conn:
        try:
            query.check_names(conn, query.Filter(owner=asked.owner))
        except query.QueryError as exc:
            return _refused((wire.sentence(exc),))
        through = analysis.through(conn, asked.owner)
        firsts = [p.first for p in query.people(conn) if p.first and asked.owner in (None, p.name)]
        first = asked.first
        if first is None and asked.last is None:
            first = _period_start(asked.range, through, min(firsts).year if firsts else None)
        shown = payouts.run(
            conn, first=first, last=asked.last, owner=asked.owner, currency=asked.currency
        )
        found = logos(request, conn)
    return JSONResponse(
        {
            "income": wire.payouts_report(shown),
            "logos": found,
            "ranges": _ranges(through, min(firsts).year) if through and firsts else [],
            "errors": [wire.sentence(networth.MissingRateError(why)) for why in shown.missing],
        }
    )


def _performance(request: Request) -> Response:
    """What the investment accounts of `owner`, or of everyone, returned over `range` (the year
    to date when left out), in `currency`, beside the benchmarks of the config. Accounts with
    a currency without a rate are left out, each such currency said in `errors`; benchmarks
    without the closes or the rate they need are named in `performance.missing_benchmarks`."""
    asked = forms.performance_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    with db.reading(request.app.state.database) as conn:
        try:
            shown = performance.run(
                conn,
                owner=asked.owner,
                currency=asked.currency,
                range_=asked.range,
                benchmarks=request.app.state.benchmarks,
            )
        except query.QueryError as exc:
            return _refused((wire.sentence(exc),))
    errors = [wire.sentence(networth.MissingRateError(why)) for why in shown.missing]
    if shown.left_out:
        errors.append(f"Left out for want of a rate: {', '.join(shown.left_out)}.")
    return JSONResponse({"performance": wire.performance_report(shown), "errors": errors})


async def _fetch_prices(request: Request) -> Response:
    """Fetch closes, splits and exchange rates not stored yet, up to yesterday, as `scrooge
    prices` does."""
    _writable(request, request.headers.get("x-token", ""))
    state = request.app.state
    if state.price_source is None:
        raise HTTPException(404)

    def fetch() -> marketdata.Refreshed:
        typed_instruments = (
            classification.load_instruments(state.imports.instruments) if state.imports else {}
        )
        symbols = {symbol: entry.price_symbol for symbol, entry in typed_instruments.items()}
        now = state.clock()
        through = now.date() - timedelta(days=1)
        conn = db.connect(state.database)
        try:
            return marketdata.refresh(
                conn,
                state.price_source,
                through=through,
                now=now,
                symbols=symbols,
                currencies=networth.CURRENCIES,
                benchmarks=state.benchmarks,
            )
        finally:
            conn.close()

    try:
        refreshed = await run_in_threadpool(fetch)
    except (ConfigError, yaml.YAMLError) as exc:
        return JSONResponse({"errors": [wire.sentence(exc)]}, 409)
    return JSONResponse(
        {
            "closes": refreshed.closes,
            "rates": refreshed.rates,
            "splits": refreshed.splits,
            "errors": [wire.sentence(Exception(problem)) for problem in refreshed.problems],
        }
    )


def _costs(request: Request) -> Response:
    """What investing cost `owner`, or everyone, each year in `currency`: commissions and tax
    withheld. Rows in a currency without a rate are left out, each such currency said in
    `errors`."""
    asked = forms.holdings_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    with db.reading(request.app.state.database) as conn:
        try:
            shown = costs.run(conn, owner=asked.owner, currency=asked.currency)
        except query.QueryError as exc:
            return _refused((wire.sentence(exc),))
    errors = [wire.sentence(networth.MissingRateError(why)) for why in shown.missing]
    return JSONResponse({"costs": wire.costs_report(shown), "errors": errors})


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
        label = store.category_labels(conn, language=language_of(request))
    return JSONResponse({**wire.transactions_context(shown, held, label), "errors": errors})


def _transactions_merchants(request: Request) -> Response:
    """Every merchant `owner` (everyone) spent money at, named as the merchant filter reads
    them, with what was spent there over the last twelve months in `currency`."""
    asked = forms.analysis_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    errors: list[str] = []
    with db.reading(request.app.state.database) as conn:
        try:
            shown = wire.unless_rate_missing(
                errors,
                lambda: merchants.run(conn, owner=asked.owner, currency=asked.currency),
                merchants.Merchants(asked.currency, None, None, (), ()),
            )
        except query.QueryError as exc:
            return _refused((wire.sentence(exc),))
    return JSONResponse({**wire.known_merchants(shown), "errors": errors})


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
    frozen: tuple[networth.FrozenAccount, ...] = request.app.state.frozen
    errors: list[str] = []
    with db.reading(request.app.state.database) as conn:
        errors += networth.frozen_notes(conn, frozen)
        valued = networth.value(conn, asked.owner)

        def unconverted(exc: networth.MissingRateError) -> None:
            # A currency without rates is said and left out; the others are still sent. An
            # account held in such a currency fails every one alike: that is said once.
            if (said := wire.sentence(exc)) not in errors:
                errors.append(said)

        ends = networth.month_ends(conn, valued, currencies, unconverted, frozen=frozen)
    return JSONResponse(
        {
            "currencies": currencies,
            "months": [wire.month_end(end) for end in ends],
            "errors": errors,
        }
    )


def _pensions(request: Request) -> Response:
    """The pension accounts of `owner`, or of everyone, in `currency`: what each is worth and
    how it changed. Accounts with a currency without a rate are left out, each such currency
    said in `errors`."""
    asked = forms.holdings_view(_asked(request))
    if asked.errors:
        return _refused(asked.errors)
    with db.reading(request.app.state.database) as conn:
        try:
            shown = pensions.run(conn, owner=asked.owner, currency=asked.currency)
        except query.QueryError as exc:
            return _refused((wire.sentence(exc),))
    errors = [wire.sentence(networth.MissingRateError(why)) for why in shown.missing]
    if shown.left_out:
        errors.append(f"Left out for want of a rate: {', '.join(shown.left_out)}.")
    return JSONResponse({"pensions": wire.pensions_report(shown), "errors": errors})


routes = [
    Route("/session", _session, name="api_session"),
    Route("/language", _choose_language, methods=["POST"], name="api_language"),
    Route("/overview", _overview, name="api_overview"),
    Route("/networth", _networth, name="api_networth"),
    Route("/networth/months", _networth_months, name="api_networth_months"),
    Route("/month", _month, name="api_month"),
    Route("/attention", _attention, name="api_attention"),
    Route("/holdings", _holdings, name="api_holdings"),
    Route("/logos", _set_logo, methods=["POST"], name="api_set_logo"),
    Route("/logos/remove", _remove_logo, methods=["POST"], name="api_remove_logo"),
    Route("/statements", _statements, name="api_statements"),
    Route("/transactions", _transactions, name="api_transactions"),
    Route("/transactions/category", _categorize, methods=["POST"], name="api_categorize"),
    Route("/transactions/context", _transactions_context, name="api_transactions_context"),
    Route("/transactions/merchants", _transactions_merchants, name="api_transactions_merchants"),
    Route("/cashflow", _cashflow, name="api_cashflow"),
    Route("/income", _income, name="api_income"),
    Route("/performance", _performance, name="api_performance"),
    Route("/prices", _fetch_prices, methods=["POST"], name="api_prices"),
    Route("/costs", _costs, name="api_costs"),
    Route("/recurring", _recurring, name="api_recurring"),
    Route("/import", _staged, name="api_staged"),
    Route("/import", _upload, methods=["POST"], name="api_upload"),
    Route("/import/maturities", _look_up, methods=["POST"], name="api_look_up"),
    Route("/import/categories", _categorize_staged, methods=["POST"], name="api_categorize_staged"),
    Route("/import/category", _add_category, methods=["POST"], name="api_add_category"),
    Route("/import/rules", _add_rule, methods=["POST"], name="api_add_rule"),
    Route("/accounts/close", _close, methods=["POST"], name="api_close"),
    Route("/accounts/reopen", _reopen, methods=["POST"], name="api_reopen"),
    Route("/accounts/typed", _type_figure, methods=["POST"], name="api_type_figure"),
    Route("/statements/{number:int}/discard", _discard, methods=["POST"], name="api_discard"),
    Route("/statements/{number:int}/uncommit", _uncommit, methods=["POST"], name="api_uncommit"),
    Route("/pensions", _pensions, name="api_pensions"),
]
