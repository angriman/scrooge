"""The JSON of `/api`, and the frontend of its own at /app."""

import base64
import hashlib
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import overview
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER
from tests.test_networth import NOW
from tests.test_overview import household
from tests.web.test_login import STORED

SITE = "http://127.0.0.1:8765"


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("api") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    household(conn)
    conn.close()
    return path


@pytest.fixture(scope="module")
def client(database: Path) -> TestClient:
    return TestClient(webapp.create_app(database, currency="CHF"), base_url=SITE)


def test_the_session_names_the_people_and_the_currencies(client: TestClient) -> None:
    sent = client.get("/api/session").json()
    assert [person["name"] for person in sent["people"]] == sorted([OWNER, PARTNER])
    assert sent["currency"] == "CHF"
    assert "EUR" in sent["currencies"]
    assert sent["read_only"] is False
    assert sent["signing"] is False
    assert sent["token"]


def test_the_overview_is_the_services_figures_as_text(client: TestClient, database: Path) -> None:
    sent = client.get("/api/overview").json()
    with db.reading(database) as conn:
        shown = overview.run(conn, currency="CHF")
    assert sent["errors"] == []
    figures = sent["overview"]
    assert figures["currency"] == "CHF"
    assert shown.day is not None
    assert figures["day"] == shown.day.isoformat()
    assert figures["total"] == format(shown.total, "f")
    assert [part["name"] for part in figures["assets"]] == [part.name for part in shown.assets]
    assert [holding["symbol"] for holding in figures["top"]] == [h.symbol for h in shown.top]
    assert [part["name"] for part in figures["people"]] == sorted([OWNER, PARTNER])
    assert isinstance(sent["checks"], list)


def test_one_persons_overview_has_no_people(client: TestClient) -> None:
    sent = client.get("/api/overview", params={"owner": OWNER, "currency": "EUR"}).json()
    assert sent["overview"]["currency"] == "EUR"
    assert sent["overview"]["people"] == []


def test_a_field_that_cannot_be_read_is_refused(client: TestClient) -> None:
    response = client.get("/api/overview", params={"currency": "XYZ"})
    assert response.status_code == 400
    assert response.json() == {"errors": ["'XYZ' is not one of EUR, CHF, USD, GBP, JPY."]}
    assert client.get("/api/networth", params={"by": "colour"}).status_code == 400


def test_someone_unknown_is_not_found_as_json(client: TestClient) -> None:
    response = client.get("/api/networth", params={"owner": "Nobody"})
    assert response.status_code == 404
    assert response.json() == {"errors": ["No owner named 'Nobody'."]}


def test_net_worth_runs_day_by_day_with_its_parts(client: TestClient) -> None:
    series = client.get("/api/networth", params={"by": "owner"}).json()["series"]
    assert series["currency"] == "CHF"
    assert [part["name"] for part in series["parts"]] == sorted([OWNER, PARTNER])
    assert all(len(part["values"]) == len(series["total"]) for part in series["parts"])
    assert client.get("/api/networth", params={"by": "total"}).json()["series"]["parts"] == []


def test_an_empty_database_has_nothing_to_show(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    conn.close()
    client = TestClient(webapp.create_app(path), base_url=SITE)
    figures = client.get("/api/overview").json()["overview"]
    assert figures["day"] is None
    assert figures["top"] == []
    series = client.get("/api/networth").json()["series"]
    assert series == {
        "currency": "EUR",
        "first": None,
        "total": [],
        "arrived": {},
        "parts": [],
        "notes": [],
    }


def test_a_missing_rate_is_said_and_the_rest_is_shown(client: TestClient) -> None:
    sent = client.get("/api/overview", params={"currency": "JPY"}).json()
    assert sent["overview"] is None
    assert sent["errors"]
    assert client.get("/api/networth", params={"currency": "JPY"}).json()["series"] is None


def test_without_signing_in_the_api_says_so(database: Path) -> None:
    app = webapp.create_app(database, password=STORED)
    response = TestClient(app, base_url=SITE).get("/api/overview", follow_redirects=False)
    assert response.status_code == 401
    assert response.json() == {"errors": ["Sign in first."]}


SHELL = '<html><body><script>start("/app")</script></body></html>'


@pytest.fixture
def spa(tmp_path: Path) -> Path:
    folder = tmp_path / "spa"
    (folder / "_app").mkdir(parents=True)
    (folder / "index.html").write_text(SHELL)
    (folder / "_app" / "start.js").write_text("export {}")
    (tmp_path / "secret.txt").write_text("not served")
    return folder


def test_every_address_under_app_is_its_page_or_one_of_its_files(database: Path, spa: Path) -> None:
    client = TestClient(webapp.create_app(database, spa=spa), base_url=SITE)
    for address in ("/app", "/app/", "/app/holdings"):
        assert client.get(address).text == SHELL
    assert client.get("/app/_app/start.js").text == "export {}"
    assert client.get("/app/..%2Fsecret.txt").text == SHELL  # never a file outside it
    assert client.get("/apple").status_code == 404


def test_the_page_may_run_its_own_script_and_no_other(database: Path, spa: Path) -> None:
    client = TestClient(webapp.create_app(database, spa=spa), base_url=SITE)
    digest = base64.b64encode(hashlib.sha256(b'start("/app")').digest()).decode()
    policy = (
        f"default-src 'self'; script-src 'self' 'sha256-{digest}'; "
        "style-src 'self'; style-src-attr 'unsafe-inline'; frame-ancestors 'none'"
    )
    assert client.get("/app").headers["content-security-policy"] == policy
    assert client.get("/").headers["content-security-policy"] == policy


def test_before_it_is_built_app_says_how_to_build_it(database: Path, tmp_path: Path) -> None:
    client = TestClient(webapp.create_app(database, spa=tmp_path / "none"), base_url=SITE)
    response = client.get("/app/")
    assert response.status_code == 404
    assert "make web" in response.text
    assert client.get("/").headers["content-security-policy"] == (
        "default-src 'self'; frame-ancestors 'none'"
    )


def test_without_a_frontend_there_is_no_app(client: TestClient) -> None:
    assert client.get("/app/").status_code == 404


def test_a_page_without_a_script_of_its_own_changes_no_policy(
    database: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "index.html").write_text('<script src="start.js"></script>')
    client = TestClient(webapp.create_app(database, spa=folder), base_url=SITE)
    assert client.get("/app").headers["content-security-policy"] == (
        "default-src 'self'; frame-ancestors 'none'"
    )
