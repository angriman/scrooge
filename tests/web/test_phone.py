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
from tests.storage.helpers import CLOSED_AT, OWNER, close, txn

WEB = Path(webapp.__file__).parent
ROWS = [
    txn("6000", date=date(2026, 8, 25), source_ref="1", kind=Kind.INCOME, category="Stipendio"),
    txn("-45.20", date=date(2026, 8, 3), source_ref="2", category="Spesa", counterparty="Bakery"),
]
PAGES = ["/", "/networth", "/holdings", "/cashflow", "/transactions", "/months"]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    path = tmp_path_factory.mktemp("phone") / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, CLOSED_AT)
    august = date(2026, 8, 1)
    close(conn, OWNER, (august, date(2026, 8, 31)), august, ("migros", ROWS))
    conn.close()
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


def test_a_month_of_the_list_and_a_statement_say_what_each_figure_is(client: TestClient) -> None:
    assert cells(client.get("/months").text, "cards") == [
        '<td class="date lead">',
        '<td data-label="Person">',
        '<td class="total">',
        '<td class="number" data-label="Statements">',
        '<td class="number" data-label="Transactions">',
        '<td class="number" data-label="Need a category">',
    ]
    assert cells(client.get("/months/Lukas/2026-08").text, "cards") == [
        '<td class="lead">',
        '<td class="wide" data-label="File">',
        '<td class="date wide" data-label="Covers">',
        '<td data-label="Reconciled">',
        '<td class="number" data-label="Transactions">',
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
