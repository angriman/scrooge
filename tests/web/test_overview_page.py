"""The first page: net worth on the last day, its parts and the largest holdings."""

import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze import overview
from finanze.storage import db
from finanze.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER
from tests.test_networth import NOW, account, close
from tests.test_overview import household
from tests.web.test_app import text
from tests.web.test_cashflow_page import tables
from tests.web.test_holdings_page import NONE, errors, headers

SITE = "http://127.0.0.1:8765"


def served(path: Path) -> TestClient:
    return TestClient(webapp.create_app(path), base_url=SITE)


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """The household of the service's tests: both people, a card, the same ETF three times."""
    path: Path = tmp_path_factory.mktemp("overview") / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    household(conn)
    conn.execute("UPDATE instruments SET name = '<Gadget> Inc' WHERE symbol = 'GADG'")
    conn.close()
    return served(path)


@pytest.fixture
def empty(tmp_path: Path) -> TestClient:
    path = tmp_path / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    conn.close()
    return served(path)


def links(html: str) -> dict[str, str]:
    """What each link of the page's body says -> where it leads, without the site."""
    body = html.partition("<main>")[2].replace(SITE, "")
    return {text(said): href for href, said in re.findall(r'<a href="([^"]*)">(.*?)</a>', body)}


def test_everyone_s_net_worth_is_the_headline_with_what_it_is_made_of(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    page = response.text
    assert "30,847.50 EUR net worth on 2026-09-30" in text(page)
    changes, kinds, kinds_total, persons, persons_total, holdings, securities = tables(page)
    assert headers(page)[0] == ["", "EUR", "Change since", "In percent"]
    assert changes == [
        ["30 days before", "30,975.00", "-127.50", "-0.41%"],
        ["A year before", NONE, NONE, NONE],  # the history is a month long
    ]
    assert kinds == [
        ["Cash", "19,325.00", "62.65%"],
        ["ETFs", "900.00", "2.92%"],
        ["Stocks", "900.00", "2.92%"],
        ["Bills", "9,872.50", "32.00%"],
        ["Card", "-150.00", "-0.49%"],  # owed: below zero, and so is its share
    ]
    assert kinds_total == [["Net worth", "30,847.50", "100.00%"]]
    assert persons == [[PARTNER, "10,100.00", "32.74%"], [OWNER, "20,747.50", "67.26%"]]
    assert persons_total == kinds_total
    assert headers(page)[3] == ["Instrument", "Kind", "EUR", "Share of net worth", ""]
    assert holdings == [
        ["912797ZZ1", "Bills", "9,872.50", "32.00%", ""],
        ["GADG &lt;Gadget&gt; Inc", "Stocks", "900.00", "2.92%", ""],
        ["WIDG Widget Index Fund", "ETFs", "900.00", "2.92%", ""],  # three accounts, one row
    ]
    assert securities == [["All securities", "11,672.50", "37.84%", ""]]
    assert "<h2>Holdings</h2>" in page  # all of them: none is cut
    assert "what is owed counts below zero, and so does its share" in text(page)
    assert "Options and futures are not counted as assets" in text(page)
    assert "Only committed statements are shown" in text(page)
    assert errors(page) == []
    for banned in (" style=", "<canvas", 'id="networth"'):
        assert banned not in page
    assert re.findall(r"<script[^>]*>", page) == [f'<script src="{SITE}/static/choose.js" defer>']


def test_one_person_s_overview_is_not_cut_by_person(client: TestClient) -> None:
    response = client.get("/", params={"owner": PARTNER, "currency": "chf"})
    page = response.text
    assert "8,080.00 CHF net worth on 2026-09-30" in text(page)
    assert "By person" not in page
    _, kinds, total, holdings, securities = tables(page)
    assert kinds == [["Cash", "7,760.00", "96.04%"], ["ETFs", "320.00", "3.96%"]]
    assert total == [["Net worth", "8,080.00", "100.00%"]]
    assert holdings == [["WIDG Widget Index Fund", "ETFs", "320.00", "3.96%", ""]]
    assert securities == [["All securities", "320.00", "3.96%", ""]]
    assert "<option selected>CHF</option>" in page
    assert f'<input type="hidden" name="back" value="/?owner={PARTNER}&amp;currency=chf">' in page


def test_the_links_keep_the_person_and_the_currency(client: TestClient) -> None:
    everyone = client.get("/", params={"currency": "CHF"}).text
    assert links(everyone) == {
        "The chart": "/networth?currency=CHF",
        PARTNER: f"/?currency=CHF&amp;owner={PARTNER}",
        OWNER: f"/?currency=CHF&amp;owner={OWNER}",
        "All holdings": "/holdings?currency=CHF",
    }
    plain = everyone.replace(SITE, "")
    assert '<a href="/?currency=CHF" aria-current="true">Everyone</a>' in plain
    assert f'<a href="/?currency=CHF&amp;owner={OWNER}">{OWNER}</a>' in plain
    his = client.get("/", params={"currency": "CHF", "owner": OWNER}).text
    assert links(his) == {
        "The chart": f"/networth?currency=CHF&amp;owner={OWNER}",
        "All holdings": f"/holdings?currency=CHF&amp;owner={OWNER}",
    }
    assert '<a href="/?currency=CHF">Everyone</a>' in his.replace(SITE, "")
    for address in (*links(everyone).values(), *links(his).values()):
        assert client.get(address.replace("&amp;", "&")).status_code == 200


def test_the_holdings_beyond_the_largest_are_one_line(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(overview, "TOP", 2)
    page = client.get("/", params={"currency": "USD"}).text
    *_, holdings, securities = tables(page)
    assert holdings == [
        ["912797ZZ1", "Bills", "9,872.50", "32.00%", ""],
        ["GADG &lt;Gadget&gt; Inc", "Stocks", "900.00", "2.92%", ""],
        ["The 1 other", "900.00", "2.92%", ""],
    ]
    assert securities == [["All securities", "11,672.50", "37.84%", ""]]
    assert "<h2>Largest holdings</h2>" in page
    monkeypatch.setattr(overview, "TOP", 1)
    assert tables(client.get("/").text)[-2][-1][0] == "The 2 others"


def test_the_columns_of_every_row_line_up(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(overview, "TOP", 2)
    page = client.get("/").text
    found = re.findall(r"<table[^>]*>(.*?)</table>", page, re.DOTALL)
    assert len(found) == 4
    for table, titles in zip(found, headers(page), strict=True):
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL):
            spans = [int(n or 1) for n in re.findall(r"<t[dh](?: colspan=\"(\d+)\")?[ >]", row)]
            assert sum(spans) == len(titles)


def test_a_currency_that_cannot_be_read_is_reported_and_euros_shown(client: TestClient) -> None:
    response = client.get("/", params={"currency": "XAU"})
    assert response.status_code == 200
    assert errors(response.text) == ["&#39;XAU&#39; is not one of EUR, CHF, USD, GBP, JPY."]
    assert "30,847.50 EUR net worth" in text(response.text)
    assert "<option selected>EUR</option>" in response.text
    assert links(response.text)["All holdings"] == "/holdings?currency=EUR"


def test_a_currency_without_a_rate_is_said_and_nothing_is_listed(client: TestClient) -> None:
    response = client.get("/", params={"currency": "GBP"})
    assert response.status_code == 200
    [error] = errors(response.text)
    assert error.startswith("No exchange rate is stored for GBP")
    assert "<table" not in response.text
    assert "Nothing is committed yet" not in response.text
    assert "<option selected>GBP</option>" in response.text


def test_nothing_committed_is_said(empty: TestClient) -> None:
    response = empty.get("/")
    assert response.status_code == 200
    assert "Nothing is committed yet." in response.text
    assert "<table" not in response.text
    assert "Options and futures" not in response.text


def test_cash_alone_is_no_holdings_and_no_empty_table(tmp_path: Path) -> None:
    path = tmp_path / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    close(conn, account("migros", "Migros", "CHF", "1000"))
    conn.close()
    page = served(path).get("/", params={"currency": "CHF"}).text
    assert "1,000.00 CHF net worth on 2026-09-30" in text(page)
    assert "No securities are held on 2026-09-30." in page
    assert len(tables(page)) == 3  # the changes, and the kinds with their total
    assert "All holdings" not in page


def test_a_net_worth_below_zero_has_dashes_for_shares(tmp_path: Path) -> None:
    path = tmp_path / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    close(conn, account("swisscard", "Swisscard", "CHF", "-300"))
    conn.close()
    page = served(path).get("/", params={"currency": "CHF"}).text
    assert "-300.00 CHF net worth on 2026-09-30" in text(page)
    assert tables(page)[1:3] == [[["Card", "-300.00", NONE]], [["Net worth", "-300.00", NONE]]]


def test_a_person_the_database_does_not_have_is_not_found(client: TestClient) -> None:
    response = client.get("/", params={"owner": "Zoe"})
    assert response.status_code == 404
    assert response.text == "no owner named 'Zoe'\n"
