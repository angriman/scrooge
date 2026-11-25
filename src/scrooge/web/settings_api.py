"""The JSON of the settings page of the app (`/api/settings`): the rules every import applies
and the categories, to see and to change (`settings.py`).

The lists are read like every other route of `web/api.py`; what changes something is refused
like every change there: from another site, without the token of these pages, or on a copy of
the database. Trying a pattern changes nothing, but runs what the address says: it too wants
the token (`X-Token`), so that no other site can make a browser run one, and is allowed on a
copy. Without the place of the rules (`create_app`'s `rules`) there is nothing to show or
change but the built-in categories.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from scrooge import categories, query, settings, staging
from scrooge.errors import ConfigError
from scrooge.storage import db, store
from scrooge.web import wire
from scrooge.web.api import _refused, _sent, language_of

# What a change refused answers: what was asked cannot be done as it is.
_REFUSED = (settings.SettingsError, staging.StagingError, ConfigError)


def _shared(request: Request) -> list[Path]:
    """The rules both people share, first wins; none without a place for rules."""
    state = request.app.state
    if state.rules is None:
        return []
    return list(state.imports.rules) if state.imports is not None else [state.rules.shared]


def _people(request: Request) -> list[str]:
    rules = request.app.state.rules
    return [] if rules is None else list(rules.people)


def _categories_file(request: Request) -> Path | None:
    rules = request.app.state.rules
    return None if rules is None else rules.shared.with_name(categories.FILE)


def _editable(request: Request) -> bool:
    state = request.app.state
    return state.rules is not None and not state.read_only


def _rules(request: Request) -> Response:
    """Every rule `owner`'s imports try (everyone's without it), in the order they are tried,
    with how many committed rows each would match on its own; and the categories a rule can
    give."""
    people = _people(request)
    owner = request.query_params.get("owner") or None
    if owner is not None and owner not in people:
        return _refused((f"{owner} is nobody the config names.",))
    shared = _shared(request)
    files = settings.rule_files(shared, people, owner) if shared else []
    with db.reading(request.app.state.database) as conn:
        listed, problems = settings.rules(conn, files, owner)
        found = query.choices(conn, language=language_of(request))
    return JSONResponse(
        {
            "people": people,
            "files": [{"key": file.key, "owner": file.owner} for file in files],
            "rules": [wire.listed_rule(rule, found.label) for rule in listed],
            "leaves": wire.choices(found)["leaves"],
            "editable": _editable(request),
            "errors": [wire.sentence(ConfigError(problem)) for problem in problems],
        }
    )


def _from_these_pages(request: Request) -> None:
    """Refuse what another site sent, or what comes without the token of these pages."""
    state = request.app.state
    origin = request.headers.get("origin", "null")
    if origin != "null" and urlsplit(origin).hostname not in state.hosts:
        raise HTTPException(403)
    token = request.headers.get("x-token", "")
    if not secrets.compare_digest(token.encode(), state.token.encode()):
        raise HTTPException(403)


def _test(request: Request) -> Response:
    """The committed rows of `owner` (of everyone) that the pattern `match` matches in
    `direction`, newest first, as a rule with it would: nothing is saved. Tried for a while
    (`settings.BUDGET`); what time left untried is said in `errors`."""
    _from_these_pages(request)
    params = request.query_params
    owner = params.get("owner") or None
    if owner is not None and owner not in _people(request):
        return _refused((f"{owner} is nobody the config names.",))
    with db.reading(request.app.state.database) as conn:
        try:
            tried = settings.try_pattern(
                conn,
                params.get("match", ""),
                direction=params.get("direction", ""),
                owner=owner,
                budget=settings.BUDGET,
            )
        except settings.SettingsError as exc:
            return _refused((wire.sentence(exc),))
        label = store.category_labels(conn, language=language_of(request))
    cut = f"It took too long: only the {tried.tried} newest rows were tried."
    return JSONResponse(
        {
            "count": tried.count,
            "rows": [wire.transaction(row, None, label) for row in tried.rows],
            "errors": [] if tried.complete else [cut],
        }
    )


class _Unreadable(Exception):
    """What was sent is not what the route takes; the message says what it takes."""


async def _change(request: Request, act: Callable[[dict[str, object]], object]) -> Response:
    """Make a change of the settings page with what was sent, once it is known to come from
    these pages: `act` returns what the answer adds to `errors`."""
    sent = await _sent(request)
    if request.app.state.rules is None:  # nowhere to write it down
        raise HTTPException(404)
    try:
        if sent is None:
            raise _Unreadable
        done = await run_in_threadpool(act, sent)
    except _Unreadable as exc:
        return _refused((str(exc) or "Send the change as JSON.",))
    except _REFUSED as exc:
        return JSONResponse({"errors": [wire.sentence(exc)]}, 409)
    except sqlite3.IntegrityError:
        return JSONResponse({"errors": ["The database already has a category of that name."]}, 409)
    return JSONResponse({**(done if isinstance(done, dict) else {}), "errors": []})


def _text(sent: dict[str, object], name: str) -> str:
    value = sent.get(name, "")
    if not isinstance(value, str):
        msg = f"Send '{name}' as text."
        raise _Unreadable(msg)
    return value


def _seen(request: Request, sent: dict[str, object]) -> settings.Seen:
    """The rule a change is about: its file, its place there, and its lines as shown."""
    key, position = _text(sent, "file"), sent.get("position")
    files = settings.rule_files(_shared(request), _people(request), None)
    path = next((file.path for file in files if file.key == key), None)
    if path is None:
        msg = f"There is no rules file {key or 'named'} here."
        raise _Unreadable(msg)
    if not isinstance(position, int) or isinstance(position, bool):
        msg = "Send the place of the rule in its file as a number."
        raise _Unreadable(msg)
    return settings.Seen(path, position, _text(sent, "version"))


async def _edit_rule(request: Request) -> Response:
    """Give a rule another pattern, category or direction: `{"file", "position", "version",
    "match", "category", "direction"}`."""

    def act(sent: dict[str, object]) -> None:
        settings.edit_rule(
            _seen(request, sent),
            match=_text(sent, "match"),
            category=_text(sent, "category"),
            direction=_text(sent, "direction"),
            categories_file=_categories_file(request),
        )

    return await _change(request, act)


async def _remove_rule(request: Request) -> Response:
    """Take a rule out of its file: `{"file", "position", "version"}`."""

    def act(sent: dict[str, object]) -> None:
        settings.remove_rule(_seen(request, sent), _categories_file(request))

    return await _change(request, act)


async def _move_rule(request: Request) -> Response:
    """Move a rule one place up (`"by": -1`) or down (1) in its file, past the rule there as
    the page showed it (`"beside"`, its `version`)."""

    def act(sent: dict[str, object]) -> None:
        by = sent.get("by")
        steps = by if isinstance(by, int) and not isinstance(by, bool) else 0
        beside = _text(sent, "beside")
        settings.move_rule(_seen(request, sent), steps, beside, _categories_file(request))

    return await _change(request, act)


def _listed_categories(request: Request) -> Response:
    """The taxonomy and what `categories.yaml` adds, with the committed rows of each, and the
    top-level expense categories a new one can go under."""
    file = _categories_file(request)
    extras = categories.load(file) if file else ()
    language = language_of(request)
    label = categories.Labels.of(extras, language=language)
    with db.reading(request.app.state.database) as conn:
        listed = settings.listed_categories(conn, file, language=language)
    unmatched: str = request.app.state.unmatched
    return JSONResponse(
        {
            "categories": [wire.listed_category(category) for category in listed],
            "groups": [label(group) for group in categories.groups(extras)],
            "unmatched": label(categories.key(unmatched)) if unmatched else None,
            "editable": _editable(request),
            "errors": [],
        }
    )


def _places(request: Request) -> settings.Places:
    state = request.app.state
    files = settings.rule_files(_shared(request), _people(request), None)
    return settings.Places(
        state.database,
        state.rules.shared.with_name(categories.FILE),
        [file.path for file in files],
        state.unmatched,
    )


def _writing(request: Request, act: Callable[[sqlite3.Connection], object]) -> object:
    conn = db.connect(request.app.state.database)
    try:
        return act(conn)
    finally:
        conn.close()


async def _add_category(request: Request) -> Response:
    """One more category: `{"name", "side", "group"}`, an expense (`uscite`) under `group`
    or a group of its own, or income (`entrate`)."""

    def act(sent: dict[str, object]) -> object:
        def add(conn: sqlite3.Connection) -> object:
            added = staging.add_category(
                conn,
                _places(request).categories_file,
                name=_text(sent, "name"),
                side=wire.side_sent(_text(sent, "side")),
                group=_text(sent, "group"),
            )
            return {"name": added.name}

        return _writing(request, add)

    return await _change(request, act)


def _rows_after(
    request: Request,
    change: Callable[..., int],
    fields: dict[str, str],
) -> Callable[[dict[str, object]], object]:
    """What a change of a category does with what was sent: `fields` maps each argument of
    `change` to the field it is sent in. The answer says how many rows it concerned."""

    def act(sent: dict[str, object]) -> object:
        given = {argument: _text(sent, field) for argument, field in fields.items()}

        def run(conn: sqlite3.Connection) -> object:
            rows = change(conn, _places(request), now=request.app.state.clock(), **given)
            return {"rows": rows}

        return _writing(request, run)

    return act


async def _rename_category(request: Request) -> Response:
    """Call an added category otherwise, on its rows and in its rules too: `{"name", "to"}`."""
    fields = {"name": "name", "to": "to"}
    return await _change(request, _rows_after(request, settings.rename_category, fields))


async def _merge_category(request: Request) -> Response:
    """Give the rows and rules of one category another: `{"name", "into"}`."""
    fields = {"name": "name", "into": "into"}
    return await _change(request, _rows_after(request, settings.merge_category, fields))


async def _move_category(request: Request) -> Response:
    """Put an added expense category under another top-level category: `{"name", "under"}`."""
    fields = {"name": "name", "under": "under"}
    return await _change(request, _rows_after(request, settings.move_category, fields))


async def _remove_category(request: Request) -> Response:
    """Take out an added category nothing gives: `{"name"}`."""

    def act(sent: dict[str, object]) -> object:
        return _writing(
            request,
            lambda conn: settings.remove_category(conn, _places(request), name=_text(sent, "name")),
        )

    return await _change(request, act)


routes = [
    Route("/rules", _rules, name="api_settings_rules"),
    Route("/rules/test", _test, name="api_settings_rules_test"),
    Route("/rules/edit", _edit_rule, methods=["POST"], name="api_settings_rules_edit"),
    Route("/rules/remove", _remove_rule, methods=["POST"], name="api_settings_rules_remove"),
    Route("/rules/move", _move_rule, methods=["POST"], name="api_settings_rules_move"),
    Route("/categories", _listed_categories, name="api_settings_categories"),
    Route("/categories/add", _add_category, methods=["POST"], name="api_settings_add"),
    Route("/categories/rename", _rename_category, methods=["POST"], name="api_settings_rename"),
    Route("/categories/merge", _merge_category, methods=["POST"], name="api_settings_merge"),
    Route("/categories/move", _move_category, methods=["POST"], name="api_settings_move"),
    Route("/categories/remove", _remove_category, methods=["POST"], name="api_settings_remove"),
]
