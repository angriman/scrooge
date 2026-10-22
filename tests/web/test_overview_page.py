"""The first page: net worth on the last day, its parts and the largest holdings."""

import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze import overview, staging
from finanze.models import PrintedValue
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


def ring(html: str) -> list[tuple[str, ...]]:
    """Each slice of the ring: its slot, dash, gap, offset, and the name, amount (short) and
    share it prints when pointed at."""
    return re.findall(
        r'<circle class="slice slot-(\d)" [^>]* stroke-dasharray="([\d.]+) ([\d.]+)" '
        r'stroke-dashoffset="(-?[\d.]+)" data-slot="\1" data-name="([^"]*)" '
        r'data-value="([^"]*)" data-share="([^"]*)">',
        html,
    )


def links(html: str) -> dict[str, str]:
    """What each link of the page's body says -> where it leads, without the site."""
    body = html.partition("<main>")[2].replace(SITE, "")
    return {text(said): href for href, said in re.findall(r'<a href="([^"]*)">(.*?)</a>', body)}


def test_everyone_s_net_worth_is_the_headline_with_what_it_is_made_of(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    page = response.text
    assert "30,847.50 EUR net worth on 2026-09-30" in text(page)
    kinds, kinds_total, holdings, securities, persons, persons_total, changes = tables(page)
    # In the order the chart stacks them, each with what it was 30 days and a year before.
    assert headers(page)[0] == ["", "EUR", "Share", "30 days before", "A year before"]
    assert kinds == [
        ["Cash", "19,325.00", "62.65%", "31,350.00", NONE],
        ["Bills", "9,872.50", "32.00%", "0.00", NONE],
        ["ETFs", "900.00", "2.92%", "0.00", NONE],
        ["Stocks", "900.00", "2.92%", "0.00", NONE],
        ["Card", "-150.00", "-0.49%", "-375.00", NONE],  # owed: below zero, and so is its share
    ]
    assert kinds_total == [["Net worth", "30,847.50", "100.00%", "30,975.00", NONE]]
    assert persons == [
        [PARTNER, "10,100.00", "32.74%", "10,100.00", NONE],
        [OWNER, "20,747.50", "67.26%", "20,875.00", NONE],
    ]
    assert persons_total == kinds_total
    # Each kind wears the colour of its band in the chart, and its row names its slice.
    assert re.findall(r'<tr data-slot="(\d)">\s*<td><span class="key slot-\1">', page) == [
        "1",
        "2",
        "5",
        "6",
        "7",
    ]
    assert headers(page)[1] == ["Instrument", "Kind", "EUR", "Share of net worth", ""]
    assert holdings == [
        ["912797ZZ1", "Bills", "9,872.50", "32.00%", ""],
        ["GADG &lt;Gadget&gt; Inc", "Stocks", "900.00", "2.92%", ""],
        ["WIDG Widget Index Fund", "ETFs", "900.00", "2.92%", ""],  # three accounts, one row
    ]
    assert securities == [["All securities", "11,672.50", "37.84%", ""]]
    assert "<h2>Holdings</h2>" in page  # all of them: none is cut
    assert headers(page)[3] == ["", "EUR", "Change since", "In percent"]
    assert changes == [
        ["30 days before", "30,975.00", "-127.50", "-0.41%"],
        ["A year before", NONE, NONE, NONE],  # the history is a month long
    ]
    assert "what is owed counts below zero, and so does its share" in text(page)
    assert "Options and futures are not counted as assets" in text(page)
    assert "Only committed statements are shown" in text(page)
    assert errors(page) == []
    assert " style=" not in page  # the page's own policy would refuse it


def test_the_ring_is_what_is_held_and_what_is_owed_is_a_note(client: TestClient) -> None:
    page = client.get("/").text
    # Clockwise from the top in the order of the chart, each a share of the 30,997.50 held
    # less a gap; the card is not in it.
    assert ring(page) == [
        ("1", "61.75", "38.25", "0", "Cash", "19.3k", "62.35% of assets"),
        ("2", "31.25", "68.75", "-62.35", "Bills", "9.9k", "31.85% of assets"),
        ("5", "2.30", "97.70", "-94.20", "ETFs", "900", "2.90% of assets"),
        ("6", "2.30", "97.70", "-97.10", "Stocks", "900", "2.90% of assets"),
    ]
    middle = re.findall(r'<text class="(\w+)" [^>]*>([^<]*)</text>', page)
    assert middle == [("what", "Assets"), ("amount", "31.0k"), ("share", "EUR")]
    # The exact amounts stay in the titles, without the script.
    assert "<title>Bills: 9,872.50 EUR, 31.85% of assets</title>" in page
    assert '<title id="held-title">What is held, 30,997.50 EUR: ' in page
    assert "Owed, and not in the ring: Card -150.00 EUR." in text(page)
    hers = client.get("/", params={"owner": PARTNER, "currency": "CHF"}).text
    assert [slice[4:] for slice in ring(hers)] == [
        ("Cash", "7.8k", "96.04% of assets"),
        ("ETFs", "320", "3.96% of assets"),
    ]
    assert "Owed, and not in the ring" not in hers


def test_the_chart_comes_first_then_what_is_held_and_the_changes_last(client: TestClient) -> None:
    """The order the page was asked for: the chart, the ring, the largest holdings, the
    people, then how net worth has changed, and the brokers' own figures at the end (only
    the notes on the figures, folded away, come after them)."""
    page = client.get("/").text
    sections = [
        '<p class="headline">',
        '<figure class="chart" id="networth"',
        "<h2>By kind of asset</h2>",
        '<figure class="chart donut" id="held">',
        "<h2>Holdings</h2>",
        "The same instrument in several accounts is one holding here.",
        "<h2>By person</h2>",
        "<h2>Change</h2>",
        "Options and futures are not counted as assets",
    ]
    places = [page.find(section) for section in sections]
    assert -1 not in places
    assert places == sorted(places)
    # The changes are a table of their own under their heading, no longer under the headline.
    after = page.partition("<h2>Change</h2>")[2]
    assert after.lstrip().startswith('<table class="narrow tight">')
    assert "Change since" not in page.partition("<h2>Change</h2>")[0]
    mine = client.get("/", params={"owner": OWNER}).text
    assert mine.find("<h2>Holdings</h2>") < mine.find("<h2>Change</h2>")
    assert "<h2>By person</h2>" not in mine


def test_the_brokers_own_figures_are_last(tmp_path: Path) -> None:
    path = tmp_path / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    cash = account("migros", "Migros", "CHF", "1000")
    printed = (PrintedValue("Cash", "CHF", Decimal(990)),)
    close(conn, replace(cash, extraction=replace(cash.extraction, values=printed)))
    conn.close()
    page = served(path).get("/", params={"currency": "CHF"}).text
    details = page.find("<summary>Against the brokers&#39; own figures</summary>")
    assert page.find("<h2>Change</h2>") < page.find("Options and futures") < details
    assert page.rfind("<h2>") < details


def test_the_notes_on_the_figures_are_folded_away_at_the_bottom(tmp_path: Path) -> None:
    """What the reader must know about how a figure came about is not under the ring: it is
    the page's last part, closed until clicked, and says how many notes it holds."""
    path = tmp_path / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    cash = account("migros", "Migros", "CHF", "1000")
    printed = (PrintedValue("Cash", "CHF", Decimal(990)),)
    close(conn, replace(cash, extraction=replace(cash.extraction, values=printed)))
    for on, name in ((date(2026, 9, 5), "Old bank"), (date(2026, 9, 20), "Other bank")):
        staging.type_balance(
            conn, owner=OWNER, account=name, day=on, amount=Decimal(250), currency="CHF"
        )
    conn.close()
    page = served(path).get("/", params={"currency": "CHF"}).text
    typed = "Old bank of Lukas has no statements: it is carried at what was typed, last for"
    assert page.count("has no statements: it is carried at what was typed") == 2
    notes = page.find("<details>\n  <summary>2 notes on how the figures are worked out</summary>")
    brokers = page.find("<summary>Against the brokers&#39; own figures</summary>")
    assert -1 < brokers < notes < page.find(typed)
    assert page.find("<h2>Change</h2>") < page.find("Options and futures") < notes
    assert page.rfind("<h2>") < notes
    assert page.rfind("</details>") > page.find(typed)  # inside it, the page's last part
    assert typed not in page.partition("<h2>Holdings</h2>")[0]  # no longer under the ring


def test_a_single_note_is_one_note(tmp_path: Path) -> None:
    path = tmp_path / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    close(conn, account("migros", "Migros", "CHF", "1000"))
    staging.type_balance(
        conn,
        owner=OWNER,
        account="Old bank",
        day=date(2026, 9, 5),
        amount=Decimal(250),
        currency="CHF",
    )
    conn.close()
    page = served(path).get("/", params={"currency": "CHF"}).text
    assert "<details>\n  <summary>1 note on how the figures are worked out</summary>" in page
    assert page.count("<details") == 1  # the only fold, closed: `<details>` without `open`


def test_without_notes_there_is_nothing_to_unfold(client: TestClient) -> None:
    page = client.get("/").text
    assert "on how the figures are worked out" not in page
    assert "<details" not in page


def test_one_person_s_overview_is_not_cut_by_person(client: TestClient) -> None:
    response = client.get("/", params={"owner": PARTNER, "currency": "chf"})
    page = response.text
    assert "8,080.00 CHF net worth on 2026-09-30" in text(page)
    assert "By person" not in page
    kinds, total, holdings, securities, _ = tables(page)
    assert kinds == [
        ["Cash", "7,760.00", "96.04%", "8,080.00", NONE],
        ["ETFs", "320.00", "3.96%", "0.00", NONE],
    ]
    assert total == [["Net worth", "8,080.00", "100.00%", "8,080.00", NONE]]
    assert holdings == [["WIDG Widget Index Fund", "ETFs", "320.00", "3.96%", ""]]
    assert securities == [["All securities", "320.00", "3.96%", ""]]
    assert "<option selected>CHF</option>" in page
    assert f'<input type="hidden" name="back" value="/?owner={PARTNER}&amp;currency=chf">' in page


def test_the_links_keep_the_person_and_the_currency(client: TestClient) -> None:
    everyone = client.get("/", params={"currency": "CHF"}).text
    assert links(everyone) == {
        PARTNER: f"/?currency=CHF&amp;owner={PARTNER}",
        OWNER: f"/?currency=CHF&amp;owner={OWNER}",
        "All holdings": "/holdings?currency=CHF",
    }
    plain = everyone.replace(SITE, "")
    assert '<a href="/?currency=CHF" aria-current="true">Everyone</a>' in plain
    assert f'<a href="/?currency=CHF&amp;owner={OWNER}">{OWNER}</a>' in plain
    his = client.get("/", params={"currency": "CHF", "owner": OWNER}).text
    assert links(his) == {
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
    _, _, holdings, securities, *_ = tables(page)
    assert holdings == [
        ["912797ZZ1", "Bills", "9,872.50", "32.00%", ""],
        ["GADG &lt;Gadget&gt; Inc", "Stocks", "900.00", "2.92%", ""],
        ["The 1 other", "900.00", "2.92%", ""],
    ]
    assert securities == [["All securities", "11,672.50", "37.84%", ""]]
    assert "<h2>Largest holdings</h2>" in page
    monkeypatch.setattr(overview, "TOP", 1)
    assert tables(client.get("/").text)[2][-1][0] == "The 2 others"


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
    assert len(tables(page)) == 3  # the kinds with their total, and the changes
    assert "All holdings" not in page
    assert ring(page) == [("1", "100.00", "0.00", "0", "Cash", "1.0k", "100.00% of assets")]


def test_a_net_worth_below_zero_has_dashes_for_shares(tmp_path: Path) -> None:
    path = tmp_path / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    close(conn, account("swisscard", "Swisscard", "CHF", "-300"))
    conn.close()
    page = served(path).get("/", params={"currency": "CHF"}).text
    assert "-300.00 CHF net worth on 2026-09-30" in text(page)
    assert tables(page)[:2] == [
        [["Card", "-300.00", NONE, "-300.00", NONE]],
        [["Net worth", "-300.00", NONE, "-300.00", NONE]],
    ]
    assert 'id="held"' not in page  # nothing is held: there is no ring


def test_a_person_the_database_does_not_have_is_not_found(client: TestClient) -> None:
    response = client.get("/", params={"owner": "Zoe"})
    assert response.status_code == 404
    assert response.text == "no owner named 'Zoe'\n"
