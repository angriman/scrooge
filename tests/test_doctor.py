"""`scrooge doctor` as a service: each check, without the command line."""

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scrooge import doctor, importing
from scrooge.doctor import Check
from scrooge.storage import db

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
TODAY = NOW.date()


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "scrooge.db"
    db.create(path, NOW)
    return path


@pytest.fixture
def setup(tmp_path: Path) -> importing.Setup:
    rules = tmp_path / "rules.yaml"
    rules.write_text("rules:\n  - {match: 'BAKERY', category: Groceries}\n", encoding="utf-8")
    people: dict[str, list[re.Pattern[str]]] = {"Lukas": [], "Anna": []}
    return importing.Setup([rules], tmp_path / "instruments.yaml", people, {})


def by_name(checks: tuple[Check, ...]) -> dict[str, Check]:
    return {check.name: check for check in checks}


def change(database: Path, *statements: str) -> None:
    conn = db.connect(database)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for statement in statements:
            conn.execute(statement)
    finally:
        conn.close()


def test_a_new_database_and_its_rules_are_fine(database: Path, setup: importing.Setup) -> None:
    checks = doctor.examine(database, setup, TODAY, ["EUR"])
    assert checks == (
        Check("schema", f"version {db.latest()}, the one this code expects"),
        Check("integrity", "no damage found"),
        Check("rules", "1 rule in 1 file"),
        Check("originals", "the file of each of 0 statements is kept"),
        Check("prices", "a close or a rate of the last week for all that is held"),
    )
    assert doctor.problems(checks) == 0


def test_a_database_at_an_older_schema_is_not_migrated_and_says_so(
    database: Path, setup: importing.Setup
) -> None:
    latest = db.latest()
    change(database, f"DELETE FROM schema_version WHERE version = {latest}")  # noqa: S608  # a number
    before = database.read_bytes()
    checks = by_name(doctor.examine(database, setup, TODAY))
    assert checks["schema"].problems == (f"version {latest - 1}: this code brings it to {latest}",)
    assert "after a copy in backups/" in checks["schema"].advice
    # What reads today's tables is not looked at; the file is left exactly as it was.
    skipped = f"not looked at: they read schema version {latest}"
    assert (checks["originals"].said, checks["prices"].said) == (skipped, skipped)
    assert checks["integrity"].problems == ()
    assert database.read_bytes() == before


def test_a_database_newer_than_the_code_or_no_database_of_ours_is_a_problem(
    database: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    newer = db.latest() + 1
    change(database, f"INSERT INTO schema_version VALUES ({newer}, 'later')")  # noqa: S608  # a number
    (schema, *_) = doctor.examine(database, setup, TODAY)
    assert schema == Check(
        "schema",
        "newer than this code",
        (f"version {newer}: this code knows up to {db.latest()}",),
        "Update scrooge.",
    )
    other = tmp_path / "other.db"
    sqlite3.connect(other).close()
    (schema, *_) = doctor.examine(other, setup, TODAY)
    assert schema.problems == ("this is no scrooge database: it has no schema version",)


def test_a_file_that_is_no_database_cannot_be_read(tmp_path: Path, setup: importing.Setup) -> None:
    path = tmp_path / "scrooge.db"
    path.write_text("not a database, only text long enough to look like a header" * 20)
    found, rules = doctor.examine(path, setup, TODAY)
    assert (found.name, found.said) == ("database", "cannot be read")
    assert found.problems == (f"{path}: file is not a database",)
    assert rules.name == "rules"


def test_a_row_that_refers_to_a_missing_row_is_damage(
    database: Path, setup: importing.Setup
) -> None:
    change(
        database,
        "INSERT INTO accounts (owner_id, name, kind, currency) "
        "VALUES (9, 'X', 'broker_securities', NULL)",
    )
    integrity = by_name(doctor.examine(database, setup, TODAY))["integrity"]
    assert integrity.said == "the database is damaged"
    assert integrity.problems == ("a row of accounts refers to a missing row of owners",)


def test_every_problem_of_every_file_an_import_reads_is_listed(
    database: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    shared = Path(setup.rules[0])
    shared.write_text("rules:\n  - {match: '(', category: Groceries}\n", encoding="utf-8")
    (tmp_path / "rules.anna.yaml").write_text(
        "rules:\n  - {match: 'x', category: Vacanze}\n  - {match: 'y', category: Holidays}\n",
        encoding="utf-8",
    )
    (tmp_path / "categories.yaml").write_text("categories: [\n", encoding="utf-8")
    setup.instruments.write_text("instruments:\n  WIDG: {tab: Shares}\n", encoding="utf-8")
    gone = tmp_path / "gone.yaml"
    wrong = importing.Setup(
        [shared, gone], setup.instruments, setup.people, {}, unmatched="nothing_like_it"
    )
    rules = by_name(doctor.examine(database, wrong, TODAY))["rules"]
    assert rules.said == "an import would be refused"
    assert rules.problems == (
        f"no rules file at {gone}",
        f"{tmp_path / 'categories.yaml'}: line 2: expected the node content, but found "
        "'<stream end>'",
        f"{tmp_path / 'rules.anna.yaml'} rule 1: unknown category 'Vacanze'",
        f"{tmp_path / 'rules.anna.yaml'} rule 2: unknown category 'Holidays'",
        f"{shared} rule 1: missing ), unterminated subpattern at position 0",
        "unmatched: 'nothing_like_it' is no category",
        f"{setup.instruments}: WIDG: unknown tab 'Shares'",
    )


def test_the_rules_count_each_file_once_and_the_categories_added(
    database: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    (tmp_path / "rules.lukas.yaml").write_text(
        "rules:\n  - {match: 'ANNA', category: Gifts}\n  - {match: 'BOOKS', category: Books}\n",
        encoding="utf-8",
    )
    (tmp_path / "categories.yaml").write_text(
        "categories:\n  expenses:\n    Leisure: [Books]\n", encoding="utf-8"
    )
    rules = by_name(doctor.examine(database, setup, TODAY))["rules"]
    assert rules == Check("rules", "3 rules in 2 files, 1 category added")
