"""The net-worth page and the figures its chart reads."""

import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze.models import PrintedValue
from finanze.storage import db
from finanze.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER
from tests.test_networth import NOW, account, close, rate, spent
from tests.web.test_app import text
from tests.web.test_cashflow_page import tables

FIRST, LAST = date(2026, 8, 31), date(2026, 9, 30)  # the opening balance, and 30 days of rows
DAYS = 31
NONE = "\u2013"  # what the table shows where there is no day that far back


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """September 2026, committed for both: francs only, and a euro worth 0.80 francs.

    Lukas has 1000 francs and spends 200 on the 10th; Anna has 500 throughout.
    """
    path: Path = tmp_path_factory.mktemp("networth") / "finanze.db"
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
    path = tmp_path / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
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


def test_net_worth_is_in_euros_and_one_line_unless_asked(client: TestClient) -> None:
    response = client.get("/networth")
    assert response.status_code == 200
    assert "1,625.00 EUR on 2026-09-30" in text(response.text)
    # (800 + 500) / 0.8 on the last day, (1000 + 500) / 0.8 thirty days before.
    assert tables(response.text)[0] == [["Net worth", "1,625.00", "1,875.00", NONE]]
    assert legend(response.text) == []
    assert source(response.text) == "http://127.0.0.1:8765/networth.json?currency=EUR&by=total"
    assert "Options and futures are not counted as assets" in text(response.text)


def test_the_parts_follow_the_total_each_with_its_colour(client: TestClient) -> None:
    response = client.get("/networth", params={"currency": "chf", "by": "owner"})
    assert tables(response.text)[0] == [
        ["Net worth", "1,300.00", "1,500.00", NONE],
        [PARTNER, "500.00", "500.00", NONE],
        [OWNER, "800.00", "1,000.00", NONE],
    ]
    by_owner = legend(response.text)
    assert [name for _, name in by_owner] == ["Net worth", PARTNER, OWNER]
    assert by_owner[0][0] == "0"
    assert {slot for slot, _ in by_owner[1:]} == {"1", "2"}
    assert source(response.text).endswith("?currency=CHF&by=owner")  # the same view

    response = client.get("/networth", params={"currency": "CHF", "by": "asset"})
    assert tables(response.text)[0] == [
        ["Net worth", "1,300.00", "1,500.00", NONE],
        ["Cash", "1,300.00", "1,500.00", NONE],
    ]
    assert legend(response.text) == [("0", "Net worth"), ("1", "Cash")]


def test_a_person_keeps_their_colour_when_looked_at_alone(client: TestClient) -> None:
    both = {name: slot for slot, name in legend(client.get("/networth?by=owner").text)}
    for person, worth in ((OWNER, "1,000.00"), (PARTNER, "625.00")):
        response = client.get("/networth", params={"owner": person, "by": "owner"})
        assert tables(response.text)[0][0][:2] == ["Net worth", worth]
        assert legend(response.text) == [("0", "Net worth"), (both[person], person)]
        assert f'<input type="hidden" name="owner" value="{person}">' in response.text
        assert source(response.text).endswith(f"?currency=EUR&by=owner&owner={person}")


def test_what_a_statement_says_it_was_worth_is_set_beside_it(client: TestClient) -> None:
    response = client.get("/networth", params={"currency": "CHF"})
    assert tables(response.text)[1] == [
        ["2026-09-30", OWNER, "Migros", "Options", "-50.00", "not counted", "", "CHF"],
        ["2026-09-30", OWNER, "Migros", "Cash", "790.00", "800.00", "10.00", "CHF"],
    ]
    hers = client.get("/networth", params={"owner": PARTNER})
    assert len(tables(hers.text)) == 1
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
    assert [line["name"] for line in client.get("/networth.json").json()["series"]] == ["Net worth"]


def test_a_view_that_cannot_be_read_is_reported_and_the_default_shown(client: TestClient) -> None:
    asked = {"currency": "XAU", "by": "shape"}
    response = client.get("/networth", params=asked)
    assert response.status_code == 200
    errors = [
        "'XAU' is not one of EUR, CHF, USD, GBP, JPY.",
        "'shape' is not total, asset or owner.",
    ]
    assert re.findall(r'<p class="error" role="alert">(.*?)</p>', response.text) == [
        error.replace("'", "&#39;") for error in errors
    ]
    assert tables(response.text)[0] == [["Net worth", "1,625.00", "1,875.00", NONE]]
    # The chart draws what the page shows, not what was asked for.
    assert client.get(source(response.text)).json()["currency"] == "EUR"
    answer = client.get("/networth.json", params=asked)
    assert answer.status_code == 400
    assert answer.json() == {"errors": errors}


def test_a_currency_without_a_rate_is_said_and_nothing_is_drawn(client: TestClient) -> None:
    response = client.get("/networth", params={"currency": "USD"})
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
    path = tmp_path / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    his = account("migros", "Migros", "CHF", "1000")
    printed = (PrintedValue("Cash", "USD", Decimal(1250)),)
    close(conn, replace(his, extraction=replace(his.extraction, values=printed)))
    conn.close()
    client = TestClient(webapp.create_app(path), base_url="http://127.0.0.1:8765")
    response = client.get("/networth", params={"currency": "CHF"})
    assert response.status_code == 200
    assert "No exchange rate is stored for USD" in response.text
    # The figures themselves need no rate, and the chart does not read the statements.
    assert client.get("/networth.json", params={"currency": "CHF"}).status_code == 200


def test_nothing_committed_is_said_and_is_an_empty_chart(empty: TestClient) -> None:
    response = empty.get("/networth")
    assert response.status_code == 200
    assert "Nothing is committed yet." in response.text
    assert 'id="networth"' not in response.text
    assert empty.get("/networth.json").json() == {"currency": "EUR", "first": None, "series": []}


def test_the_page_brings_its_own_chart_and_keeps_its_own_colours(client: TestClient) -> None:
    page = client.get("/networth").text
    # Dark Reader would paint over the chart's canvas: the stylesheet has a dark mode.
    assert '<meta name="darkreader-lock">' in page
    for asset in ("vendor/uPlot.iife.min.js", "vendor/uPlot.min.css", "networth.js"):
        address = f"http://127.0.0.1:8765/static/{asset}"
        assert address in page
        assert client.get(address).status_code == 200
    assert "<script" not in client.get("/").text  # only this page has a script
