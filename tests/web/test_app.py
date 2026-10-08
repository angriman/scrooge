import re
from datetime import date
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze.models import Kind
from finanze.storage import db
from finanze.web import app as webapp
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close, txn

WEB = Path(webapp.__file__).parent
LUKAS = [
    txn("-45.20", date=date(2026, 7, 3), source_ref="1"),
    txn("6000", date=date(2026, 8, 25), source_ref="2", kind=Kind.INCOME, category="Stipendio"),
    txn("-12", date=date(2026, 9, 10), source_ref="3"),
]
ANNA = [txn("-60", date=date(2026, 7, 12), source_ref="1")]


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Lukas with July and August committed and September open; Anna with July committed."""
    path = tmp_path_factory.mktemp("web") / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, CLOSED_AT)
    july = date(2026, 7, 1)
    close(conn, OWNER, (july, date(2026, 9, 30)), date(2026, 8, 1), ("migros", LUKAS))
    close(conn, PARTNER, (july, date(2026, 7, 31)), july, ("migros", ANNA))
    conn.close()
    return path


@pytest.fixture
def client(database: Path) -> TestClient:
    return TestClient(webapp.create_app(database), base_url="http://127.0.0.1:8765")


def rows(html: str) -> list[list[str]]:
    """The cells of every table row in the page's body."""
    body = html.partition("<tbody>")[2]
    found = []
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        found.append([re.sub(r"\s+", " ", cell).strip() for cell in cells])
    return found


def test_the_overview_says_what_each_person_has_committed(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert rows(response.text) == [
        ["Anna", "2026-07 to 2026-07 (1)", "1", "0"],
        ["Lukas", "2026-07 to 2026-08 (2)", "2", "1"],
    ]
    assert '<a href="http://127.0.0.1:8765/" aria-current="true">Everyone</a>' in response.text


def test_the_view_can_be_one_person(client: TestClient) -> None:
    response = client.get("/", params={"owner": "Anna"})
    assert [row[0] for row in rows(response.text)] == ["Anna"]
    page = response.text.replace("http://127.0.0.1:8765", "")
    assert '<a href="/?owner=Anna" aria-current="true">Anna</a>' in page
    assert '<a href="/">Everyone</a>' in page
    assert '<a href="/?owner=Anna">Overview</a>' in page


def test_a_person_the_database_does_not_have_is_not_found(client: TestClient) -> None:
    response = client.get("/", params={"owner": "<b>Zoe"})
    assert response.status_code == 404
    assert response.text == "no owner named '<b>Zoe'\n"
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "192.168.1.20"])
def test_a_request_under_another_host_name_is_refused(client: TestClient, host: str) -> None:
    assert client.get("/", headers={"host": host}).status_code == 400


def test_localhost_is_this_machine_too(client: TestClient) -> None:
    assert client.get("/", headers={"host": "localhost:8765"}).status_code == 200


@pytest.mark.parametrize("path", ["/", "/static/app.css", "/nothing-here"])
def test_every_response_forbids_loading_from_elsewhere(client: TestClient, path: str) -> None:
    headers = client.get(path).headers
    assert headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"
    assert headers["x-content-type-options"] == "nosniff"


def test_the_stylesheet_is_served_from_the_package(client: TestClient) -> None:
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_no_page_or_asset_names_another_site() -> None:
    files = [*WEB.glob("templates/*"), *WEB.glob("static/*")]
    assert files
    for file in files:
        assert not re.search(r"https?:|//\w", file.read_text(encoding="utf-8")), file.name
