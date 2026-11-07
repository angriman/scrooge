"""What the pages give the stylesheet to lay them out on a phone. How they then look is
checked by eye: nothing here draws a page."""

import re
from datetime import date
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze.models import Kind
from finanze.storage import db
from finanze.web import app as webapp
from tests.storage.helpers import OWNER, txn
from tests.web.builders import BUILT, close, household

WEB = Path(webapp.__file__).parent
ROWS = [
    txn("6000", date=date(2026, 8, 25), source_ref="1", kind=Kind.INCOME, category="Stipendio"),
    txn("-45.20", date=date(2026, 8, 3), source_ref="2", category="Spesa", counterparty="Bakery"),
]
PAGES = ["/", "/holdings", "/cashflow", "/analysis", "/transactions", "/months"]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    path = tmp_path_factory.mktemp("phone") / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    august = date(2026, 8, 1)
    close(conn, OWNER, (august, date(2026, 8, 31)), ("migros", ROWS))
    conn.close()
    return TestClient(webapp.create_app(path), base_url="http://127.0.0.1:8765")


@pytest.fixture
def draft(tmp_path: Path) -> TestClient:
    path = tmp_path / "finanze.db"
    household(path)
    return TestClient(webapp.create_app(path), base_url="http://127.0.0.1:8765")


def cells(html: str, table: str) -> list[str]:
    """The opening tags of the cells of the first body row of the table of that class."""
    body = html.partition(f'<table class="{table}">')[2].partition("<tbody>")[2]
    assert body, f"no table of class {table}"
    return re.findall(r"<td[^>]*>", body.partition("</tr>")[0])


@pytest.mark.parametrize("path", [*PAGES, "/months/Lukas/2026-08"])
def test_every_page_is_as_wide_as_the_screen_it_is_on(client: TestClient, path: str) -> None:
    page = client.get(path)
    assert page.status_code == 200
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page.text


def test_a_transaction_names_its_cells_for_the_card_it_becomes(client: TestClient) -> None:
    assert cells(client.get("/transactions").text, "entries") == [
        '<td class="date">',
        '<td class="person">',
        '<td class="account">',
        '<td class="category">',
        '<td class="what">',
        '<td class="number amount">',
    ]
    one = cells(client.get("/transactions", params={"owner": "Lukas"}).text, "entries")
    assert '<td class="person">' not in one


def test_a_period_of_the_cash_flow_says_what_each_figure_is(client: TestClient) -> None:
    assert cells(client.get("/cashflow").text, "cards") == [
        '<td class="date lead">',
        '<td class="number" data-label="Income">',
        '<td class="number" data-label="Expenses">',
        '<td class="bars">',
        '<td class="number total" data-label="Left">',
        '<td class="number" data-label="Investing">',
    ]


def test_the_import_page_is_as_wide_as_the_screen_it_is_on(draft: TestClient) -> None:
    page = draft.get("/import", params={"owner": "Lukas"})
    assert page.status_code == 200
    assert '<meta name="viewport" content="width=device-wi' in page.text


def test_an_account_and_a_month_of_the_list_say_what_each_figure_is(client: TestClient) -> None:
    assert cells(client.get("/months").text, "cards") == [
        '<td class="lead">',
        '<td data-label="Account">',
        '<td data-label="Source">',
        '<td class="date" data-label="Committed to">',
        '<td class="date" data-label="Staged to">',
    ]
    page = client.get("/months").text
    assert cells(page.partition("</table>")[2].partition("</table>")[2], "cards") == [
        '<td class="date lead">',
        '<td data-label="Person">',
        '<td class="number" data-label="Statements">',
        '<td class="number" data-label="Transactions">',
    ]
    assert cells(client.get("/months/Lukas/2026-08").text, "cards") == [
        '<td class="lead">',
        '<td class="wide" data-label="File">',
        '<td class="date wide" data-label="Covers">',
        '<td data-label="Reconciled">',
        '<td class="number" data-label="Transactions">',
    ]


def test_the_draft_names_its_cells_for_the_cards_they_become(draft: TestClient) -> None:
    page = draft.get("/import", params={"owner": "Lukas"}).text
    assert cells(page, "cards") == [  # the staged statements
        '<td class="lead">',
        '<td class="wide" data-label="File">',
        '<td class="date wide" data-label="Covers">',
        '<td class="number" data-label="Rows">',
        '<td class="wide">',
    ]
    totals = page.partition('<section id="totals">')[2]
    assert cells(totals, "cards") == [
        '<td class="lead">',
        '<td class="number total sum" data-unit="CHF">',
        '<td class="number count" data-label="Rows">',
    ]
    assert cells(page.partition('<section id="waiting">')[2], "cards") == [
        '<td class="lead wide">',
        '<td class="wide">',
        '<td class="wide">',
    ]
    assert cells(page.partition('<section id="rows">')[2], "entries") == [
        '<td class="date">',
        '<td class="account">',
        '<td class="what">',
        '<td class="number amount">',
        '<td class="category">',
    ]


def test_every_cell_of_a_card_has_a_place_or_a_name() -> None:
    """A cell without either would sit in the card with nothing to say what it is."""
    placed = re.compile(r'class="[^"]*\b(lead|total|wide|share|bars)\b|data-label="[^"]+"')
    for template in sorted((WEB / "templates").glob("*.html")):
        text = template.read_text(encoding="utf-8")
        for table in re.findall(r'<table class="cards">.*?</table>', text, re.DOTALL):
            body = table.partition("<tbody>")[2]
            for cell in re.findall(r"<td[^>]*>", body):
                assert placed.search(cell) or cell == "<td>", f"{template.name}: {cell}"


def test_the_stylesheet_and_the_chart_agree_on_what_a_phone_is() -> None:
    css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
    script = (WEB / "static" / "networth.js").read_text(encoding="utf-8")
    assert css.count("@media (max-width: 40rem)") == 1
    assert 'matchMedia("(max-width: 40rem)")' in script
