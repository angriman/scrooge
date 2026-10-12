import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from httpx2 import Response
from starlette.testclient import TestClient

from finanze import importing, monthclose
from finanze.storage import db
from finanze.web import app as webapp
from tests.conftest import FILES, INSTRUMENTS_YAML, write_rules
from tests.storage.helpers import PEOPLE
from tests.web.test_app import text

SITE = "http://127.0.0.1:8765"
CLICKED = datetime(2026, 10, 12, 9, 30, tzinfo=UTC)
MATURED = "  912797YY2: {maturity: 2026-09-16}\n"
Upload = tuple[str, tuple[str, bytes]]


class Site:
    """The app over an empty database, set up to import the fixture month."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.database = folder / "data" / "finanze.db"
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


@pytest.fixture
def site(folder: Path) -> Site:
    made = Site(folder)
    made.client = made.app(imports=made.setup)
    return made


def test_the_page_asks_whose_statements_and_which_files(site: Site) -> None:
    response = site.client.get("/import", params={"owner": "Anna"})
    assert response.status_code == 200
    assert 'enctype="multipart/form-data"' in response.text
    assert '<input type="file" name="file" multiple required>' in response.text
    assert "<option>Lukas</option>" in response.text
    assert "<option selected>Anna</option>" in response.text
    assert (
        f'<a href="{SITE}/import" aria-current="page">Import</a>' in site.client.get("/import").text
    )


def test_uploaded_statements_are_staged_and_their_files_kept(site: Site) -> None:
    response = site.send([*site.uploads(), *site.uploads("notes.txt")])
    assert response.status_code == 200
    page = text(response.text)
    assert "September 2026 for Lukas: 6 statements reconciled" in page
    assert "migros Migros.csv 2026-09-01 to 2026-09-30" in page
    assert "notes.txt is not a statement and was left out" in page
    assert "Revolut EUR includes rows with no balance to check against" in page
    assert f'<a href="{SITE}/months/Lukas/2026-09">2026-09</a>' in response.text
    assert site.count("transactions") > 0
    assert site.count("committed_transactions") == 0  # staged: the month page commits
    assert site.count("statement_files") == len(FILES)
    assert site.kept() == len(FILES)
    assert list(site.folder.glob("**/*.part")) == []


def test_what_does_not_reconcile_is_refused_whole(site: Site) -> None:
    migros = site.folder / "Migros.csv"
    tampered = migros.read_text(encoding="utf-8").replace("-1288.18", "-1288.19")
    migros.write_text(tampered, encoding="utf-8")
    response = site.send(site.uploads())
    assert response.status_code == 409
    assert "Nothing was imported: Migros.csv" in text(response.text)
    assert "<form" in response.text
    assert site.count("transactions") == 0
    assert site.kept() == 0


def test_a_committed_month_is_left_as_it_is_and_what_differs_is_said(site: Site) -> None:
    assert site.send(site.uploads("Revolut Savings.tsv")).status_code == 200
    conn = db.connect(site.database)
    monthclose.commit(conn, owner="Lukas", month=date(2026, 9, 1), now=CLICKED)
    conn.close()
    before = site.count("transactions")
    same = text(site.send(site.uploads("Revolut Savings.tsv")).text)
    assert "0 transactions staged" in same
    assert "5 rows are booked in committed months and stored there: left as they are." in same
    assert "Nothing new: every month these files cover is committed." in same
    assert "Differs" not in same

    savings = site.folder / "Revolut Savings.tsv"
    lines = savings.read_text(encoding="utf-8").splitlines(keepends=True)
    without = [line for line in lines if not line.startswith("Sep 29, 2026, 2:09")]  # rows short
    savings.write_text("".join(without), encoding="utf-8")
    short = site.send(site.uploads("Revolut Savings.tsv"))
    assert short.status_code == 200
    assert "is committed and is not in Revolut Savings.tsv." in text(short.text)
    assert "Nothing was changed in the committed months" in text(short.text)
    assert site.count("transactions") == before


def test_the_form_needs_a_person_and_a_file(site: Site) -> None:
    nobody = site.send(site.uploads("Migros.csv"), owner="Zoe")
    assert nobody.status_code == 400
    assert "Zoe is nobody the config names" in text(nobody.text)
    nothing = site.send([])
    assert nothing.status_code == 400
    assert "Choose at least one file" in text(nothing.text)
    assert site.count("transactions") == 0


def test_two_files_of_one_name_are_told_apart(site: Site) -> None:
    first, second = site.uploads("Migros.csv", "Revolut Savings.tsv")
    response = site.send([first, ("file", ("Migros.csv", second[1][1]))])
    assert response.status_code == 200
    page = text(response.text)
    assert "migros 1/Migros.csv" in page
    assert "revolut savings 2/Migros.csv" in page


def test_a_form_from_elsewhere_or_without_the_token_is_refused(site: Site) -> None:
    files = site.uploads("Migros.csv")
    assert site.send(files, token="guessed").status_code == 403  # noqa: S106  # not a password
    form = {"token": site.token, "owner": "Lukas"}
    elsewhere = {"origin": "https://evil.example"}
    assert site.client.post("/import", data=form, files=files, headers=elsewhere).status_code == 403
    assert site.count("transactions") == 0


def test_a_copy_imports_nothing(site: Site) -> None:
    copy = site.app(imports=site.setup, read_only=True)
    assert "Import</a>" not in copy.get("/months").text
    assert copy.get("/import").status_code == 404
    form = {"token": site.token, "owner": "Lukas"}
    assert copy.post("/import", data=form, files=site.uploads("Migros.csv")).status_code == 404
    assert site.count("transactions") == 0


def test_without_its_setup_the_app_has_no_import_page(site: Site) -> None:
    plain = site.app()
    assert "Import</a>" not in plain.get("/months").text
    assert plain.get("/import").status_code == 404


def test_a_pension_certificate_is_taken_and_what_was_read_is_shown(
    site: Site, pillar2: Path
) -> None:
    scan = ("file", ("Scan 0001.pdf", (pillar2 / "Scan 0001.pdf").read_bytes()))
    response = site.send([scan])
    assert response.status_code == 200
    page = text(response.text)
    assert "Pillar 2 AXA of Lukas: " in page
    assert "read off Scan 0001.pdf." in page
    assert "statements reconciled" not in page
    assert site.count("balances") > 0
    assert site.kept() == 1
