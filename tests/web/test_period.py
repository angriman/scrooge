"""The picker of a period beside the From and To fields (period.js): what the pages give it.
The fields stay text fields that are typed in and read by the server as before; how the
picker then behaves is checked by eye: nothing here runs a script."""

import re
from datetime import date
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze.storage import db
from finanze.web import app as webapp
from tests.storage.helpers import OWNER, txn
from tests.web.builders import BUILT, close

WEB = Path(webapp.__file__).parent
SITE = "http://127.0.0.1:8765"
ROWS = [
    txn("-45.20", date=date(2026, 7, 3), source_ref="1"),
    txn("-80", date=date(2026, 8, 17), source_ref="2"),
    txn("-12", date=date(2026, 8, 31), source_ref="3"),
]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    path = tmp_path_factory.mktemp("period") / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    close(conn, OWNER, (date(2026, 7, 1), date(2026, 8, 31)), ("migros", ROWS))
    conn.close()
    return TestClient(webapp.create_app(path), base_url=SITE)


def fields(page: str) -> list[str]:
    """The opening tags of the filter's From and To fields."""
    form = page.partition('<form method="get" class="filter">')[2].partition("</form>")[0]
    return re.findall(r'<input name="(?:from|to)"[^>]*>', form)


@pytest.mark.parametrize("path", ["/transactions", "/cashflow"])
def test_the_from_and_to_fields_are_marked_for_the_picker(client: TestClient, path: str) -> None:
    page = client.get(path, params={"from": "2026-07", "to": "2026"}).text
    first, last = fields(page)
    # Still text fields, with what was typed in them.
    assert first.startswith('<input name="from" value="2026-07" ')
    assert last.startswith('<input name="to" value="2026" ')
    assert 'data-period="from"' in first
    assert 'data-period="to"' in last
    assert "type=" not in first + last
    assert f'<script src="{SITE}/static/period.js" defer></script>' in page
    assert not re.search(r"\son[a-z]+=", page)  # no inline handler: the policy allows none
    assert 'style="' not in page


def test_only_the_pages_with_a_period_have_the_picker(client: TestClient) -> None:
    for path in ["/", "/holdings", "/months"]:
        assert "period.js" not in client.get(path).text


@pytest.mark.parametrize(
    ("asked", "days"),
    [
        ({"from": "2026-08-17", "to": "2026-08-17"}, ["2026-08-17"]),  # what the day grid writes
        ({"from": "2026-08", "to": "2026-08"}, ["2026-08-31", "2026-08-17"]),  # a month
        ({"from": "2026-07", "to": "2026-07"}, ["2026-07-03"]),
        ({"from": "2026", "to": "2026"}, ["2026-08-31", "2026-08-17", "2026-07-03"]),  # a year
        ({"from": "2026-07-04", "to": "2026-08"}, ["2026-08-31", "2026-08-17"]),  # mixed
        ({"from": "2025", "to": "2025"}, []),
    ],
)
def test_what_the_picker_writes_filters_as_typed(
    client: TestClient, asked: dict[str, str], days: list[str]
) -> None:
    page = client.get("/transactions", params=asked).text
    assert re.findall(r'<td class="date">(\d{4}-\d\d-\d\d)', page) == days
    assert '<p class="error"' not in page


def test_the_script_writes_the_forms_the_server_reads_and_loads_nothing() -> None:
    script = (WEB / "static" / "period.js").read_text(encoding="utf-8")
    assert script.startswith("// ")
    assert '"use strict";' in script
    assert "[data-period]" in script
    assert "Pick " in script  # the button's name, after its field's
    assert not re.search(r"fetch|XMLHttpRequest|eval\(|innerHTML|https?:", script)
    assert ".style" not in script  # the policy allows no inline style: classes only


def test_the_stylesheet_places_the_picker_on_a_phone_too() -> None:
    css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
    assert ".calendar {" in css
    assert ".calendar.flip {" in css
    phone = css.partition("@media (max-width: 40rem) {")[2]
    assert ".calendar {" in phone


def test_a_server_answers_the_script_with_a_script(client: TestClient) -> None:
    response = client.get("/static/period.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
