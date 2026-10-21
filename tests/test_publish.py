import shutil
import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO

import pytest
from starlette.testclient import TestClient

from finanze import publish, staging
from finanze.models import CashBalance
from finanze.storage import db
from finanze.web import app as webapp
from tests.storage.helpers import AUGUST, CLOSED_AT, OWNER, SEPTEMBER, batch, close, statement, txn

AUGUST_ROWS = [
    txn("-45.20", date=date(2026, 8, 3), source_ref="1"),
    txn("-12", date=date(2026, 8, 20), source_ref="2"),
]
SEPTEMBER_ROWS = [
    txn("-8", date=date(2026, 9, 3), source_ref="3", source_file="migros sep.csv"),
    txn("-3", date=date(2026, 9, 4), source_ref="4", source_file="migros sep.csv"),
]


@pytest.fixture
def database(tmp_path: Path) -> Path:
    """Lukas: August committed with two rows, September staged with two more."""
    path = tmp_path / "data" / "finanze.db"
    path.parent.mkdir()
    conn = db.connect(path)
    db.migrate(conn, CLOSED_AT)
    august = (AUGUST, date(2026, 8, 31))
    close(conn, OWNER, august, august[1], ("migros", AUGUST_ROWS))
    balance = CashBalance("Migros", "CHF", Decimal("-57.20"), Decimal("-68.20"))
    september = statement("migros", SEPTEMBER_ROWS, [balance], month=SEPTEMBER)
    staging.stage(conn, batch(september), CLOSED_AT)
    conn.close()
    return path


@pytest.fixture
def conn(database: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(database)
    yield connection
    connection.close()


@pytest.fixture
def share(tmp_path: Path) -> Path:
    """The other machine's folder, as it is mounted on this one."""
    folder = tmp_path / "share"
    folder.mkdir()
    return folder


def names(folder: Path) -> list[str]:
    return sorted(file.name for file in folder.iterdir())


def owners(path: Path) -> list[str]:
    with db.reading(path) as copy:
        return [row[0] for row in copy.execute("SELECT name FROM owners ORDER BY name")]


def test_the_whole_database_is_published_as_one_file_and_what_it_holds_is_said(
    conn: sqlite3.Connection, database: Path, share: Path
) -> None:
    done = publish.run(conn, database, share)
    assert done == publish.Published(
        path=share / "finanze.db",
        size=(share / "finanze.db").stat().st_size,
        version=db.latest(),
        committed=2,
        staged=2,
        staged_files=1,
    )
    assert done.size > 0
    assert names(share) == ["finanze.db"]
    assert names(database.parent) == ["finanze.db"]  # the snapshot made here is gone
    with db.reading(done.path) as copy:
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert copy.execute("SELECT count(*) FROM transactions").fetchone()[0] == 4


def test_what_is_being_written_here_is_published_as_it_was_last_saved(
    conn: sqlite3.Connection, database: Path, share: Path
) -> None:
    writer = db.connect(database)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO owners (name) VALUES ('Zoe')")
    publish.run(conn, database, share)
    writer.execute("COMMIT")
    writer.close()
    assert owners(share / "finanze.db") == ["Anna", "Lukas"]
    publish.run(conn, database, share)  # again, over the copy that is there
    assert owners(share / "finanze.db") == ["Anna", "Lukas", "Zoe"]
    assert names(share) == ["finanze.db"]


def test_the_copy_is_called_finanze_db_whatever_the_database_is_called(
    database: Path, share: Path
) -> None:
    renamed = database.rename(database.with_name("ours.sqlite"))
    conn = db.connect(renamed)
    assert publish.run(conn, renamed, share).path == share / "finanze.db"
    conn.close()


def test_the_published_copy_is_shown_from_a_folder_nothing_can_be_written_in(
    conn: sqlite3.Connection, database: Path, share: Path
) -> None:
    copy = publish.run(conn, database, share).path
    copy.chmod(0o444)
    share.chmod(0o555)
    try:
        client = TestClient(webapp.create_app(copy, read_only=True), base_url="http://127.0.0.1")
        assert "2026-08" in client.get("/months").text
        assert client.get("/transactions").status_code == 200
    finally:
        share.chmod(0o755)
    assert names(share) == ["finanze.db"]


def test_a_folder_that_is_not_there_is_not_created(
    conn: sqlite3.Connection, database: Path, share: Path
) -> None:
    unmounted = share / "Volumes" / "nas"
    with pytest.raises(publish.PublishError, match="a share that is not mounted"):
        publish.run(conn, database, unmounted)
    assert not unmounted.parent.exists()
    (share / "a file").write_text("x", encoding="utf-8")
    with pytest.raises(publish.PublishError, match="a file: no such folder"):
        publish.run(conn, database, share / "a file")
    assert names(database.parent) == ["finanze.db"]


def test_the_folder_of_the_database_itself_is_refused(
    conn: sqlite3.Connection, database: Path, share: Path
) -> None:
    before = database.read_bytes()
    with pytest.raises(publish.PublishError, match="the database itself is kept there"):
        publish.run(conn, database, database.parent)
    link = share / "link"
    link.symlink_to(database.parent, target_is_directory=True)  # the same folder by another name
    with pytest.raises(publish.PublishError, match="the database itself is kept there"):
        publish.run(conn, database, link)
    assert database.read_bytes() == before
    assert names(database.parent) == ["finanze.db"]


def test_a_copy_that_fails_midway_leaves_the_one_published_before(
    conn: sqlite3.Connection, database: Path, share: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publish.run(conn, database, share)
    before = (share / "finanze.db").read_bytes()
    conn.execute("INSERT INTO owners (name) VALUES ('Zoe')")

    def cut_off(source: BinaryIO, target: BinaryIO) -> None:
        target.write(source.read(1024))
        target.flush()
        assert len(names(share)) == 2  # the half is there, under a name nobody reads
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copyfileobj", cut_off)
    with pytest.raises(publish.PublishError) as failure:
        publish.run(conn, database, share)
    assert str(failure.value) == (
        f"{share}: nothing was published, and what was there is as it was: "
        "[Errno 28] No space left on device"
    )
    assert names(share) == ["finanze.db"]
    assert (share / "finanze.db").read_bytes() == before
    assert owners(share / "finanze.db") == ["Anna", "Lukas"]
    assert names(database.parent) == ["finanze.db"]

    monkeypatch.undo()
    publish.run(conn, database, share)
    assert owners(share / "finanze.db") == ["Anna", "Lukas", "Zoe"]


def test_a_folder_that_cannot_be_written_in_is_said(
    conn: sqlite3.Connection, database: Path, share: Path
) -> None:
    share.chmod(0o555)
    try:
        with pytest.raises(publish.PublishError, match="nothing was published"):
            publish.run(conn, database, share)
    finally:
        share.chmod(0o755)
    assert names(share) == []
    assert names(database.parent) == ["finanze.db"]
