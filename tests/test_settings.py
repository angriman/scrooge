"""The rules and the categories as the settings page changes them: in their YAML files where
they are, and on the committed rows."""

import dataclasses
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from scrooge import categories, query, rulesfile, settings, staging
from scrooge.models import Kind
from scrooge.storage import db
from tests.storage import helpers
from tests.storage.helpers import OWNER, PARTNER, txn

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
AUGUST = (date(2026, 8, 1), date(2026, 8, 31))
SHARED = """# Rules both share.
rules:
  # --- Shops
  - {match: 'KONSUM', category: Groceries}  # food
  - {match: 'POSTA', category: Shipping}
  - {match: 'ACME', category: Salary, direction: in}
"""
OWN = """rules:
  - {match: 'Anna Meier', category: Prestiti}
"""
CATEGORIES = """# Added by hand.
categories:
  uscite: {}
  income: []
"""


@pytest.fixture
def places(tmp_path: Path) -> settings.Places:
    shared = tmp_path / "rules.yaml"
    shared.write_text(SHARED, encoding="utf-8")
    (tmp_path / "rules.lukas.yaml").write_text(OWN, encoding="utf-8")
    file = tmp_path / categories.FILE
    file.write_text(CATEGORIES, encoding="utf-8")
    files = settings.rule_files([shared], [OWNER, PARTNER], None)
    return settings.Places(tmp_path / "scrooge.db", file, [f.path for f in files])


@pytest.fixture
def conn(places: settings.Places) -> Iterator[sqlite3.Connection]:
    """Lukas and Anna with August committed; Lukas has rows of two added categories."""
    connection = db.connect(places.database)
    db.migrate(connection, NOW)
    for name, group in (("Shipping", categories.GROUP_TRANSPORT), ("Prestiti", "")):
        staging.add_category(
            connection, places.categories_file, name=name, side="uscite", group=group
        )
    day = date(2026, 8, 10)
    lukas = [
        txn("-45", date=day, source_ref="1", description="KONSUM"),
        txn("-10", date=day, source_ref="2", description="POSTA", category="Shipping"),
        txn("-200", date=day, source_ref="3", description="Anna Meier", category="Prestiti"),
        txn(
            "6000",
            date=day,
            source_ref="4",
            description="ACME",
            kind=Kind.INCOME,
            category=categories.SALARY,
        ),
    ]
    helpers.close(connection, OWNER, AUGUST, AUGUST[1], ("migros", lukas))
    anna = [txn("-20", source_ref="1", description="KONSUM", date=date(2026, 8, 20))]
    helpers.close(connection, PARTNER, AUGUST, AUGUST[1], ("migros", anna))
    yield connection
    connection.close()


def _shared(places: settings.Places) -> Path:
    return places.rule_files[-1]


def _own(places: settings.Places) -> Path:
    return places.rule_files[0]


def _categories_of(conn: sqlite3.Connection) -> list[tuple[str, str | None]]:
    rows = query.run(conn, query.Filter(owner=OWNER)).rows
    return [(row.description, row.category) for row in rows]


def test_rule_files_go_in_the_order_an_import_tries_them(tmp_path: Path) -> None:
    shared = [tmp_path / "rules.yaml", tmp_path / "more" / "rules.yaml"]
    files = settings.rule_files(shared, [OWNER, PARTNER], None)
    assert [(f.key, f.owner) for f in files] == [
        ("rules.lukas.yaml", OWNER),
        ("rules.anna.yaml", PARTNER),
        ("rules.yaml", None),
        (str(shared[1]), None),
    ]
    assert [f.key for f in settings.rule_files(shared[:1], [OWNER, PARTNER], PARTNER)] == [
        "rules.anna.yaml",
        "rules.yaml",
    ]


def test_rules_say_how_many_committed_rows_each_matches(
    conn: sqlite3.Connection, places: settings.Places
) -> None:
    files = settings.rule_files([_shared(places)], [OWNER, PARTNER], None)
    listed, problems = settings.rules(conn, files)
    assert problems == []
    assert [(r.file.key, r.position, r.match, r.matches) for r in listed] == [
        ("rules.lukas.yaml", 1, "Anna Meier", 1),
        ("rules.yaml", 1, "KONSUM", 2),  # Lukas's and Anna's
        ("rules.yaml", 2, "POSTA", 1),
        ("rules.yaml", 3, "ACME", 1),
    ]
    assert listed[3].direction == "in"


def test_a_rule_that_is_no_pattern_matches_nothing_and_a_broken_file_is_said(
    conn: sqlite3.Connection, places: settings.Places
) -> None:
    _shared(places).write_text("rules:\n  - {match: '(', category: Groceries}\n", encoding="utf-8")
    _own(places).write_text("rules: [\n", encoding="utf-8")
    files = settings.rule_files([_shared(places)], [OWNER], None)
    listed, problems = settings.rules(conn, files)
    assert [(r.match, r.matches) for r in listed] == [("(", None)]
    assert len(problems) == 1
    assert "rules.lukas.yaml" in problems[0]


def test_a_pattern_tried_lists_the_rows_it_matches_newest_first(
    conn: sqlite3.Connection,
) -> None:
    tried = settings.try_pattern(conn, "konsum|acme")
    assert (tried.count, tried.tried, tried.complete) == (3, 5, True)
    assert tried.rows[0].owner == PARTNER  # 20 August, the newest
    tried = settings.try_pattern(conn, "konsum|acme", direction="in", owner=OWNER)
    assert (tried.count, [row.description for row in tried.rows]) == (1, ["ACME"])


def test_a_pattern_is_tried_for_a_while_only(conn: sqlite3.Connection) -> None:
    ticks = iter(range(100))
    tried = settings.try_pattern(conn, ".", budget=2, clock=lambda: next(ticks))
    assert (tried.count, tried.tried, tried.complete) == (2, 2, False)


@pytest.mark.parametrize(
    ("match", "direction", "problem"),
    [
        ("", "", "needs a pattern"),
        ("(", "", "is no pattern"),
        ("a", "up", "direction"),
        ("a" * 201, "", "at most 200 characters"),
    ],
)
def test_a_pattern_that_cannot_be_tried_says_why(
    conn: sqlite3.Connection, match: str, direction: str, problem: str
) -> None:
    with pytest.raises(settings.SettingsError, match=problem):
        settings.try_pattern(conn, match, direction=direction)


def _seen(places: settings.Places, position: int, path: Path | None = None) -> settings.Seen:
    """Rule `position` of the shared file (or of `path`) as the page would show it now."""
    file = path or _shared(places)
    return settings.Seen(file, position, rulesfile.read(file)[1][position - 1].version)


@pytest.mark.usefixtures("conn")  # which adds the categories the other rules give
def test_a_rule_edited_keeps_the_comments_of_its_file(places: settings.Places) -> None:
    settings.edit_rule(
        _seen(places, 1),
        match="KONSUM|COOP",
        category=categories.EATING_OUT,
        direction="out",
        categories_file=places.categories_file,
    )
    assert _shared(places).read_text(encoding="utf-8") == SHARED.replace(
        "{match: 'KONSUM', category: Groceries}  # food",
        '{match: "KONSUM|COOP", category: "Eating out", direction: out}  # food',
    )


@pytest.mark.parametrize(
    ("match", "category", "direction", "problem"),
    [
        (" ", categories.GROCERIES, "", "needs a pattern"),
        ("[", categories.GROCERIES, "", "is no pattern"),
        ("KONSUM", "Nothing", "", "no category 'Nothing'"),
        ("KONSUM", categories.GROCERIES, "sideways", "direction"),
    ],
)
def test_an_edit_that_cannot_be_made_leaves_the_file(
    places: settings.Places, match: str, category: str, direction: str, problem: str
) -> None:
    with pytest.raises(settings.SettingsError, match=problem):
        settings.edit_rule(
            _seen(places, 1),
            match=match,
            category=category,
            direction=direction,
            categories_file=None,
        )
    assert _shared(places).read_text(encoding="utf-8") == SHARED


def test_a_change_of_a_rule_shown_before_another_change_is_refused(
    places: settings.Places,
) -> None:
    shown = _seen(places, 1)
    # Only the direction changed since: the rule is not as the page showed it.
    text = SHARED.replace(
        "{match: 'KONSUM', category: Groceries}",
        "{match: 'KONSUM', category: Groceries, direction: out}",
    )
    _shared(places).write_text(text, encoding="utf-8")
    with pytest.raises(settings.SettingsError, match="changed since"):
        settings.remove_rule(shown, None)
    with pytest.raises(settings.SettingsError, match="no rule 4"):
        settings.remove_rule(settings.Seen(_shared(places), 4, shown.version), None)
    assert _shared(places).read_text(encoding="utf-8") == text


def test_a_change_that_would_not_load_is_not_saved(places: settings.Places) -> None:
    broken = (
        "rules:\n  - {match: 'KONSUM', category: Groceries, kind: nonsense}\n"
        "  - {match: a, category: Groceries}\n"
    )
    _shared(places).write_text(broken, encoding="utf-8")
    with pytest.raises(settings.SettingsError, match="left as it was"):
        settings.edit_rule(
            _seen(places, 1),
            match="KONSUM",
            category=categories.GROCERIES,
            direction="in",
            categories_file=None,
        )
    with pytest.raises(settings.SettingsError, match="left as it was"):
        settings.move_rule(_seen(places, 2), -1, None)
    assert _shared(places).read_text(encoding="utf-8") == broken


@pytest.mark.usefixtures("conn")
def test_a_rule_removed_or_moved_keeps_the_comments(places: settings.Places) -> None:
    file = places.categories_file
    settings.move_rule(_seen(places, 3), -1, file)
    settings.move_rule(_seen(places, 1), 1, file)
    lines = _shared(places).read_text(encoding="utf-8").splitlines()
    assert lines[:3] == SHARED.splitlines()[:3]
    assert [line.split(",")[0] for line in lines[3:]] == [
        "  - {match: 'ACME'",
        "  - {match: 'KONSUM'",
        "  - {match: 'POSTA'",
    ]
    assert lines[4].endswith("# food")
    settings.remove_rule(_seen(places, 2), file)
    assert "KONSUM" not in _shared(places).read_text(encoding="utf-8")
    assert "# --- Shops" in _shared(places).read_text(encoding="utf-8")


BLOCKS = """rules:
  - match: KONSUM
    category: Groceries
  - match: ACME  # the employer
    category: Salary
  -
    match: POSTA
    category: Groceries
  - {match: '.', category: Gifts}
"""


def test_rules_on_lines_of_their_own_are_changed_only_when_it_is_safe(
    places: settings.Places,
) -> None:
    path = _shared(places)
    path.write_text(BLOCKS, encoding="utf-8")
    # A dash on a line of its own: neither removed nor moved, nor passed by a move.
    changes: list[Callable[[], None]] = [
        lambda: settings.remove_rule(_seen(places, 3), None),
        lambda: settings.move_rule(_seen(places, 3), -1, None),
        lambda: settings.move_rule(_seen(places, 4), -1, None),
    ]
    for change in changes:
        with pytest.raises(settings.SettingsError, match="cannot be"):
            change()
    # Comments among its keys: not written again, but moved and removed whole.
    with pytest.raises(settings.SettingsError, match="comments among its keys"):
        settings.edit_rule(
            _seen(places, 2),
            match="ACME",
            category=categories.GROCERIES,
            direction="",
            categories_file=None,
        )
    assert path.read_text(encoding="utf-8") == BLOCKS
    settings.move_rule(_seen(places, 2), -1, None)
    moved = "  - match: ACME  # the employer\n    category: Salary\n  - match: KONSUM\n"
    assert moved in path.read_text(encoding="utf-8")
    settings.remove_rule(_seen(places, 1), None)
    settings.edit_rule(
        _seen(places, 1),
        match="KONSUM",
        category=categories.GROCERIES,
        direction="out",
        categories_file=None,
    )
    assert path.read_text(encoding="utf-8") == (
        "rules:\n"
        '  - {match: "KONSUM", category: "Groceries", direction: out}\n'
        "  -\n    match: POSTA\n    category: Groceries\n"
        "  - {match: '.', category: Gifts}\n"
    )


@pytest.mark.parametrize(
    ("position", "by", "problem"),
    [(1, -1, "is the first"), (3, 1, "is the last"), (1, 2, "one place")],
)
def test_a_rule_moves_one_place_within_its_file(
    places: settings.Places, position: int, by: int, problem: str
) -> None:
    with pytest.raises(settings.SettingsError, match=problem):
        settings.move_rule(_seen(places, position), by, None)


def test_categories_list_the_taxonomy_then_what_is_added(
    conn: sqlite3.Connection, places: settings.Places
) -> None:
    listed = {(c.name, c.side): c for c in settings.listed_categories(conn, places.categories_file)}
    assert listed[categories.GROCERIES, "uscite"].rows == 2
    assert listed[categories.GROCERIES, "uscite"].built_in
    assert listed["Shipping", "uscite"] == settings.ListedCategory(
        "Shipping", "uscite", categories.GROUP_TRANSPORT, False, 1
    )
    assert listed["Prestiti", "uscite"].parent == "Prestiti"
    assert listed[categories.SALARY, "entrate"].rows == 1
    assert listed[categories.OWN_TRANSFER, "transfer"].rows == 0
    assert len(settings.listed_categories(conn, None)) == len(listed) - 2


def test_a_category_renamed_follows_on_rows_rules_and_file(
    conn: sqlite3.Connection, places: settings.Places
) -> None:
    rows = settings.rename_category(conn, places, name="Prestiti", to=" Mutuo  casa ", now=NOW)
    assert rows == 1
    assert ("Anna Meier", "Mutuo casa") in _categories_of(conn)
    added = categories.load(places.categories_file)
    assert categories.Extra("Mutuo casa", "uscite", "Mutuo casa") in added
    assert places.categories_file.read_text(encoding="utf-8").startswith("# Added by hand.\n")
    assert _own(places).read_text(encoding="utf-8") == OWN.replace(
        "category: Prestiti", 'category: "Mutuo casa"'
    )
    assert len(list((places.database.parent / "backups").iterdir())) == 1


@pytest.mark.parametrize(
    ("name", "to", "problem"),
    [
        (categories.GROCERIES, "Cibo", "is built in"),
        ("Nothing", "Cibo", "no category 'Nothing'"),
        ("Prestiti", categories.GROCERIES, "already a category 'Groceries'"),
        ("Prestiti", "  ", "needs a name"),
    ],
)
def test_a_rename_that_cannot_be_made_says_why(
    conn: sqlite3.Connection, places: settings.Places, name: str, to: str, problem: str
) -> None:
    with pytest.raises(settings.SettingsError, match=problem):
        settings.rename_category(conn, places, name=name, to=to, now=NOW)


def test_a_rename_that_fails_leaves_rows_files_and_no_copy(
    conn: sqlite3.Connection, places: settings.Places
) -> None:
    before = places.categories_file.read_text(encoding="utf-8")
    _shared(places).write_text("rules: [{match: a, category: Groceries}]\n", encoding="utf-8")
    with pytest.raises(settings.SettingsError, match="could not be changed"):
        settings.rename_category(conn, places, name="Prestiti", to="Mutuo", now=NOW)
    assert ("Anna Meier", "Prestiti") in _categories_of(conn)
    assert places.categories_file.read_text(encoding="utf-8") == before
    assert _own(places).read_text(encoding="utf-8") == OWN
    assert list((places.database.parent / "backups").iterdir()) == []


def test_a_category_merged_gives_its_rows_and_rules_the_other(
    conn: sqlite3.Connection, places: settings.Places
) -> None:
    rows = settings.merge_category(
        conn, places, name="Shipping", into=categories.OTHER_SHOPPING, now=NOW
    )
    assert rows == 1
    assert ("POSTA", categories.OTHER_SHOPPING) in _categories_of(conn)
    assert "Shipping" not in [e.name for e in categories.load(places.categories_file)]
    assert 'category: "Other shopping"}' in _shared(places).read_text(encoding="utf-8")
    assert "# --- Shops" in _shared(places).read_text(encoding="utf-8")
    assert "Shipping" not in [c.name for c in settings.listed_categories(conn, None)]


def test_a_built_in_category_merged_stays_empty(
    conn: sqlite3.Connection, places: settings.Places
) -> None:
    assert (
        settings.merge_category(conn, places, name=categories.GROCERIES, into="Prestiti", now=NOW)
        == 2
    )
    assert [c for d, c in _categories_of(conn) if d == "KONSUM"] == ["Prestiti"]
    listed = settings.listed_categories(conn, places.categories_file)
    assert (categories.GROCERIES, 0) in [(c.name, c.rows) for c in listed]


@pytest.mark.parametrize(
    ("name", "into", "problem"),
    [
        ("Nothing", categories.GROCERIES, "no category 'Nothing'"),
        (categories.GROCERIES, categories.GROCERIES, "into another one"),
        (
            categories.GROCERIES,
            categories.OWN_TRANSFER,
            "Groceries is an expense and Own transfer is not",
        ),
        (categories.SALARY, categories.GROCERIES, "Salary is income and Groceries is not"),
        (
            categories.TRADING,
            categories.GROCERIES,
            "Trading is income and Groceries is not",
        ),  # on both sides
    ],
)
def test_a_merge_that_cannot_be_made_says_why(
    conn: sqlite3.Connection, places: settings.Places, name: str, into: str, problem: str
) -> None:
    with pytest.raises(settings.SettingsError, match=problem):
        settings.merge_category(conn, places, name=name, into=into, now=NOW)


def test_an_added_category_nothing_gives_can_be_removed(
    conn: sqlite3.Connection, places: settings.Places
) -> None:
    staging.add_category(conn, places.categories_file, name="Mance", side="entrate", group="")
    staging.add_category(conn, places.categories_file, name="Gatto", side="uscite", group="")
    settings.remove_category(conn, places, name="Mance")
    settings.remove_category(conn, places, name="Gatto")
    names = [e.name for e in categories.load(places.categories_file)]
    assert names == ["Shipping", "Prestiti"]
    assert "Gatto" not in categories.groups(categories.load(places.categories_file))
    found = conn.execute("SELECT count(*) FROM categories WHERE name IN ('Mance', 'Gatto')")
    assert found.fetchone()[0] == 0


@pytest.mark.parametrize(
    ("name", "problem"),
    [("Shipping", "1 rows carry"), (categories.GROCERIES, "is built in")],
)
def test_a_category_in_use_is_not_removed(
    conn: sqlite3.Connection, places: settings.Places, name: str, problem: str
) -> None:
    with pytest.raises(settings.SettingsError, match=problem):
        settings.remove_category(conn, places, name=name)
    assert "Shipping" in [e.name for e in categories.load(places.categories_file)]


def test_a_category_a_rule_gives_is_not_removed(
    conn: sqlite3.Connection, places: settings.Places
) -> None:
    staging.add_category(conn, places.categories_file, name="Mance", side="entrate", group="")
    _own(places).write_text("rules:\n  - {match: 'TIP', category: Mance}\n", encoding="utf-8")
    with pytest.raises(settings.SettingsError, match="1 rules give Mance"):
        settings.remove_category(conn, places, name="Mance")
    assert "Mance" in [e.name for e in categories.load(places.categories_file)]


@pytest.mark.parametrize(
    "change",
    [
        lambda conn, places: settings.rename_category(
            conn, places, name="Prestiti", to="Mutuo", now=NOW
        ),
        lambda conn, places: settings.merge_category(
            conn, places, name="Prestiti", into=categories.GIFTS, now=NOW
        ),
        lambda conn, places: settings.remove_category(conn, places, name="Prestiti"),
    ],
)
def test_the_category_of_what_no_rule_matches_stays(
    conn: sqlite3.Connection, places: settings.Places, change: Any
) -> None:
    kept = dataclasses.replace(places, unmatched="Prestiti")
    with pytest.raises(settings.SettingsError, match="change it there first"):
        change(conn, kept)
    assert "Prestiti" in [e.name for e in categories.load(places.categories_file)]


def test_a_category_moved_under_another_keeps_its_rows(
    conn: sqlite3.Connection, places: settings.Places
) -> None:
    assert (
        settings.move_category(conn, places, name="Prestiti", under=categories.GROUP_HOME, now=NOW)
        == 1
    )
    added = categories.load(places.categories_file)
    assert categories.Extra("Prestiti", "uscite", categories.GROUP_HOME) in added
    assert "Prestiti" not in categories.groups(added)  # the group of its own went
    listed = settings.listed_categories(conn, places.categories_file)
    assert ("Prestiti", categories.GROUP_HOME, 1) in [(c.name, c.parent, c.rows) for c in listed]
    found = conn.execute("SELECT count(*) FROM categories WHERE name = 'Prestiti'").fetchone()
    assert found[0] == 1  # the leaf, and no empty group
    settings.move_category(conn, places, name="Prestiti", under=" Debiti ", now=NOW)
    assert categories.Extra("Prestiti", "uscite", "Debiti") in categories.load(
        places.categories_file
    )
    query.check_names(conn, query.Filter(category="Debiti"))


@pytest.mark.parametrize(
    ("name", "under", "problem"),
    [
        (categories.GROCERIES, categories.GROUP_HOME, "is built in"),
        ("Mance", categories.GROUP_HOME, "is income"),
        ("Shipping", categories.GROUP_TRANSPORT, "under Transport already"),
        ("Shipping", " ", "which top-level category"),
        ("Shipping", categories.GROCERIES, "a category of its own"),
    ],
)
def test_a_move_that_cannot_be_made_says_why(
    conn: sqlite3.Connection, places: settings.Places, name: str, under: str, problem: str
) -> None:
    staging.add_category(conn, places.categories_file, name="Mance", side="entrate", group="")
    with pytest.raises(settings.SettingsError, match=problem):
        settings.move_category(conn, places, name=name, under=under, now=NOW)


def test_a_change_whose_files_cannot_be_put_back_keeps_the_copy(
    conn: sqlite3.Connection, places: settings.Places, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: object) -> int:
        raise settings.SettingsError("the rules could not be changed")

    def unwritable(_: object) -> None:
        raise PermissionError("read-only folder")

    monkeypatch.setattr(settings, "_recategorize", fail)
    monkeypatch.setattr(settings, "_put_back", unwritable)
    with pytest.raises(settings.SettingsError, match="kept in") as raised:
        settings.rename_category(conn, places, name="Prestiti", to="Mutuo", now=NOW)
    assert "could not all be put back as they were (read-only folder)" in str(raised.value)
    (copy,) = (places.database.parent / "backups").iterdir()
    assert str(copy) in str(raised.value)
    assert ("Anna Meier", "Prestiti") in _categories_of(conn)  # the database rolled back
