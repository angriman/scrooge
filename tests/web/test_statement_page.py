import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze import monthclose
from finanze.models import CashBalance
from finanze.storage import db, originals
from finanze.web import app as webapp
from tests.storage.helpers import AUGUST, CLOSED_AT, OWNER, PARTNER, batch, close, statement
from tests.web.test_app import text
from tests.web.test_month_page import ANNA, LUKAS, SITE

CONTENT = b"Data;Importo\n03.08.2026;-45.20\n"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    """Lukas: August committed, September open. Anna: September open. One statement each."""
    path = tmp_path / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, CLOSED_AT)
    close(conn, OWNER, (AUGUST, date(2026, 9, 30)), AUGUST, ("migros", LUKAS))
    hers = statement("migros", ANNA, [CashBalance("Migros", "CHF", Decimal(0), Decimal(-60))])
    monthclose.stage(conn, batch(hers, owner=PARTNER), CLOSED_AT)
    conn.close()
    return path


@pytest.fixture
def client(database: Path) -> TestClient:
    return TestClient(webapp.create_app(database), base_url=SITE, follow_redirects=False)


def stored(database: Path, owner: str) -> tuple[int, str]:
    """The id and the digest of the one statement of `owner`."""
    conn = sqlite3.connect(database)
    found = conn.execute(
        "SELECT f.id, f.sha256 FROM statement_files f JOIN owners o ON o.id = f.owner_id "
        "WHERE o.name = ?",
        (owner,),
    ).fetchone()
    conn.close()
    return int(found[0]), str(found[1])


def keep(database: Path, digest: str) -> None:
    folder = originals.folder_of(database)
    folder.mkdir()
    (folder / f"{digest}.csv").write_bytes(CONTENT)


def test_a_statement_has_a_page_that_says_what_it_is(client: TestClient, database: Path) -> None:
    number, digest = stored(database, "Lukas")
    response = client.get(f"/statements/{number}")
    assert response.status_code == 200
    page = text(response.text)
    assert "migros.csv" in page
    assert "Lukas" in page
    assert "2026-08-01 to 2026-09-30" in page
    assert digest in page
    assert "Imported 2026-10-08" in page
    assert "4 transactions" in page
    assert f'href="{SITE}/months/Lukas/2026-08"' in response.text
    assert f'href="{SITE}/months/Lukas/2026-09"' in response.text


def test_the_page_offers_the_original_only_when_it_is_kept(
    client: TestClient,
    database: Path,
) -> None:
    number, digest = stored(database, "Lukas")
    address = f"/statements/{number}/original"
    before = client.get(f"/statements/{number}")
    assert address not in before.text
    assert "The file itself is not kept here" in text(before.text)
    assert client.get(address).status_code == 404
    keep(database, digest)
    assert f'href="{SITE}{address}"' in client.get(f"/statements/{number}").text
    original = client.get(address)
    assert original.status_code == 200
    assert original.content == CONTENT
    assert original.headers["content-disposition"] == 'attachment; filename="migros.csv"'


def test_a_statement_nobody_stored_is_not_found(client: TestClient) -> None:
    assert client.get("/statements/999").status_code == 404
    assert client.get("/statements/999/original").status_code == 404
    assert client.get("/statements/abc").status_code == 404


def test_a_month_links_each_statement_to_its_page(client: TestClient, database: Path) -> None:
    number, _ = stored(database, "Anna")
    page = client.get("/months/Anna/2026-09").text
    assert f'<a href="{SITE}/statements/{number}">migros.csv</a>' in page


def test_a_transaction_links_to_the_statement_it_came_from(
    client: TestClient,
    database: Path,
) -> None:
    number, _ = stored(database, "Lukas")
    page = client.get("/transactions", params={"owner": "Lukas"}).text
    link = f'<a href="{SITE}/statements/{number}" title="The statement this row came from">'
    assert page.count(f"{link}file</a>") == 2  # August's rows
