"""The first page says which accounts are behind: a statement of theirs is probably missing."""

import html
import re
from datetime import date
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge.storage import db, store
from scrooge.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER, txn
from tests.web.builders import ANNA_SEPTEMBER, AUGUST_DAYS, BUILT, LUKAS_AUGUST, SEPTEMBER_DAYS
from tests.web.builders import close as committed
from tests.web.test_app import text

SITE = "http://127.0.0.1:8765"
NUDGE = re.compile(r'<p class="nudge" role="status">(.*?)</p>', re.DOTALL)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    """Lukas: his bank committed for August, his Revolut for June only; Anna: her bank
    committed for September."""
    found = tmp_path / "f.db"
    conn = db.connect(found)
    db.migrate(conn, BUILT)
    committed(conn, OWNER, AUGUST_DAYS, ("migros", LUKAS_AUGUST))
    june = (date(2026, 6, 1), date(2026, 6, 30))
    row = txn("-9", source_ref="9", account="Revolut CHF", date=june[0])
    committed(conn, OWNER, june, ("revolut", [row]))
    committed(conn, PARTNER, SEPTEMBER_DAYS, ("migros", ANNA_SEPTEMBER))
    conn.close()
    return found


def nudges(client: TestClient, **params: str) -> list[tuple[str, dict[str, str]]]:
    """What each nudge of the first page says, and where its links lead."""
    page = client.get("/", params={"currency": "CHF", **params})
    assert page.status_code == 200
    return [
        (
            html.unescape(text(said)),
            {
                text(name): html.unescape(href).replace(SITE, "")
                for href, name in re.findall(r'<a href="([^"]*)">(.*?)</a>', said)
            },
        )
        for said in NUDGE.findall(page.text)
    ]


def test_an_account_behind_is_named_with_where_to_import_it(path: Path) -> None:
    client = TestClient(webapp.create_app(path), base_url=SITE)
    said = (
        "Lukas's Revolut CHF statements end on 2026-06-30, over a month before the others: "
        "a statement is probably missing. Import"
    )
    assert nudges(client) == [(said, {"Import": "/import?owner=Lukas"})]
    # One person's view goes without their name; the other's has nothing behind.
    assert [said for said, _ in nudges(client, owner=OWNER)] == [
        (
            "Revolut CHF statements end on 2026-06-30, over a month before the others: "
            "a statement is probably missing. Import"
        )
    ]
    assert nudges(client, owner=PARTNER) == []


def test_a_copy_leads_to_the_months_page_where_nothing_is_imported(path: Path) -> None:
    client = TestClient(webapp.create_app(path, read_only=True), base_url=SITE)
    [(said, links)] = nudges(client)
    assert said.endswith("a statement is probably missing. See the months")
    assert links == {"See the months": "/months?owner=Lukas"}


def test_a_closed_account_is_never_behind(path: Path) -> None:
    conn = db.connect(path)
    revolut = store.account_of(conn, store.owner_id(conn, OWNER) or 0, "Revolut CHF")
    assert revolut is not None
    store.set_closed(conn, revolut.id, date(2026, 6, 30))
    conn.close()
    assert nudges(TestClient(webapp.create_app(path), base_url=SITE)) == []
