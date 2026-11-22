"""The marks beside a holding: the user's own logos, kept in the database, the icon of bills,
or its letters; and the holdings page's list of those without a logo."""

import hashlib
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from scrooge.storage import db, logos
from scrooge.web import app as webapp
from tests.test_networth import NOW
from tests.test_overview import household

SITE = "http://127.0.0.1:8765"
LOGO = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1"/></svg>'
)
SHOWN = '<img class="mark" src="/logos/WIDG?v='


@pytest.fixture
def database(tmp_path: Path) -> Path:
    """The household of the overview's tests, a logo for its fund and none for its share."""
    conn = db.connect(tmp_path / "scrooge.db")
    db.migrate(conn, NOW)
    household(conn)
    logos.add(conn, "WIDG", LOGO, "widg.svg", NOW)
    conn.close()
    return tmp_path / "scrooge.db"


def logos_digest() -> str:
    """The part of its digest the address of WIDG's logo carries, then the end of its tag."""
    return hashlib.sha256(LOGO).hexdigest()[:12] + '" alt="" width="24" height="24">'


def _client(app: Starlette) -> TestClient:
    return TestClient(app, base_url=SITE)


def test_a_logo_is_served_from_the_database_and_runs_nothing(database: Path) -> None:
    client = _client(webapp.create_app(database))
    response = client.get("/logos/WIDG")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert response.content == LOGO
    policies = response.headers.get_list("content-security-policy")
    assert "default-src 'none'; style-src 'unsafe-inline'; sandbox" in policies
    for symbol in ("GADG", "..%2Fscrooge.db"):
        assert client.get(f"/logos/{symbol}").status_code == 404


def test_each_holding_wears_its_logo_the_icon_of_bills_or_its_letters(database: Path) -> None:
    client = _client(webapp.create_app(database))
    page = client.get("/").text.replace(SITE, "")
    assert SHOWN in page
    assert page.split(SHOWN)[1].startswith(logos_digest())
    assert '<img class="mark" src="/static/icons/bill.svg" alt="" width="24" height="24">' in page
    assert '<span class="mark letters slot-6" aria-hidden="true">GA</span>' in page


def test_a_new_image_has_a_new_address_so_the_browser_asks_again(database: Path) -> None:
    client = _client(webapp.create_app(database))
    before = client.get("/").text
    conn = db.connect(database)
    logos.add(conn, "WIDG", LOGO.replace(b"1 1", b"2 2"), "", NOW)
    conn.close()
    after = client.get("/").text
    address = "/logos/WIDG?v="
    assert address in before
    assert before.split(address)[1][:12] != after.split(address)[1][:12]


def test_without_a_logo_every_holding_wears_its_letters(database: Path) -> None:
    conn = db.connect(database)
    logos.remove(conn, "WIDG")
    conn.close()
    client = _client(webapp.create_app(database))
    page = client.get("/").text
    assert "/logos/" not in page
    assert '<span class="mark letters slot-5" aria-hidden="true">WI</span>' in page
    assert client.get("/logos/WIDG").status_code == 404


def test_the_holdings_page_lists_the_holdings_without_a_logo_never_the_bills(
    database: Path,
) -> None:
    app = webapp.create_app(database)
    page = _client(app).get("/holdings").text
    assert "1 holding without a logo" in page
    assert '<input type="hidden" name="symbol" value="GADG">' in page
    assert 'name="symbol" value="WIDG"' not in page
    assert f'name="token" value="{app.state.token}"' in page
    assert 'name="symbol" value="912' not in page


def test_a_logo_is_uploaded_for_a_holding_from_its_page(database: Path) -> None:
    app = webapp.create_app(database, lambda: NOW)
    client = _client(app)
    sent = client.post(
        "/holdings/logo?owner=Lukas",
        data={"token": app.state.token, "symbol": "GADG"},
        files={"file": ("../gadg.svg", LOGO, "image/svg+xml")},
        follow_redirects=False,
    )
    assert sent.status_code == 303
    assert sent.headers["location"].endswith("/holdings?owner=Lukas")
    assert client.get("/logos/GADG").content == LOGO
    conn = db.connect(database)
    where = "SELECT source FROM logos WHERE symbol = 'GADG'"
    assert conn.execute(where).fetchone()[0] == "gadg.svg"
    conn.close()
    assert "without a logo" not in client.get("/holdings").text


@pytest.mark.parametrize(
    ("symbol", "image", "why"),
    [
        ("GADG", b"not an image", "neither a PNG nor an SVG"),
        ("GADG", b"", "the file is empty"),
        ("NOPE", LOGO, "no instrument has this symbol"),
    ],
)
def test_an_image_that_cannot_be_kept_is_said_on_the_page(
    database: Path, symbol: str, image: bytes, why: str
) -> None:
    app = webapp.create_app(database, lambda: NOW)
    files = {"file": ("x.svg", image, "image/svg+xml")} if image else {"other": ("x", b"x")}
    sent = _client(app).post(
        "/holdings/logo", data={"token": app.state.token, "symbol": symbol}, files=files
    )
    assert sent.status_code == 409
    assert "No logo was kept" in sent.text
    assert why in sent.text


def test_a_form_from_elsewhere_or_without_the_token_is_refused(database: Path) -> None:
    app = webapp.create_app(database, lambda: NOW)
    client = _client(app)
    files = {"file": ("x.svg", LOGO, "image/svg+xml")}
    data = {"token": "of-an-earlier-start", "symbol": "GADG"}
    assert client.post("/holdings/logo", data=data, files=files).status_code == 403
    elsewhere = {"origin": "http://evil.example"}
    data["token"] = app.state.token
    refused = client.post("/holdings/logo", data=data, files=files, headers=elsewhere)
    assert refused.status_code == 403


def test_a_copy_of_the_database_has_no_form_and_changes_nothing(database: Path) -> None:
    app = webapp.create_app(database, lambda: NOW, read_only=True)
    client = _client(app)
    page = client.get("/holdings").text
    assert "1 holding without a logo" in page
    assert "scrooge logo GADG &lt;file&gt;" in page
    assert 'name="symbol"' not in page
    files = {"file": ("x.svg", LOGO, "image/svg+xml")}
    data = {"token": app.state.token, "symbol": "GADG"}
    sent = client.post("/holdings/logo", data=data, files=files)
    assert sent.status_code == 403
    assert client.get("/logos/GADG").status_code == 404
