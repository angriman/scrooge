"""Statements staged and committed by hand for the page tests: no files needed.

Kept here, next to the tests that use them, so that the pages do not depend on how the
storage tests build their own."""

import re
import sqlite3
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from httpx2 import Response
from starlette.testclient import TestClient

from scrooge import importing, staging
from scrooge.models import Kind, Transaction
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.conftest import FILES, INSTRUMENTS_YAML, MATURED, write_rules
from tests.storage import helpers
from tests.storage.helpers import OWNER, PARTNER, PEOPLE, txn

SITE = "http://127.0.0.1:8765"
CLICKED = datetime(2026, 10, 12, 9, 30, tzinfo=UTC)
BUILT = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)  # when the databases of these tests were made
Upload = tuple[str, tuple[str, bytes]]

Files = tuple[str, Sequence[Transaction]]


def stage_rows(  # whose statement, which days, what in it, where it begins, when
    conn: sqlite3.Connection,
    owner: str,
    span: tuple[date, date],
    *files: Files,
    opening: str = "0",
    now: datetime = BUILT,
) -> None:
    """Stage one statement per `(source, rows)` that covers `span`; its account opens with
    `opening` and closes with what the rows add up to."""
    helpers.stage_rows(conn, owner, span, *files, opening=opening, now=now)


def close(
    conn: sqlite3.Connection,
    owner: str,
    span: tuple[date, date],
    *files: Files,
    opening: str = "0",
    now: datetime = BUILT,
) -> None:
    """Stage statements that cover `span` and commit everything staged for `owner`."""
    stage_rows(conn, owner, span, *files, opening=opening, now=now)
    staging.commit(conn, owner=owner, now=now)


# Lukas: August committed; September staged with a categorized transfer, a categorized
# purchase and one row waiting for a category. Anna: September staged.
AUGUST_DAYS = (date(2026, 8, 1), date(2026, 8, 31))
SEPTEMBER_DAYS = (date(2026, 9, 1), date(2026, 9, 30))
LUKAS_AUGUST = [
    txn("-45.20", date=date(2026, 8, 3), source_ref="1"),
    txn("6000", date=date(2026, 8, 25), source_ref="2", kind=Kind.INCOME, category="Stipendio"),
    txn(
        "-1000",
        date=date(2026, 8, 28),
        source_ref="3",
        kind=Kind.TRANSFER,
        category="Giroconto",
        description="SAVINGS TOPUP AUG",
    ),
]
LUKAS_SEPTEMBER = [
    txn("-12.50", date=date(2026, 9, 4), source_ref="4", counterparty="Bakery"),
    txn("-80", date=date(2026, 9, 5), source_ref="5", category="", counterparty="<i>Kiosk</i>"),
    txn(
        "-1000",
        date=date(2026, 9, 20),
        source_ref="6",
        kind=Kind.TRANSFER,
        category="Giroconto",
        description="SAVINGS TOPUP",
    ),
]
ANNA_SEPTEMBER = [txn("-60", date=date(2026, 9, 12), source_ref="1")]
AUGUST_CLOSING = "4954.80"


def household(path: Path) -> None:
    """The database described above, at `path`."""
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    close(conn, OWNER, AUGUST_DAYS, ("migros", LUKAS_AUGUST))
    stage_rows(conn, OWNER, SEPTEMBER_DAYS, ("migros", LUKAS_SEPTEMBER), opening=AUGUST_CLOSING)
    stage_rows(conn, PARTNER, SEPTEMBER_DAYS, ("migros", ANNA_SEPTEMBER))
    conn.close()


def row_id(database: Path, owner: str, what: str) -> str:
    """The id of the one row of `owner` whose counterparty or description is `what`."""
    conn = sqlite3.connect(database)
    (found,) = conn.execute(
        "SELECT t.id FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "JOIN owners o ON o.id = a.owner_id "
        "WHERE o.name = ? AND ? IN (t.counterparty, t.description) "
        "ORDER BY t.date",
        (owner, what),
    ).fetchall()
    conn.close()
    return str(found[0])


def file_id(database: Path, owner: str, committed: bool) -> int:
    """The id of the one statement of `owner` that is committed (or staged)."""
    conn = sqlite3.connect(database)
    (found,) = conn.execute(
        "SELECT f.id FROM statement_files f JOIN owners o ON o.id = f.owner_id "
        "WHERE o.name = ? AND (f.committed_at IS NOT NULL) = ?",
        (owner, committed),
    ).fetchall()
    conn.close()
    return int(found[0])


def token(page: str) -> str:
    found = re.search(r'name="token" value="([^"]+)"', page)
    assert found
    return found.group(1)


def category_of(database: Path, row: str) -> tuple[str | None, str | None]:
    """The category of a row and where it came from."""
    conn = sqlite3.connect(database)
    (found,) = conn.execute(
        "SELECT c.name, t.category_source FROM transactions t "
        "LEFT JOIN categories c ON c.id = t.category_id WHERE t.id = ?",
        (row,),
    ).fetchall()
    conn.close()
    return (found[0], found[1])


def committed_files(database: Path) -> list[str]:
    """Owner and name of every committed statement."""
    conn = sqlite3.connect(database)
    found = conn.execute(
        "SELECT o.name, f.filename FROM statement_files f JOIN owners o ON o.id = f.owner_id "
        "WHERE f.committed_at IS NOT NULL ORDER BY 1, 2"
    ).fetchall()
    conn.close()
    return [f"{owner} {name}" for owner, name in found]


class Site:
    """The app over an empty database, set up to import the fixture month."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.database = folder / "data" / "scrooge.db"
        self.database.parent.mkdir()
        conn = db.connect(self.database)
        db.migrate(conn, CLICKED)
        conn.close()
        instruments = folder / "instruments.yaml"
        instruments.write_text(INSTRUMENTS_YAML + MATURED, encoding="utf-8")
        self.setup = importing.Setup([write_rules(folder)], instruments, PEOPLE, {})
        self.client = self.app()

    def app(self, **settings: object) -> TestClient:
        made = webapp.create_app(self.database, clock=lambda: CLICKED, **settings)  # type: ignore[arg-type]  # each test names the settings it changes
        made.state.token = self.token
        return TestClient(made, base_url=SITE, follow_redirects=False)

    def uploads(self, *names: str) -> list[Upload]:
        return [("file", (name, (self.folder / name).read_bytes())) for name in names or FILES]

    def send(self, files: list[Upload], owner: str = "Lukas", **fields: str) -> Response:
        form = {"token": self.token, "owner": owner} | fields
        return self.client.post("/import", data=form, files=files)

    @property
    def token(self) -> str:
        """What every form of the pages sends back; the same for every app of a test."""
        return "the-token-of-these-tests"

    def count(self, table: str) -> int:
        conn = sqlite3.connect(self.database)
        try:
            return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])  # noqa: S608  # table names are literals in this file
        finally:
            conn.close()

    def kept(self) -> int:
        folder = self.database.parent / "originals"
        return len(list(folder.iterdir())) if folder.is_dir() else 0
