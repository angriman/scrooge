"""The app over a copy of the database (`serve: {read_only: true}`): it shows, and changes
nothing, wherever the copy lies."""

import os
import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze import monthclose
from finanze.models import CashBalance
from finanze.storage import db
from finanze.web import app as webapp
from tests.storage.helpers import AUGUST, CLOSED_AT, OWNER, PARTNER, batch, close, statement
from tests.web.test_app import text
from tests.web.test_login import STORED, sign_in
from tests.web.test_month_page import ANNA, LUKAS, SITE, status

COPY = "This is a copy: changes are made on the machine that holds the data."
PAGES = [  # in francs, the one currency of these rows: no exchange rate is needed
    "/?currency=CHF",
    "/networth?currency=CHF",
    "/networth.json?currency=CHF",
    "/holdings?currency=CHF",
    "/cashflow",
    "/months",
]
MONTHS = ["/months/Lukas/2026-08", "/months/Lukas/2026-09", "/months/Anna/2026-09"]
FORMS = [
    "/months/Lukas/2026-08/reopen",
    "/months/Anna/2026-09/commit",
    "/months/Lukas/2026-09/categorize",
]


def fill(path: Path) -> None:
    """Lukas: August committed, September open with a row to categorize. Anna: September open."""
    conn = db.connect(path)
    db.migrate(conn, CLOSED_AT)
    close(conn, OWNER, (AUGUST, date(2026, 9, 30)), AUGUST, ("migros", LUKAS))
    hers = statement("migros", ANNA, [CashBalance("Migros", "CHF", Decimal(0), Decimal(-60))])
    monthclose.stage(conn, batch(hers, owner=PARTNER), CLOSED_AT)
    conn.close()


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
    assert "<form" not in response.text
    assert "<button" not in response.text
    assert "<select" not in response.text
    assert text(response.text).count(COPY) == 1
    assert "Reopen" not in response.text
    assert "is saved first" not in response.text


def test_a_copy_still_says_what_a_month_is_waiting_for(client: TestClient) -> None:
    page = text(client.get("/months/Lukas/2026-09").text)
    assert "It cannot be committed yet: 1 transaction still needs a category" in page
    assert "2026-09-05 · Migros · -80.00 CHF Not set here." in page
    assert "Committed on 2026-10-08." in text(client.get("/months/Lukas/2026-08").text)
    assert "Open, and nothing stands in the way." in text(client.get("/months/Anna/2026-09").text)


def test_the_app_that_holds_the_data_does_not_call_itself_a_copy(database: Path) -> None:
    client = TestClient(webapp.create_app(database), base_url=SITE)
    for path in MONTHS:
        page = client.get(path).text
        assert "<form" in page
        assert "copy:" not in page


@pytest.mark.parametrize("path", FORMS)
def test_a_copy_refuses_every_form_that_would_change_it(
    client: TestClient, database: Path, path: str
) -> None:
    before = status(database), database.read_bytes()
    good = {"token": client.app.state.token}  # type: ignore[attr-defined]  # the Starlette app
    form = good | {"id": "x", "category": "Regali", "remember": "rows"}
    response = client.post(path, data=form)
    assert response.status_code == 403
    assert response.text == f"{COPY}\n"
    # The token is still asked for first: a page of another site learns nothing either way.
    assert client.post(path, data=form | {"token": "guessed"}).text == "Forbidden"
    elsewhere = client.post(path, data=form, headers={"origin": "https://evil.example"})
    assert elsewhere.text == "Forbidden"
    assert (status(database), database.read_bytes()) == before
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
    assert page.count("<form") == 1  # signing out is all that is left to send
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
    db.migrate(conn, CLOSED_AT)
    conn.close()
    newer.replace(database)
    after = client.get("/months").text
    assert "Lukas" not in after
    assert "Nothing is imported yet" in after
