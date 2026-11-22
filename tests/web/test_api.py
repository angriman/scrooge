"""The JSON of `/api`, and the frontend of its own at /app."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze import overview
from finanze.storage import db
from finanze.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER
from tests.test_networth import NOW
from tests.test_overview import household
from tests.web.test_login import STORED

SITE = "http://127.0.0.1:8765"


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("api") / "finanze.db"
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
    path = tmp_path / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    conn.close()
    client = TestClient(webapp.create_app(path), base_url=SITE)
    figures = client.get("/api/overview").json()["overview"]
    assert figures["day"] is None
    assert figures["top"] == []
    series = client.get("/api/networth").json()["series"]
    assert series == {"currency": "EUR", "first": None, "total": [], "parts": [], "notes": []}


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
