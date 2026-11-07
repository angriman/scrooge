"""The app over a copy of the database (`serve: {read_only: true}`): it shows, and changes
nothing, wherever the copy lies."""

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze.storage import db
from finanze.web import app as webapp
from tests.web.builders import BUILT, SITE, committed_files, file_id, household
from tests.web.test_app import text
from tests.web.test_login import STORED, sign_in

COPY = "This is a copy: changes are made on the machine that holds the data."
PAGES = [  # in francs, the one currency of these rows: no exchange rate is needed
    "/?currency=CHF",
    "/networth.json?currency=CHF",
    "/holdings?currency=CHF",
    "/analysis?currency=CHF",
    "/months",
]
MONTHS = ["/months/Lukas/2026-08", "/months/Lukas/2026-09", "/months/Anna/2026-09"]
FORMS = [
    "/import/save",
    "/import/commit",
    "/import/category",
    "/months/Lukas/2026-08/categorize",
    "/months/Lukas/2026-08/category",
    "/statements/1/discard",
    "/statements/1/uncommit",
]


def fill(path: Path) -> None:
    """Lukas: August committed, September staged. Anna: September staged."""
    household(path)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "copy" / "finanze.db"
    path.parent.mkdir()
    fill(path)
    return path


@pytest.fixture
def client(database: Path) -> TestClient:
    app = webapp.create_app(database, read_only=True)
    return TestClient(app, base_url=SITE, follow_redirects=False)


@pytest.fixture
def locked(database: Path) -> Iterator[Path]:
    """The copy where nothing can be written, neither the file nor a file next to it."""
    database.chmod(0o444)
    database.parent.chmod(0o555)
    yield database
    database.parent.chmod(0o755)
    database.chmod(0o644)


@pytest.mark.parametrize("path", MONTHS)
def test_a_copy_shows_a_month_without_the_forms_that_change_it(
    client: TestClient, path: str
) -> None:
    response = client.get(path)
    assert response.status_code == 200
    page = response.text.partition("<main>")[2]  # the header chooses a currency, nothing more
    assert "<form" not in page
    assert "<button" not in page
    assert "<select" not in page
    assert "Add a category" not in response.text


def test_a_copy_has_no_import_page_and_no_link_to_one(client: TestClient) -> None:
    assert client.get("/import").status_code == 404
    assert "Import</a>" not in client.get("/months").text
    assert client.post("/import", data={"token": "x"}).status_code == 404


def test_a_copy_shows_a_statement_without_the_forms_that_change_it(
    client: TestClient, database: Path
) -> None:
    for committed in (True, False):
        page = client.get(f"/statements/{file_id(database, 'Lukas', committed=committed)}")
        assert page.status_code == 200
        assert "<form" not in page.text.partition("<main>")[2]
        assert "Take it back" not in page.text
        assert "Remove it" not in page.text


def test_a_copy_still_says_how_far_each_account_is_committed(client: TestClient) -> None:
    page = text(client.get("/months").text)
    assert "Lukas Migros Migros Bank account statement 2026-08-31 staged to 2026-09-30" in page
    assert "Committed on 2026-10-08" in text(client.get(f"/statements/{1}").text)


def test_the_app_that_holds_the_data_does_not_call_itself_a_copy(database: Path) -> None:
    client = TestClient(webapp.create_app(database), base_url=SITE)
    assert "<form" in client.get("/import?owner=Lukas").text
    assert "Import</a>" in client.get("/months").text
    assert "copy:" not in client.get("/import?owner=Lukas").text


@pytest.mark.parametrize("path", FORMS)
def test_a_copy_refuses_every_form_that_would_change_it(
    client: TestClient, database: Path, path: str
) -> None:
    before = committed_files(database), database.read_bytes()
    good = {"token": client.app.state.token}  # type: ignore[attr-defined]  # the Starlette app
    form = good | {"id": "x", "category": "Regali", "remember": "rows", "owner": "Lukas"}
    response = client.post(path, data=form)
    assert response.status_code == 403
    assert response.text == f"{COPY}\n"
    # The token is still asked for first: a page of another site learns nothing either way.
    assert client.post(path, data=form | {"token": "guessed"}).text == "Forbidden"
    elsewhere = client.post(path, data=form, headers={"origin": "https://evil.example"})
    assert elsewhere.text == "Forbidden"
    assert (committed_files(database), database.read_bytes()) == before
    assert [file.name for file in database.parent.iterdir()] == ["finanze.db"]  # no backup


def test_a_copy_behind_a_password_asks_for_it_before_it_refuses(database: Path) -> None:
    app = webapp.create_app(database, password=STORED, names=["finanze.home"], read_only=True)
    client = TestClient(app, base_url=SITE, follow_redirects=False)
    form = {"token": app.state.token}
    for path in FORMS:
        unknown = client.post(path, data=form)
        assert (unknown.status_code, unknown.text) == (401, "sign in first\n")
    assert sign_in(client).status_code == 303
    for path in [*PAGES, *MONTHS]:
        assert client.get(path).status_code == 200, path
    for path in FORMS:
        assert client.post(path, data=form).status_code == 403
    page = client.get("/months/Lukas/2026-08").text
    assert page.count('<form method="post"') == 1  # signing out is all that is left to send
    out = client.post("/logout", data=form)
    assert (out.status_code, out.headers["location"]) == (303, "/login")
    assert client.get("/months").status_code == 303


@pytest.mark.parametrize(
    "path", [*PAGES, *MONTHS, "/transactions", "/transactions.csv", "/static/app.css"]
)
def test_a_copy_is_read_where_nothing_can_be_written(locked: Path, path: str) -> None:
    assert not os.access(locked.parent, os.W_OK)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        sqlite3.connect(locked).execute("CREATE TABLE written (x INTEGER)")
    client = TestClient(webapp.create_app(locked, read_only=True), base_url=SITE)
    assert client.get(path).status_code == 200
    assert [file.name for file in locked.parent.iterdir()] == ["finanze.db"]


def test_a_copy_replaced_while_the_app_runs_is_what_the_next_page_shows(
    client: TestClient, database: Path, tmp_path: Path
) -> None:
    assert "Lukas" in client.get("/months").text
    newer = tmp_path / "newer.db"  # as `finanze publish` brings it: whole, then renamed over
    conn = db.connect(newer)
    db.migrate(conn, BUILT)
    conn.close()
    newer.replace(database)
    after = client.get("/months").text
    assert "Lukas" not in after
    assert "Nothing is committed yet" in after
