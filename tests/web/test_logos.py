"""The marks beside a holding: the user's own logos, the icon of bills, or its letters."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze.storage import db
from finanze.web import app as webapp
from tests.test_networth import NOW
from tests.test_overview import household

SITE = "http://127.0.0.1:8765"
LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1"/></svg>'
)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """The household of the overview's tests, a logo for its fund and a file that is none."""
    conn = db.connect(tmp_path / "finanze.db")
    db.migrate(conn, NOW)
    household(conn)
    conn.close()
    logos = tmp_path / "logos"
    logos.mkdir()
    (logos / "WIDG.svg").write_text(LOGO)
    (logos / "notes.txt").write_text("not a logo")
    (logos / "OLD.png").mkdir()  # a folder, whatever its name says
    return tmp_path


def test_a_logo_is_served_from_the_folder_beside_the_database_and_runs_nothing(
    folder: Path,
) -> None:
    client = TestClient(webapp.create_app(folder / "finanze.db"), base_url=SITE)
    response = client.get("/logos/WIDG.svg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert response.text == LOGO
    policies = response.headers.get_list("content-security-policy")
    assert "default-src 'none'; style-src 'unsafe-inline'; sandbox" in policies
    for name in ("notes.txt", "OLD.png", "GADG.svg", "..%2Ffinanze.db"):
        assert client.get(f"/logos/{name}").status_code == 404


def test_each_holding_wears_its_logo_the_icon_of_bills_or_its_letters(folder: Path) -> None:
    client = TestClient(webapp.create_app(folder / "finanze.db"), base_url=SITE)
    page = client.get("/").text.replace(SITE, "")
    assert '<img class="mark" src="/logos/WIDG.svg" alt="" width="24" height="24">' in page
    assert '<img class="mark" src="/static/icons/bill.svg" alt="" width="24" height="24">' in page
    assert '<span class="mark letters slot-6" aria-hidden="true">GA</span>' in page


def test_without_a_folder_of_logos_every_holding_wears_its_letters(folder: Path) -> None:
    for each in (folder / "logos").iterdir():
        if each.is_dir():
            each.rmdir()
        else:
            each.unlink()
    (folder / "logos").rmdir()
    client = TestClient(webapp.create_app(folder / "finanze.db"), base_url=SITE)
    page = client.get("/").text
    assert "/logos/" not in page
    assert '<span class="mark letters slot-5" aria-hidden="true">WI</span>' in page
    assert client.get("/logos/WIDG.svg").status_code == 404
