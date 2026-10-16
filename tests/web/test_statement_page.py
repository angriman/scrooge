import sqlite3
from datetime import date
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze.storage import db, originals
from finanze.web import app as webapp
from tests.storage.helpers import OWNER, txn
from tests.web.builders import (
    AUGUST,
    AUGUST_CLOSING,
    BUILT,
    LUKAS_AUGUST,
    SITE,
    close,
    committed_files,
    file_id,
    household,
)
from tests.web.test_app import text

CONTENT = b"Data;Importo\n03.08.2026;-45.20\n"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    """Lukas: August committed, September staged. Anna: September staged. One statement each."""
    path = tmp_path / "finanze.db"
    household(path)
    return path


@pytest.fixture
def client(database: Path) -> TestClient:
    return TestClient(webapp.create_app(database), base_url=SITE, follow_redirects=False)


def stored(database: Path, owner: str) -> tuple[int, str]:
    """The id and the digest of the first statement of `owner`."""
    conn = sqlite3.connect(database)
    found = conn.execute(
        "SELECT f.id, f.sha256 FROM statement_files f JOIN owners o ON o.id = f.owner_id "
        "WHERE o.name = ? ORDER BY f.id",
        (owner,),
    ).fetchone()
    conn.close()
    return int(found[0]), str(found[1])


def keep(database: Path, digest: str) -> None:
    folder = originals.folder_of(database)
    folder.mkdir()
    (folder / f"{digest}.csv").write_bytes(CONTENT)


def sent(client: TestClient) -> dict[str, str]:
    return {"token": client.app.state.token}  # type: ignore[attr-defined]  # the Starlette app


def test_a_statement_has_a_page_that_says_what_it_is(client: TestClient, database: Path) -> None:
    number, digest = stored(database, "Lukas")
    response = client.get(f"/statements/{number}")
    assert response.status_code == 200
    page = text(response.text)
    assert "migros.csv" in page
    assert "Lukas" in page
    assert "Migros Bank account statement" in page
    assert "Committed on 2026-10-08" in page
    assert "2026-08-01 to 2026-08-31" in page
    assert digest in page
    assert "Imported 2026-10-08" in page
    assert "3 transactions" in page
    assert f'href="{SITE}/months/Lukas/2026-08"' in response.text
    assert "2026-09" not in page


def test_a_committed_statement_can_be_taken_back_and_is_then_staged(
    client: TestClient, database: Path
) -> None:
    number, _ = stored(database, "Lukas")
    page = client.get(f"/statements/{number}")
    assert "<h2>Take it back</h2>" in page.text
    assert f'<form method="post" action="{SITE}/statements/{number}/uncommit">' in page.text
    assert "<button>Take back</button>" in page.text
    assert "Remove</button>" not in page.text
    response = client.post(f"/statements/{number}/uncommit", data=sent(client))
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/import?owner=Lukas"
    assert "Staged, waiting to be committed" in text(client.get(f"/statements/{number}").text)
    assert committed_files(database) == []
    assert "2026-08-01 to 2026-08-31" in text(client.get(response.headers["location"]).text)
    assert "<td>none</td>" not in client.get("/months").text  # the page still works


def test_a_staged_statement_can_be_removed(client: TestClient, database: Path) -> None:
    number = file_id(database, "Anna", committed=False)
    page = client.get(f"/statements/{number}")
    assert "Staged, waiting to be committed" in text(page.text)
    assert "<h2>Remove it</h2>" in page.text
    assert f'<form method="post" action="{SITE}/statements/{number}/discard">' in page.text
    assert "<button>Take back</button>" not in page.text
    response = client.post(f"/statements/{number}/discard", data=sent(client))
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/import?owner=Anna"
    assert client.get(f"/statements/{number}").status_code == 404
    assert committed_files(database) == ["Lukas migros.csv"]


def test_a_statement_between_committed_ones_cannot_be_taken_back_and_says_why(
    tmp_path: Path,
) -> None:
    path = tmp_path / "three.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    close(conn, OWNER, AUGUST, ("migros", LUKAS_AUGUST))
    september = [txn("-10", source_ref="7", date=date(2026, 9, 3))]
    close(
        conn,
        OWNER,
        (date(2026, 9, 1), date(2026, 9, 30)),
        ("migros", september),
        opening=AUGUST_CLOSING,
    )
    october = [txn("-20", source_ref="8", date=date(2026, 10, 2))]
    close(
        conn, OWNER, (date(2026, 10, 1), date(2026, 10, 5)), ("migros", october), opening="4944.80"
    )
    conn.close()
    client = TestClient(webapp.create_app(path), base_url=SITE, follow_redirects=False)
    conn = sqlite3.connect(path)
    middle = int(
        conn.execute("SELECT id FROM statement_files WHERE period_start = '2026-09-01'").fetchone()[
            0
        ]
    )
    conn.close()
    page = client.get(f"/statements/{middle}")
    assert "It cannot be taken back now" in text(page.text)
    assert "<button>Take back</button>" not in page.text
    refused = client.post(f"/statements/{middle}/uncommit", data=sent(client))
    assert refused.status_code == 409
    assert "Nothing was changed: " in text(refused.text)
    assert "between other committed statements" in text(refused.text)
    assert len(committed_files(path)) == 3


def test_what_cannot_be_done_to_a_statement_is_said_and_nothing_changes(
    client: TestClient, database: Path
) -> None:
    committed, _ = stored(database, "Lukas")
    staged = file_id(database, "Anna", committed=False)
    removed = client.post(f"/statements/{committed}/discard", data=sent(client))
    assert removed.status_code == 409
    assert "is committed: take it back first" in text(removed.text)
    back = client.post(f"/statements/{staged}/uncommit", data=sent(client))
    assert back.status_code == 409
    assert "is not committed" in text(back.text)
    assert committed_files(database) == ["Lukas migros.csv"]
    assert client.post("/statements/999/discard", data=sent(client)).status_code == 404
    assert client.post("/statements/999/uncommit", data=sent(client)).status_code == 404


@pytest.mark.parametrize("action", ["discard", "uncommit"])
def test_a_form_without_the_token_of_these_pages_changes_nothing(
    client: TestClient, database: Path, action: str
) -> None:
    number, _ = stored(database, "Lukas")
    url = f"/statements/{number}/{action}"
    assert client.post(url).status_code == 403
    assert client.post(url, data={"token": "guessed"}).status_code == 403
    elsewhere = client.post(url, data=sent(client), headers={"origin": "https://evil.example"})
    assert elsewhere.status_code == 403
    assert client.get(url).status_code == 405
    assert committed_files(database) == ["Lukas migros.csv"]


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
    number, _ = stored(database, "Lukas")
    page = client.get("/months/Lukas/2026-08").text
    assert f'<a href="{SITE}/statements/{number}">migros.csv</a>' in page


def test_the_draft_links_each_statement_and_row_to_its_page(
    client: TestClient, database: Path
) -> None:
    number = file_id(database, "Anna", committed=False)
    page = client.get("/import", params={"owner": "Anna"}).text
    assert f'<a href="{SITE}/statements/{number}">migros.csv</a>' in page
    assert page.count(f'<a href="{SITE}/statements/{number}">statement</a>') == 1


def test_a_transaction_links_to_the_statement_it_came_from(
    client: TestClient,
    database: Path,
) -> None:
    number, _ = stored(database, "Lukas")
    page = client.get("/transactions", params={"owner": "Lukas"}).text
    link = f'<a href="{SITE}/statements/{number}" title="The statement this row came from">'
    assert page.count(f"{link}file</a>") == 3  # August's rows
