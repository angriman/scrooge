"""`scrooge migrate --dry-run` as a service: the migrations tried on a copy."""

import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scrooge import dryrun
from scrooge.doctor import Check
from scrooge.dryrun import Step
from scrooge.storage import db

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
_EVERY = db.migrations


def older(path: Path, monkeypatch: pytest.MonkeyPatch, behind: int = 2) -> int:
    """A database as code `behind` migrations older left it, with a row in it; its version."""
    kept = _EVERY()[: len(_EVERY()) - behind]
    conn = db.connect(path)
    with monkeypatch.context() as patch:
        patch.setattr(db, "migrations", lambda: kept)
        db.migrate(conn, NOW)
    conn.execute("INSERT INTO owners (name) VALUES ('Lukas')")
    conn.close()
    return kept[-1][0]


def left_in_temp() -> set[str]:
    return {path.name for path in Path(tempfile.gettempdir()).glob("scrooge-dry-run-*")}


def test_the_pending_migrations_are_tried_on_a_copy_and_the_database_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scrooge.db"
    was = older(path, monkeypatch)
    before, temps = path.read_bytes(), left_in_temp()
    tried = dryrun.run(path, NOW)
    names = db.names()
    latest = db.latest()
    assert tried == dryrun.DryRun(
        was,
        latest,
        (Step(latest - 1, names[latest - 1]), Step(latest, names[latest])),
        Check("integrity", "no damage found"),
    )
    assert tried.ok
    assert path.read_bytes() == before
    assert [file.name for file in tmp_path.iterdir()] == ["scrooge.db"]  # nothing beside it
    assert left_in_temp() == temps  # the copy is gone


def test_a_database_at_this_code_s_schema_has_nothing_to_try(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    db.create(path, NOW)
    tried = dryrun.run(path, NOW)
    assert (tried.found, tried.latest, tried.steps, tried.ok) == (db.latest(),) * 2 + ((), True)


def test_a_migration_that_fails_on_the_copy_is_said_and_those_after_it_are_not_tried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scrooge.db"
    was = older(path, monkeypatch, behind=0)
    every = _EVERY()
    broken = (was + 1, "CREATE TABLE owners (x INTEGER) STRICT;")
    after = (was + 2, "CREATE TABLE later (x INTEGER) STRICT;")
    monkeypatch.setattr(db, "migrations", lambda: [*every, broken, after])
    before = path.read_bytes()
    tried = dryrun.run(path, NOW)
    assert tried.steps == (
        Step(was + 1, f"{was + 1:04d}", "table owners already exists"),
        Step(was + 2, f"{was + 2:04d}", tried=False),
    )
    assert tried.integrity.problems == ()
    assert not tried.ok
    assert path.read_bytes() == before


def test_a_python_migration_that_raises_without_a_message_is_named_by_its_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scrooge.db"
    was = older(path, monkeypatch, behind=0)

    def fails(conn: sqlite3.Connection) -> None:
        raise KeyError

    every = _EVERY()
    monkeypatch.setattr(db, "migrations", lambda: [*every, (was + 1, fails)])
    (step,) = dryrun.run(path, NOW).steps
    assert step.failed == "KeyError"


def test_a_copy_with_damage_does_not_come_through(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    db.create(path, NOW)
    conn = db.connect(path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO accounts (owner_id, name, kind) VALUES (9, 'X', 'broker_securities')")
    conn.close()
    tried = dryrun.run(path, NOW)
    assert tried.steps == ()
    assert tried.integrity.problems == ("a row of accounts refers to a missing row of owners",)
    assert not tried.ok


def test_a_database_newer_than_the_code_or_no_database_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    db.create(path, NOW)
    conn = db.connect(path)
    conn.execute("INSERT INTO schema_version VALUES (99, 'x')")
    conn.close()
    with pytest.raises(db.MigrationError, match="schema version 99, this code knows up to"):
        dryrun.run(path, NOW)
    path.write_text("not a database, only text long enough to look like a header" * 20)
    with pytest.raises(db.MigrationError, match="cannot be read \\(file is not a database\\)"):
        dryrun.run(path, NOW)
