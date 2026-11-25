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


def _token(client: TestClient) -> dict[str, str]:
    return {"X-Token": client.get("/api/session").json()["token"]}


def test_a_logo_is_uploaded_through_the_api_and_every_logo_is_sent_back(database: Path) -> None:
    client = _client(webapp.create_app(database, lambda: NOW))
    sent = client.post(
        "/api/logos",
        data={"symbol": "GADG"},
        files={"file": ("../gadg.svg", LOGO, "image/svg+xml")},
        headers=_token(client),
    )
    assert sent.status_code == 200
    assert sent.json()["errors"] == []
    assert sorted(sent.json()["logos"]) == ["GADG", "WIDG"]
    assert client.get("/logos/GADG").content == LOGO
    conn = db.connect(database)
    where = "SELECT source FROM logos WHERE symbol = 'GADG'"
    assert conn.execute(where).fetchone()[0] == "gadg.svg"
    conn.close()


@pytest.mark.parametrize(
    ("symbol", "files", "why"),
    [
        ("GADG", {"file": ("x.svg", b"not an image", "image/svg+xml")}, "neither a PNG nor an SVG"),
        ("GADG", {"other": ("x", b"x")}, "the file is empty"),
        ("NOPE", {"file": ("x.svg", LOGO, "image/svg+xml")}, "no instrument has this symbol"),
    ],
)
def test_an_image_the_api_cannot_keep_says_why(
    database: Path, symbol: str, files: dict[str, tuple[str, bytes]], why: str
) -> None:
    client = _client(webapp.create_app(database, lambda: NOW))
    sent = client.post("/api/logos", data={"symbol": symbol}, files=files, headers=_token(client))
    assert sent.status_code == 409
    (error,) = sent.json()["errors"]
    assert error.startswith("No logo was kept: ")
    assert why in error


def test_a_form_the_api_cannot_read_keeps_no_logo(database: Path) -> None:
    client = _client(webapp.create_app(database, lambda: NOW))
    files = [("file", ("a.svg", LOGO, "image/svg+xml")), ("file", ("b.svg", LOGO, "image/svg+xml"))]
    sent = client.post("/api/logos", data={"symbol": "GADG"}, files=files, headers=_token(client))
    assert sent.status_code == 400
    assert sent.json()["errors"][0].startswith("No logo was kept: ")
    assert client.get("/logos/GADG").status_code == 404


def test_a_logo_is_removed_through_the_api(database: Path) -> None:
    client = _client(webapp.create_app(database, lambda: NOW))
    token = _token(client)
    gone = client.post("/api/logos/remove", json={"symbol": "WIDG"}, headers=token)
    assert gone.json() == {"logos": {}, "errors": []}
    assert client.get("/logos/WIDG").status_code == 404
    again = client.post("/api/logos/remove", json={"symbol": "WIDG"}, headers=token)
    assert again.status_code == 409
    assert again.json()["errors"] == ["WIDG: no logo to remove."]
    garbled = client.post("/api/logos/remove", content=b"[1]", headers=token)
    assert garbled.status_code == 400


def test_the_api_keeps_no_logo_without_the_token_or_over_a_copy(database: Path) -> None:
    client = _client(webapp.create_app(database, lambda: NOW))
    files = {"file": ("x.svg", LOGO, "image/svg+xml")}
    assert client.post("/api/logos", data={"symbol": "GADG"}, files=files).status_code == 403
    assert client.post("/api/logos/remove", json={"symbol": "WIDG"}).status_code == 403
    copy = _client(webapp.create_app(database, lambda: NOW, read_only=True))
    token = _token(copy)
    sent = copy.post("/api/logos", data={"symbol": "GADG"}, files=files, headers=token)
    assert sent.status_code == 403
    assert copy.post("/api/logos/remove", json={"symbol": "WIDG"}, headers=token).status_code == 403
    assert client.get("/logos/WIDG").status_code == 200
