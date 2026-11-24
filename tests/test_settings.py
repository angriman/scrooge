"""The rules and the categories as the settings page changes them: in their YAML files where
they are, and on the committed rows."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from scrooge import categories, query, settings, staging
from scrooge.models import Kind
from scrooge.storage import db
from tests.storage import helpers
from tests.storage.helpers import OWNER, PARTNER, txn

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
AUGUST = (date(2026, 8, 1), date(2026, 8, 31))
SHARED = """# Rules both share.
rules:
  # --- Shops
  - {match: 'KONSUM', category: Spesa}  # food
  - {match: 'POSTA', category: Spedizioni}
  - {match: 'ACME', category: Stipendio, direction: in}
"""
OWN = """rules:
  - {match: 'Anna Meier', category: Prestiti}
"""
CATEGORIES = """# Added by hand.
categories:
  uscite: {}
  entrate: []
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
    for name, group in (("Spedizioni", "Trasporti"), ("Prestiti", "")):
        staging.add_category(
            connection, places.categories_file, name=name, side="uscite", group=group
        )
    day = date(2026, 8, 10)
    lukas = [
        txn("-45", date=day, source_ref="1", description="KONSUM"),
        txn("-10", date=day, source_ref="2", description="POSTA", category="Spedizioni"),
        txn("-200", date=day, source_ref="3", description="Anna Meier", category="Prestiti"),
        txn(
            "6000",
            date=day,
            source_ref="4",
            description="ACME",
            kind=Kind.INCOME,
            category="Stipendio",
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
    _shared(places).write_text("rules:\n  - {match: '(', category: Spesa}\n", encoding="utf-8")
    _own(places).write_text("rules: [\n", encoding="utf-8")
    files = settings.rule_files([_shared(places)], [OWNER], None)
    listed, problems = settings.rules(conn, files)
    assert [(r.match, r.matches) for r in listed] == [("(", None)]
    assert len(problems) == 1
    assert "rules.lukas.yaml" in problems[0]


def test_a_pattern_tried_lists_the_rows_it_matches_newest_first(
    conn: sqlite3.Connection,
) -> None:
    count, rows = settings.try_pattern(conn, "konsum|acme")
    assert count == 3
    assert rows[0].owner == PARTNER  # 20 August, the newest
    count, rows = settings.try_pattern(conn, "konsum|acme", direction="in", owner=OWNER)
    assert (count, [row.description for row in rows]) == (1, ["ACME"])


@pytest.mark.parametrize(
    ("match", "direction", "problem"),
    [("", "", "write a pattern"), ("(", "", "is no pattern"), ("a", "up", "direction")],
)
def test_a_pattern_that_cannot_be_tried_says_why(
    conn: sqlite3.Connection, match: str, direction: str, problem: str
) -> None:
    with pytest.raises(settings.SettingsError, match=problem):
        settings.try_pattern(conn, match, direction=direction)


def _seen(places: settings.Places, position: int, match: str, category: str) -> settings.Seen:
    return settings.Seen(_shared(places), position, match, category)


@pytest.mark.usefixtures("conn")  # which adds the categories the other rules give
def test_a_rule_edited_keeps_the_comments_of_its_file(places: settings.Places) -> None:
    settings.edit_rule(
        _seen(places, 1, "KONSUM", "Spesa"),
        match="KONSUM|COOP",
        category="Ristoranti e bar",
        direction="out",
        categories_file=places.categories_file,
    )
    assert _shared(places).read_text(encoding="utf-8") == SHARED.replace(
        "{match: 'KONSUM', category: Spesa}  # food",
        '{match: "KONSUM|COOP", category: "Ristoranti e bar", direction: out}  # food',
    )


@pytest.mark.parametrize(
    ("match", "category", "direction", "problem"),
    [
        (" ", "Spesa", "", "needs a pattern"),
        ("[", "Spesa", "", "is no pattern"),
        ("KONSUM", "Nothing", "", "no category 'Nothing'"),
        ("KONSUM", "Spesa", "sideways", "direction"),
    ],
)
def test_an_edit_that_cannot_be_made_leaves_the_file(
    places: settings.Places, match: str, category: str, direction: str, problem: str
) -> None:
    with pytest.raises(settings.SettingsError, match=problem):
        settings.edit_rule(
            _seen(places, 1, "KONSUM", "Spesa"),
            match=match,
            category=category,
            direction=direction,
            categories_file=None,
        )
    assert _shared(places).read_text(encoding="utf-8") == SHARED


def test_an_edit_of_a_rule_shown_before_a_change_is_refused(places: settings.Places) -> None:
    with pytest.raises(settings.SettingsError, match="changed since"):
        settings.edit_rule(
            _seen(places, 2, "KONSUM", "Spesa"),
            match="KONSUM",
            category="Spesa",
            direction="",
            categories_file=None,
        )


def test_an_edit_that_would_not_load_is_not_saved(places: settings.Places) -> None:
    broken = "rules:\n  - {match: 'KONSUM', category: Spesa, kind: nonsense}\n"
    _shared(places).write_text(broken, encoding="utf-8")
    with pytest.raises(settings.SettingsError, match="was not saved"):
        settings.edit_rule(
            _seen(places, 1, "KONSUM", "Spesa"),
            match="KONSUM",
            category="Spesa",
            direction="in",
            categories_file=None,
        )
    assert _shared(places).read_text(encoding="utf-8") == broken


def test_a_rule_removed_or_moved_keeps_the_comments(places: settings.Places) -> None:
    settings.move_rule(_seen(places, 3, "ACME", "Stipendio"), -1)
    settings.move_rule(_seen(places, 1, "KONSUM", "Spesa"), 1)
    lines = _shared(places).read_text(encoding="utf-8").splitlines()
    assert lines[:3] == SHARED.splitlines()[:3]
    assert [line.split(",")[0] for line in lines[3:]] == [
        "  - {match: 'ACME'",
        "  - {match: 'KONSUM'",
        "  - {match: 'POSTA'",
    ]
    assert lines[4].endswith("# food")
    settings.remove_rule(_seen(places, 2, "KONSUM", "Spesa"))
    assert "KONSUM" not in _shared(places).read_text(encoding="utf-8")
    assert "# --- Shops" in _shared(places).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("shown", "by", "problem"),
    [
        ((1, "KONSUM", "Spesa"), -1, "is the first"),
        ((3, "ACME", "Stipendio"), 1, "is the last"),
        ((1, "KONSUM", "Spesa"), 2, "one place"),
    ],
)
def test_a_rule_moves_one_place_within_its_file(
    places: settings.Places, shown: tuple[int, str, str], by: int, problem: str
) -> None:
    with pytest.raises(settings.SettingsError, match=problem):
        settings.move_rule(_seen(places, *shown), by)


def test_categories_list_the_taxonomy_then_what_is_added(
    conn: sqlite3.Connection, places: settings.Places
) -> None:
    listed = {(c.name, c.side): c for c in settings.listed_categories(conn, places.categories_file)}
    assert listed["Spesa", "uscite"].rows == 2
    assert listed["Spesa", "uscite"].built_in
    assert listed["Spedizioni", "uscite"] == settings.ListedCategory(
        "Spedizioni", "uscite", "Trasporti", False, 1
    )
    assert listed["Prestiti", "uscite"].parent == "Prestiti"
    assert listed["Stipendio", "entrate"].rows == 1
    assert listed["Giroconto", "transfer"].rows == 0
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
        ("Spesa", "Cibo", "is built in"),
        ("Nothing", "Cibo", "no category 'Nothing'"),
        ("Prestiti", "Spesa", "already a category 'Spesa'"),
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
    _shared(places).write_text("rules: [{match: a, category: Spesa}]\n", encoding="utf-8")
    with pytest.raises(settings.SettingsError, match="could not be changed"):
        settings.rename_category(conn, places, name="Prestiti", to="Mutuo", now=NOW)
    assert ("Anna Meier", "Prestiti") in _categories_of(conn)
    assert places.categories_file.read_text(encoding="utf-8") == before
    assert _own(places).read_text(encoding="utf-8") == OWN
    assert list((places.database.parent / "backups").iterdir()) == []


def test_a_category_merged_gives_its_rows_and_rules_the_other(
    conn: sqlite3.Connection, places: settings.Places
) -> None:
    rows = settings.merge_category(conn, places, name="Spedizioni", into="Acquisti vari", now=NOW)
    assert rows == 1
    assert ("POSTA", "Acquisti vari") in _categories_of(conn)
    assert "Spedizioni" not in [e.name for e in categories.load(places.categories_file)]
    assert 'category: "Acquisti vari"}' in _shared(places).read_text(encoding="utf-8")
    assert "# --- Shops" in _shared(places).read_text(encoding="utf-8")
    assert "Spedizioni" not in [c.name for c in settings.listed_categories(conn, None)]


def test_a_built_in_category_merged_stays_empty(
    conn: sqlite3.Connection, places: settings.Places
) -> None:
    assert settings.merge_category(conn, places, name="Spesa", into="Prestiti", now=NOW) == 2
    assert [c for d, c in _categories_of(conn) if d == "KONSUM"] == ["Prestiti"]
    listed = settings.listed_categories(conn, places.categories_file)
    assert ("Spesa", 0) in [(c.name, c.rows) for c in listed]


@pytest.mark.parametrize(
    ("name", "into", "problem"),
    [
        ("Nothing", "Spesa", "no category 'Nothing'"),
        ("Spesa", "Spesa", "into another one"),
        ("Spesa", "Giroconto", "money between accounts"),
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
    assert names == ["Spedizioni", "Prestiti"]
    assert "Gatto" not in categories.groups(categories.load(places.categories_file))
    found = conn.execute("SELECT count(*) FROM categories WHERE name IN ('Mance', 'Gatto')")
    assert found.fetchone()[0] == 0


@pytest.mark.parametrize(
    ("name", "problem"),
    [("Spedizioni", "1 rows carry"), ("Spesa", "is built in")],
)
def test_a_category_in_use_is_not_removed(
    conn: sqlite3.Connection, places: settings.Places, name: str, problem: str
) -> None:
    with pytest.raises(settings.SettingsError, match=problem):
        settings.remove_category(conn, places, name=name)
    assert "Spedizioni" in [e.name for e in categories.load(places.categories_file)]


def test_a_category_a_rule_gives_is_not_removed(
    conn: sqlite3.Connection, places: settings.Places
) -> None:
    staging.add_category(conn, places.categories_file, name="Mance", side="entrate", group="")
    _own(places).write_text("rules:\n  - {match: 'TIP', category: Mance}\n", encoding="utf-8")
    with pytest.raises(settings.SettingsError, match="1 rules give Mance"):
        settings.remove_category(conn, places, name="Mance")
    assert "Mance" in [e.name for e in categories.load(places.categories_file)]
