"""The chart of the first page, the figures it reads, and the brokers' own figures beside it."""

import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge.models import PrintedValue
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER
from tests.test_networth import NOW, account, bill, bought, close, ibkr, rate, spent
from tests.web.test_analysis_cashflow import tables
from tests.web.test_app import text

FIRST, LAST = date(2026, 8, 31), date(2026, 9, 30)  # the opening balance, and 30 days of rows
DAYS = 31
NONE = "\u2013"  # what the table shows where there is no day that far back


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """September 2026, committed for both: francs only, and a euro worth 0.80 francs.

    Lukas has 1000 francs and spends 200 on the 10th; Anna has 500 throughout.
    """
    path: Path = tmp_path_factory.mktemp("networth") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    his = account("migros", "Migros", "CHF", "1000", spent("-200", date(2026, 9, 10)))
    printed = (
        PrintedValue("Cash", "CHF", Decimal(790)),
        PrintedValue("Options", "CHF", Decimal(-50)),
    )
    close(conn, replace(his, extraction=replace(his.extraction, values=printed)))
    close(conn, account("migros", "Migros", "CHF", "500", filename="hers.csv"), owner=PARTNER)
    rate(conn, "EUR", FIRST, "0.8")
    conn.close()
    return TestClient(webapp.create_app(path), base_url="http://127.0.0.1:8765")


@pytest.fixture
def empty(tmp_path: Path) -> TestClient:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    conn.close()
    return TestClient(webapp.create_app(path), base_url="http://127.0.0.1:8765")


@pytest.fixture
def mixed(tmp_path: Path) -> TestClient:
    """One broker account in dollars holding cash, an ETF and a bill."""
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    fifteenth = date(2026, 9, 15)
    close(conn, ibkr(bought("WIDG", "10", "-500", fifteenth), bill("1000", "-990", fifteenth)))
    conn.close()
    return TestClient(webapp.create_app(path), base_url="http://127.0.0.1:8765")


def legend(html: str) -> list[tuple[str, str]]:
    """The colour slot and the name of every line the legend lists."""
    listed = html.partition('<ul class="legend"')[2].partition("</ul>")[0]
    return re.findall(r'<span class="key slot-(\d)"></span>([^<]+)</button>', listed)


def source(html: str) -> str:
    """Where the chart reads its figures from."""
    found: list[str] = re.findall(r'data-source="([^"]*)"', html)
    assert len(found) == 1
    return found[0].replace("&amp;", "&")


def test_net_worth_is_in_euros_and_by_asset_type_unless_asked(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    # (800 + 500) / 0.8 on the last day, (1000 + 500) / 0.8 thirty days before.
    assert "Net worth on 2026-09-30 1,625.00 EUR" in text(response.text)
    assert "In 30 days -250.00 -13.33% From 1,875.00" in text(response.text)
    assert legend(response.text) == [("0", "Net worth"), ("1", "Cash")]
    assert source(response.text) == "http://127.0.0.1:8765/networth.json?currency=EUR&by=asset"
    assert 'by=asset" aria-current="true">By asset type</a>' in response.text
    assert "Options and futures are not counted as assets" in text(response.text)


def test_the_total_alone_is_one_line_without_a_legend(client: TestClient) -> None:
    response = client.get("/", params={"by": "total"})
    assert legend(response.text) == []
    assert source(response.text).endswith("?currency=EUR&by=total")
    assert 'by=total" aria-current="true">Total</a>' in response.text


def test_the_old_address_of_the_chart_leads_to_the_first_page(client: TestClient) -> None:
    asked = {"currency": "CHF", "by": "owner", "owner": OWNER}
    response = client.get("/networth", params=asked, follow_redirects=False)
    assert response.status_code == 307
    assert (
        response.headers["location"]
        == f"http://127.0.0.1:8765/?currency=CHF&by=owner&owner={OWNER}"
    )
    assert legend(client.get("/networth", params=asked).text)[1][1] == OWNER


def test_the_parts_follow_the_total_each_with_its_colour(client: TestClient) -> None:
    response = client.get("/", params={"currency": "chf", "by": "owner"})
    by_owner = legend(response.text)
    assert [name for _, name in by_owner] == ["Net worth", PARTNER, OWNER]
    assert by_owner[0][0] == "0"
    assert {slot for slot, _ in by_owner[1:]} == {"1", "2"}
    assert source(response.text).endswith("?currency=CHF&by=owner")  # the same view
    # Each person with what they had 30 days before, whatever the chart is cut by.
    assert tables(response.text)[2] == [
        [PARTNER, "500.00", "38.46%", "500.00", NONE],
        [OWNER, "800.00", "61.54%", "1,000.00", NONE],
    ]
    assert 'by=owner" aria-current="true">By person</a>' in response.text

    response = client.get("/", params={"currency": "CHF", "by": "asset"})
    assert tables(response.text)[0] == [["Cash", "1,300.00", "100.00%", "1,500.00", NONE]]
    assert legend(response.text) == [("0", "Net worth"), ("1", "Cash")]


def test_a_person_keeps_their_colour_when_looked_at_alone(client: TestClient) -> None:
    both = {name: slot for slot, name in legend(client.get("/?by=owner").text)}
    for person, worth in ((OWNER, "1,000.00"), (PARTNER, "625.00")):
        response = client.get("/", params={"owner": person, "by": "owner"})
        assert f"Net worth on 2026-09-30 {worth} EUR" in text(response.text)
        assert legend(response.text) == [("0", "Net worth"), (both[person], person)]
        assert source(response.text).endswith(f"?currency=EUR&by=owner&owner={person}")


def test_what_a_statement_says_it_was_worth_is_set_beside_it(client: TestClient) -> None:
    response = client.get("/", params={"currency": "CHF"})
    assert tables(response.text)[-1] == [
        ["2026-09-30", OWNER, "Migros", "Options", "-50.00", "not counted", "", "CHF"],
        ["2026-09-30", OWNER, "Migros", "Cash", "790.00", "800.00", "10.00", "CHF"],
    ]
    hers = client.get("/", params={"owner": PARTNER})
    assert len(tables(hers.text)) == 2  # the kinds with their total
    assert "Against the brokers" not in hers.text


def test_the_chart_reads_every_day_as_text_to_the_cent(client: TestClient) -> None:
    response = client.get("/networth.json", params={"by": "asset"})
    assert response.status_code == 200
    answer = response.json()
    assert (answer["currency"], answer["first"]) == ("EUR", FIRST.isoformat())
    total, cash = answer["series"]
    assert (total["name"], total["slot"]) == ("Net worth", 0)
    assert (cash["name"], cash["slot"]) == ("Cash", 1)
    # 1500 francs until the 9th, 1300 from the 10th, at 0.80 francs a euro.
    assert total["values"] == cash["values"] == ["1875.00"] * 10 + ["1625.00"] * 21
    assert len(total["values"]) == DAYS == (LAST - FIRST).days + 1
    # by asset type unless asked otherwise, as on the page
    assert client.get("/networth.json").json() == answer
    alone = client.get("/networth.json", params={"by": "total"}).json()["series"]
    assert [line["name"] for line in alone] == ["Net worth"]


def test_the_parts_are_listed_and_stacked_from_the_steadiest_up(mixed: TestClient) -> None:
    asked = {"by": "asset", "currency": "USD"}
    page = mixed.get("/", params=asked).text
    # Bills below the ETF, whatever the order the kinds of asset are worked out in; each in
    # its colour, and neighbours in the stack in neighbouring ones.
    assert legend(page) == [("0", "Net worth"), ("1", "Cash"), ("2", "Bills"), ("5", "ETFs")]
    assert [row[0] for row in tables(page)[0]] == ["Cash", "Bills", "ETFs"]
    # The script finds the button of a series by its slot.
    assert re.findall(r'<button type="button" data-slot="(\d)" aria-pressed="true">', page) == [
        slot for slot, _ in legend(page)
    ]
    # The chart stacks its parts in the order it is given them, and they add up to the total.
    series = mixed.get("/networth.json", params=asked).json()["series"]
    assert [(str(line["slot"]), line["name"]) for line in series] == legend(page)
    total, *parts = ([Decimal(value) for value in line["values"]] for line in series)
    assert total == [sum(day) for day in zip(*parts, strict=True)]


def test_a_view_that_cannot_be_read_is_reported_and_the_default_shown(client: TestClient) -> None:
    asked = {"currency": "XAU", "by": "shape"}
    response = client.get("/", params=asked)
    assert response.status_code == 200
    errors = [
        "'XAU' is not one of EUR, CHF, USD, GBP, JPY.",
        "'shape' is not total, asset or owner.",
    ]
    assert re.findall(r'<p class="error" role="alert">(.*?)</p>', response.text) == [
        error.replace("'", "&#39;") for error in errors
    ]
    assert "Net worth on 2026-09-30 1,625.00 EUR" in text(response.text)
    # The chart draws what the page shows, not what was asked for.
    assert client.get(source(response.text)).json()["currency"] == "EUR"
    answer = client.get("/networth.json", params=asked)
    assert answer.status_code == 400
    assert answer.json() == {"errors": errors}


def test_a_currency_without_a_rate_is_said_and_nothing_is_drawn(client: TestClient) -> None:
    response = client.get("/", params={"currency": "USD"})
    assert response.status_code == 200
    [error] = re.findall(r'<p class="error" role="alert">(.*?)</p>', response.text)
    assert error.startswith("No exchange rate is stored for USD")
    assert error.endswith(".")
    assert 'id="networth"' not in response.text
    assert "Nothing is committed yet" not in response.text
    assert "<option selected>USD</option>" in response.text
    answer = client.get("/networth.json", params={"currency": "USD"})
    assert answer.status_code == 400
    assert answer.json() == {"errors": [error]}


def test_a_statement_valued_in_a_currency_without_a_rate_is_said_too(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    his = account("migros", "Migros", "CHF", "1000")
    printed = (PrintedValue("Cash", "USD", Decimal(1250)),)
    close(conn, replace(his, extraction=replace(his.extraction, values=printed)))
    conn.close()
    client = TestClient(webapp.create_app(path), base_url="http://127.0.0.1:8765")
    response = client.get("/", params={"currency": "CHF"})
    assert response.status_code == 200
    assert "No exchange rate is stored for USD" in response.text
    # The figures themselves need no rate, and neither does the chart: only the brokers'
    # own figures are left out.
    assert "Net worth on 2026-09-30 1,000.00 CHF" in text(response.text)
    assert 'id="networth"' in response.text
    assert "Against the brokers" not in response.text
    assert client.get("/networth.json", params={"currency": "CHF"}).status_code == 200


def test_nothing_committed_is_said_and_is_an_empty_chart(empty: TestClient) -> None:
    response = empty.get("/")
    assert response.status_code == 200
    assert "Nothing is committed yet." in response.text
    assert 'id="networth"' not in response.text
    assert empty.get("/networth.json").json() == {"currency": "EUR", "first": None, "series": []}


def test_the_page_brings_its_own_chart_and_keeps_its_own_colours(client: TestClient) -> None:
    page = client.get("/").text
    # Dark Reader would paint over the chart's canvas: the stylesheet has a dark mode.
    assert '<meta name="darkreader-lock">' in page
    for asset in ("vendor/uPlot.iife.min.js", "vendor/uPlot.min.css", "networth.js", "donut.js"):
        address = f"http://127.0.0.1:8765/static/{asset}"
        assert address in page
        assert client.get(address).status_code == 200
    # the chart's scripts are this page's: the holdings page has the header's and its switch
    assert re.findall(r"<script[^>]*>", client.get("/holdings").text) == [
        '<script src="http://127.0.0.1:8765/static/choose.js" defer>',
        '<script src="http://127.0.0.1:8765/static/views.js" defer>',
    ]
