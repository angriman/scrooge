"""The currency every page shows its figures in: chosen once in the header, for all of them."""

import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze.storage import db
from finanze.web import app as webapp
from tests.storage.helpers import OWNER
from tests.test_networth import NOW
from tests.test_overview import household
from tests.web.builders import Site
from tests.web.test_app import text
from tests.web.test_holdings_page import errors

SITE = "http://127.0.0.1:8765"
PAGES = ["/", "/networth", "/holdings", "/cashflow", "/transactions", "/months"]
WEB = Path(webapp.__file__).parent


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("currency") / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    household(conn)
    conn.close()
    return path


@pytest.fixture
def client(database: Path) -> TestClient:
    """A browser of its own: nothing chosen yet."""
    return TestClient(webapp.create_app(database), base_url=SITE)


def picker(page: str) -> str:
    """The form in the header that chooses the currency."""
    [form] = re.findall(r'<form method="get" action="[^"]*/currency".*?</form>', page, re.DOTALL)
    return str(form)


def chosen(page: str) -> str:
    [currency] = re.findall(r"<option selected>(\w+)</option>", picker(page))
    return str(currency)


@pytest.mark.parametrize("path", PAGES)
def test_every_page_has_the_picker_in_its_header(client: TestClient, path: str) -> None:
    page = client.get(path, params={"owner": OWNER}).text
    header = page.partition("<main>")[0]
    form = picker(header)
    assert "data-auto" in form
    assert f'<input type="hidden" name="back" value="{path}?owner={OWNER}">' in form
    assert re.findall(r"<option(?: selected)?>(\w+)</option>", form) == [
        "EUR",
        "CHF",
        "USD",
        "GBP",
        "JPY",
    ]
    assert chosen(page) == "EUR"  # nothing chosen yet
    assert "<button>Show</button>" in form  # for a browser without the script
    assert f'<script src="{SITE}/static/choose.js" defer></script>' in header
    assert '<select name="currency">' not in page.partition("<main>")[2]  # the header's alone


def test_the_script_sends_the_form_when_a_menu_changes(client: TestClient) -> None:
    script = (WEB / "static" / "choose.js").read_text()
    assert "form[data-auto]" in script
    assert "requestSubmit" in script
    assert client.get("/static/choose.js").status_code == 200


def test_a_currency_chosen_is_remembered_and_the_page_shown_again_in_it(
    client: TestClient,
) -> None:
    back = f"/holdings?owner={OWNER}&currency=EUR"  # the address named the old one
    response = client.get(
        "/currency", params={"currency": "chf", "back": back}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/holdings?owner={OWNER}"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("finanze_currency=CHF;")
    assert "HttpOnly" in cookie
    assert "Path=/" in cookie
    for path in PAGES:
        assert chosen(client.get(path).text) == "CHF"
    assert "net worth on 2026-09-30" in text(client.get("/").text)
    assert " CHF net worth on" in text(client.get("/").text)
    assert " CHF</strong> in securities" in client.get("/holdings").text
    networth = client.get("/networth").text
    assert "/networth.json?currency=CHF&amp;by=total" in networth
    assert '<input type="hidden" name="currency" value="CHF">' in networth  # the breakdown's form


def test_an_address_that_names_a_currency_is_shown_in_it_and_the_choice_stays(
    client: TestClient,
) -> None:
    client.get("/currency", params={"currency": "CHF", "back": "/"})
    page = client.get("/", params={"currency": "EUR"}).text
    assert chosen(page) == "EUR"
    assert " EUR net worth on" in text(page)
    assert chosen(client.get("/").text) == "CHF"


def test_a_currency_that_is_not_offered_is_refused_and_nothing_is_remembered(
    client: TestClient,
) -> None:
    response = client.get(
        "/currency", params={"currency": "XAU", "back": "/"}, follow_redirects=False
    )
    assert response.status_code == 400
    assert "set-cookie" not in response.headers
    assert response.text == "'XAU' is not one of EUR, CHF, USD, GBP, JPY.\n"
    assert chosen(client.get("/").text) == "EUR"


def test_a_remembered_currency_that_is_no_longer_offered_is_left_aside(
    client: TestClient,
) -> None:
    client.cookies.set("finanze_currency", "XAU")
    page = client.get("/").text
    assert chosen(page) == "EUR"
    assert errors(page) == []


@pytest.mark.parametrize("back", ["", "//elsewhere.example/", "https://elsewhere.example/", "/\\x"])
def test_the_way_back_never_leads_to_another_site(client: TestClient, back: str) -> None:
    response = client.get(
        "/currency", params={"currency": "USD", "back": back}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_a_page_that_answers_a_form_sent_has_no_picker(site: Site) -> None:
    # It is no address to go back to: the page is gone once the browser leaves it.
    sent = site.send(site.uploads("Migros.csv"), owner="Zoe")
    assert sent.status_code == 400
    assert "/currency" not in sent.text
    assert "/currency" in site.client.get("/import").text
