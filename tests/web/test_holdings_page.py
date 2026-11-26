"""The holdings page: what is held on the last committed day, position by position."""

import re
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import portfolio
from scrooge.models import Holding, SecurityTrade
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER
from tests.test_networth import NOW, account, bill, bought, close, day, ibkr, price, rate
from tests.web.test_analysis_cashflow import tables
from tests.web.test_app import text

SITE = "http://127.0.0.1:8765"
NONE = "\u2013"  # what a cell shows where there is no figure
DUE = "912797WW4"  # a bill that matured two days before the last day and is not repaid yet
FUND = "CH0000000001"
LAST = datetime(2026, 9, 30, 18, 0, tzinfo=UTC)  # a clock on the last day committed


def at(path: Path, now: datetime = LAST) -> TestClient:
    """The app over `path` on a clock that says `now`: the bills are valued on that day."""
    return TestClient(webapp.create_app(path, clock=lambda: now), base_url=SITE)


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    path: Path = tmp_path_factory.mktemp("holdings") / "scrooge.db"
    household(path)
    return at(path)


def household(path: Path) -> None:
    """September 2026, committed for both, in dollars; a dollar and a euro are 0.80 francs.

    Lukas holds an ETF whose last close is of the 18th, a share with a close on the last day,
    another with none at all, and two bills; Anna holds the same ETF.
    """
    conn = db.connect(path)
    db.migrate(conn, NOW)
    close(
        conn,
        ibkr(
            bought("WIDG", "10", "-500", day(15)),
            bought("GADG", "3", "-900", day(16), asset_tab="Stocks"),
            bought("SPRK", "20.50", "-410", day(16), asset_tab="Stocks"),
            bill("10000", "-9850", day(15)),  # matures on 24 December
            bought(
                DUE,
                "5000",
                "-4950",
                day(16),
                asset_tab="Bonds",
                price=Decimal(99),
                maturity=date(2026, 9, 28),
            ),
            opening="50000",
        ),
    )
    close(conn, ibkr(bought("WIDG", "7", "-350", day(10))), owner=PARTNER)
    conn.execute("UPDATE instruments SET name = 'Widget <Index> Fund' WHERE symbol = 'WIDG'")
    price(conn, "WIDG", day(15), "51")
    price(conn, "WIDG", day(18), "53.5")
    price(conn, "SPRK", day(30), "18")
    rate(conn, "USD", day(1), "0.8")
    rate(conn, "EUR", day(1), "0.8")
    conn.close()


@pytest.fixture(scope="module")
def odd(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """Lukas alone: a pension account with fund units and cash, and shares of which six were
    held before the history starts. A dollar is 0.80 francs; no rate is stored for the euro."""
    path: Path = tmp_path_factory.mktemp("odd") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    unit = bought(
        FUND, "2", "-300", day(15), asset_tab="Funds", account="Pillar 3a", currency="CHF"
    )
    broker = ibkr(bought("WIDG", "4", "-200", day(15)))
    printed = (Holding("IBKR USD", "WIDG", "USD", Decimal(10), Decimal(55), Decimal(550), "ETFs"),)
    close(
        conn,
        account("finpension", "Pillar 3a", "CHF", "1000", unit),
        replace(broker, extraction=replace(broker.extraction, holdings=printed)),
    )
    price(conn, FUND, day(30), "155")
    rate(conn, "USD", day(1), "0.8")
    conn.close()
    return TestClient(webapp.create_app(path), base_url=SITE)


@pytest.fixture
def empty(tmp_path: Path) -> TestClient:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    conn.close()
    return TestClient(webapp.create_app(path), base_url=SITE)


def headers(html: str) -> list[list[str]]:
    """The column titles of every table on the page."""
    heads = re.findall(r"<thead>(.*?)</thead>", html, re.DOTALL)
    return [[text(cell) for cell in re.findall(r"<th[^>]*>(.*?)</th>", head)] for head in heads]


def tiles(html: str) -> list[str]:
    """What each tile of the page says."""
    found = re.findall(r'<section class="tile[^"]*">(.*?)</section>', html, re.DOTALL)
    return [text(tile) for tile in found]


def lenses(html: str) -> dict[str, str]:
    """How what is held is spread: each cut's title -> its parts and their shares."""
    found = re.findall(r'aria-label="By (\w+): ([^"]*)"', html)
    return {cut.capitalize(): parts for cut, parts in found}


def errors(html: str) -> list[str]:
    found: list[str] = re.findall(r'<p class="error" role="alert">(.*?)</p>', html)
    return found


def test_everyone_s_positions_are_listed_by_kind_with_what_they_add_up_to(
    client: TestClient,
) -> None:
    response = client.get("/holdings")
    assert response.status_code == 200
    page = response.text
    assert tiles(page)[0] == "Securities on 2026-09-30 17,051.00 EUR Statements up to 2026-09-30"
    etfs, stocks, bills, everything, _ = tables(page)
    assert lenses(page)["Kind"] == "Bills 87.22%, Stocks 7.44%, ETFs 5.34%"
    assert headers(page)[0] == [
        "Instrument",
        "Person",
        "Account",
        "Quantity",
        "Price",
        "Value",
        "Cost",
        "Gain or loss",
        "Value in EUR",
        "Share",
        "",
    ]
    assert etfs == [
        ["ETFs"],
        [
            "WIDG Widget &lt;Index&gt; Fund",
            OWNER,
            "IBKR USD",
            "10",
            "53.5 USD close of 2026-09-18",  # stale: the day the close is from
            "535.00 USD",
            "500.00",
            "+35.00 +7.00%",
            "535.00",
            "3.14%",
            "",
        ],
        [
            "WIDG Widget &lt;Index&gt; Fund",
            PARTNER,
            "IBKR USD",
            "7",
            "53.5 USD close of 2026-09-18",
            "374.50 USD",
            "350.00",
            "+24.50 +7.00%",
            "374.50",
            "2.20%",
            "",
        ],
        ["All ETFs", "909.50", "5.34%", ""],
    ]
    assert [row[0] for row in stocks] == ["Stocks", "GADG", "SPRK", "All Stocks"]
    # a close of the day shown needs no day; a quantity keeps its decimals but not its zeros
    assert stocks[2][3:10] == [
        "20.5",
        "18 USD",
        "369.00 USD",
        "410.00",
        "-41.00 -10.00%",
        "369.00",
        "2.16%",
    ]
    # a bill has no price; its quantity is its face
    assert bills[1][:6] == ["912797ZZ1", OWNER, "IBKR USD", "10,000", NONE, "9,872.50 USD"]
    assert bills[-1] == ["All Bills", "14,872.50", "87.22%", ""]
    assert everything == [["All 6", "17,051.00", "100.00%", ""]]
    assert re.findall(r"<script[^>]*>", page) == [
        f'<script src="{SITE}/static/choose.js" defer>',
        f'<script src="{SITE}/static/views.js" defer>',
    ]
    assert " style=" not in page


def test_the_columns_of_every_row_line_up(client: TestClient) -> None:
    for owner, columns in ((None, 11), (OWNER, 10)):
        page = client.get("/holdings", params={"owner": owner} if owner else {}).text
        assert len(headers(page)[0]) == columns
        body = page.partition("<summary>Every position")[2].partition("</table>")[0]
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL):
            spans = [int(n or 1) for n in re.findall(r"<t[dh](?: colspan=\"(\d+)\")?[ >]", row)]
            assert sum(spans) == columns
        ladder = page.partition("<summary>Every bill")[2]
        assert {len(row) for row in tables(ladder)[0]} == {len(headers(ladder)[0])} == {columns - 1}


def test_a_price_that_is_only_that_of_a_trade_is_marked(client: TestClient) -> None:
    page = client.get("/holdings").text
    gadget = tables(page)[1][1]
    assert gadget[3:8] == [
        "3",
        "300 USD last trade, 2026-09-16",
        "900.00 USD",
        "900.00",
        "+0.00 +0.00%",
    ]
    assert "<small>last trade, 2026-09-16</small>" in page
    assert "No close is stored for GADG: valued at the price of the last trade." in text(page)


def test_one_person_s_positions_need_no_column_for_the_person(client: TestClient) -> None:
    response = client.get("/holdings", params={"owner": PARTNER})
    assert response.status_code == 200
    page = response.text
    assert tiles(page)[0].startswith("Securities on 2026-09-30 374.50 EUR")
    assert all("Person" not in titles for titles in headers(page))
    etfs, everything = tables(page)
    assert lenses(page) == {
        "Kind": "ETFs 100.00%",
        "Currency": "USD 100.00%",
        "Account": "IBKR USD 100.00%",  # hers alone: no person beside it, and no cut by person
    }
    assert etfs[1][:3] == ["WIDG Widget &lt;Index&gt; Fund", "IBKR USD", "7"]
    assert everything == [["All 1", "374.50", "100.00%", ""]]
    assert "Bills by maturity" not in page  # she holds none
    assert f'<input type="hidden" name="back" value="/holdings?owner={PARTNER}">' in page


def test_the_figures_are_in_the_currency_asked_for(client: TestClient) -> None:
    response = client.get("/holdings", params={"currency": "chf"})
    page = response.text
    assert tiles(page)[0].startswith("Securities on 2026-09-30 13,640.80 CHF")
    assert "<option selected>CHF</option>" in page
    assert headers(page)[0][8] == "Value in CHF"
    # what is in the instrument's currency stays as it is
    assert tables(page)[0][1][4:9] == [
        "53.5 USD close of 2026-09-18",
        "535.00 USD",
        "500.00",
        "+35.00 +7.00%",
        "428.00",
    ]
    assert errors(page) == []


def test_a_currency_that_cannot_be_read_is_reported_and_euros_shown(client: TestClient) -> None:
    response = client.get("/holdings", params={"currency": "XAU"})
    assert response.status_code == 200
    assert errors(response.text) == ["&#39;XAU&#39; is not one of EUR, CHF, USD, GBP, JPY."]
    assert tiles(response.text)[0].startswith("Securities on 2026-09-30 17,051.00 EUR")
    assert "<option selected>EUR</option>" in response.text


def test_the_bills_are_listed_by_maturity_and_one_that_is_due_says_so(client: TestClient) -> None:
    page = client.get("/holdings", params={"currency": "CHF"}).text
    assert headers(page)[1] == [
        "Maturity",
        "Days to go",
        "Bill",
        "Person",
        "Account",
        "Face",
        "Cost",
        "Interest",
        "Value today",
        "Value in CHF",
    ]
    assert tables(page)[-1] == [
        [
            "2026-09-28",
            "matured 2 days ago",
            DUE,
            OWNER,
            "IBKR USD",
            "5,000 USD",
            "4,950.00",
            "50.00",
            "5,000.00",
            "4,000.00",
        ],
        [
            "2026-12-24",
            "85",
            "912797ZZ1",
            OWNER,
            "IBKR USD",
            "10,000 USD",
            "9,850.00",
            "150.00",
            "9,872.50",
            "7,898.00",
        ],
    ]
    mine = client.get("/holdings", params={"owner": OWNER}).text
    assert "Person" not in headers(mine)[1]
    assert [row[:3] for row in tables(mine)[-1]] == [
        ["2026-09-28", "matured 2 days ago", DUE],
        ["2026-12-24", "85", "912797ZZ1"],
    ]
    assert "Interest is what a bill earns when it is repaid: its face less what it cost" in text(
        page
    )


def test_the_bills_are_valued_today_at_the_last_rate_stored(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    household(path)
    conn = db.connect(path)
    rate(conn, "USD", day(20, 10), "0.9")
    conn.close()
    today = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
    page = at(path, today).get("/holdings", params={"currency": "CHF"})
    assert [row[1:3] + row[-2:] for row in tables(page.text)[-1]] == [
        ["matured 23 days ago", DUE, "5,000.00", "4,500.00"],
        ["64", "912797ZZ1", "9,904.00", "8,913.60"],  # 36 days of 100 from cost to face
    ]
    said = text(page.text)
    assert "Days to go and values are of 2026-10-21" in said
    assert "One that has matured since 2026-09-30 may be repaid already" in said
    # a clock still on the last day committed: the ladder is never before the newest rate
    on_the_day = text(at(path).get("/holdings").text)
    assert "Days to go and values are of 2026-10-20" in on_the_day
    assert "Securities on 2026-10-20" in said  # the newest rate stored
    assert "Statements up to 2026-09-30: what was held then is valued at the latest" in said


def test_a_bill_without_a_day_or_due_on_the_day_shown_is_worded_too(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)

    def other(cusip: str, maturity: date) -> SecurityTrade:
        return bought(
            cusip, "5000", "-4950", day(16), asset_tab="Bonds", price=Decimal(99), maturity=maturity
        )

    close(
        conn,
        ibkr(
            other("912797AA9", day(29)),
            other("912797BB8", day(30)),
            other("912797CC7", day(2, 10)),
            opening="50000",
        ),
    )
    conn.execute("UPDATE instruments SET maturity = NULL WHERE symbol = '912797CC7'")
    conn.close()
    page = at(path).get("/holdings?currency=USD").text
    assert [row[:3] for row in tables(page)[-1]] == [
        ["2026-09-29", "matured 1 day ago", "912797AA9"],
        ["2026-09-30", "matures today", "912797BB8"],
        [NONE, NONE, "912797CC7"],
    ]


def test_a_cost_that_is_not_known_is_a_dash(odd: TestClient) -> None:
    page = odd.get("/holdings", params={"currency": "CHF", "owner": OWNER}).text
    assert tiles(page)[0].startswith("Securities on 2026-09-30 750.00 CHF")
    etfs, pension, _ = tables(page)
    assert lenses(page)["Kind"] == "ETFs 58.67%, Pillar 3a 41.33%"
    assert etfs[1] == [
        "WIDG",
        "IBKR USD",
        "10",
        "55 USD",
        "550.00 USD",
        NONE,
        NONE,
        "440.00",
        "58.67%",
        "",
    ]
    assert pension[1][:7] == [
        FUND,
        "Pillar 3a",
        "2",
        "155 CHF",
        "310.00 CHF",
        "300.00",
        "+10.00 +3.33%",
    ]
    assert "Bills by maturity" not in page
    assert "A dash stands where the cost is not known." in text(page)


def test_the_cash_of_a_pension_account_is_said_to_be_on_the_other_page(
    odd: TestClient, client: TestClient
) -> None:
    said = "The cash of the pension account, 700.00 CHF, is counted in the Pillar 3a line"
    assert said in text(odd.get("/holdings", params={"currency": "CHF"}).text)
    assert "875.00 USD, is counted" in text(odd.get("/holdings", params={"currency": "USD"}).text)
    assert "pension account" not in client.get("/holdings").text  # nobody has one


def test_a_currency_without_a_rate_is_said_and_nothing_is_listed(odd: TestClient) -> None:
    response = odd.get("/holdings")
    assert response.status_code == 200
    [error] = errors(response.text)
    assert error.startswith("No exchange rate is stored for EUR")
    assert error.endswith(".")
    assert "<table" not in response.text
    assert "Nothing is committed yet" not in response.text
    assert "<option selected>EUR</option>" in response.text


def test_the_fixed_notes_say_what_is_not_counted_and_what_a_cost_is(client: TestClient) -> None:
    said = text(client.get("/holdings").text)
    assert "Options and futures are not counted as assets" in said
    assert "what the lots still held cost, the oldest sold first, without commissions" in said
    assert "cost what they were worth on the day they arrived" in said


def test_nothing_committed_is_said(empty: TestClient) -> None:
    response = empty.get("/holdings")
    assert response.status_code == 200
    assert "Nothing is committed yet." in response.text
    assert "<table" not in response.text
    assert "Options and futures" not in response.text


def test_cash_alone_is_no_securities_and_no_empty_table(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    close(conn, account("migros", "Migros", "CHF", "1000"))
    conn.close()
    page = TestClient(webapp.create_app(path), base_url=SITE).get("/holdings").text
    assert "No securities are held on 2026-09-30." in page
    assert "<table" not in page
    assert "Nothing is committed yet" not in page
    assert "Options and futures are not counted as assets" in text(page)


def test_a_person_the_database_does_not_have_is_not_found(client: TestClient) -> None:
    response = client.get("/holdings", params={"owner": "Zoe"})
    assert response.status_code == 404
    assert response.text == "no owner named 'Zoe'\n"


def test_the_page_is_linked_after_the_overview_and_the_person_keeps_the_currency(
    client: TestClient,
) -> None:
    home = client.get("/", params={"owner": PARTNER}).text.replace(SITE, "")
    after = home.partition(f'<a href="/?owner={PARTNER}" aria-current="page">Overview</a>')[2]
    assert after.lstrip().startswith(f'<a href="/holdings?owner={PARTNER}">Holdings</a>')
    page = client.get("/holdings", params={"currency": "CHF"}).text.replace(SITE, "")
    assert '<a href="/holdings" aria-current="page">Holdings</a>' in page
    assert '<a href="/holdings?currency=CHF" aria-current="true">Everyone</a>' in page
    for person in (OWNER, PARTNER):
        assert f'<a href="/holdings?currency=CHF&amp;owner={person}">{person}</a>' in page
    hers = client.get("/holdings", params={"currency": "CHF", "owner": PARTNER}).text
    assert '<a href="/holdings?currency=CHF">Everyone</a>' in hers.replace(SITE, "")


def test_the_page_forbids_loading_from_elsewhere(client: TestClient) -> None:
    sent = client.get("/holdings").headers
    assert sent["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"
    assert sent["x-content-type-options"] == "nosniff"
    assert sent["referrer-policy"] == "no-referrer"


def listed(html: str, name: str) -> list[str]:
    """What each item of the list of class `name` says."""
    found = re.search(rf'<ol class="{name}">(.*?)</ol>', html, re.DOTALL)
    return [text(item) for item in found.group(1).split("</li>")[:-1]] if found else []


def test_the_figures_come_first_the_holdings_as_a_map_or_a_list(client: TestClient) -> None:
    page = client.get("/holdings").text
    assert tiles(page)[1:] == [
        "Gain or loss +91.00 +0.54% On a cost of 16,960.00",
        "Holdings 5 6 positions in 2 accounts",  # the same broker's, of two people
        "Next maturity 2026-12-24 In 85 days, 9,872.50",  # not the one that has matured
    ]
    # Each instrument once, largest first: a rectangle of the map and an item of the list.
    assert re.findall(r'<g clip-path="url\(#tile-\d\)"><title>([^:]*):', page) == [
        "912797ZZ1",
        "912797WW4",
        "WIDG Widget &lt;Index&gt; Fund",
        "GADG",
        "SPRK",
    ]
    assert listed(page, "ranked listing") == [
        "912797ZZ1 9,872.50 57.90% · +0.23%",
        "912797WW4 5,000.00 29.32% · +1.01%",
        "WI WIDG Widget &lt;Index&gt; Fund 909.50 5.34% · +7.00%",  # of both people
        "GA GADG 900.00 5.28% · +0.00%",
        "SP SPRK 369.00 2.16% · -10.00%",
    ]
    # The switch is for the script to show: without it the screen's width decides.
    assert '<section class="panel views" data-views="positions">' in page
    assert '<button type="button" data-view="map" hidden>Map</button>' in page
    assert lenses(page) == {
        "Kind": "Bills 87.22%, Stocks 7.44%, ETFs 5.34%",
        "Currency": "USD 100.00%",
        "Account": f"IBKR USD ({OWNER}) 97.80%, IBKR USD ({PARTNER}) 2.20%",
        "Person": f"{OWNER} 97.80%, {PARTNER} 2.20%",
    }


def test_the_gains_are_listed_best_first_and_a_cost_not_known_last(
    client: TestClient, odd: TestClient
) -> None:
    assert listed(client.get("/holdings").text, "gains") == [
        "WI WIDG +7.00% +59.50",
        "912797WW4 +1.01% +50.00",
        "912797ZZ1 +0.23% +22.50",
        "GA GADG +0.00% +0.00",
        "SP SPRK -10.00% -41.00",
    ]
    page = odd.get("/holdings", params={"currency": "CHF", "owner": OWNER}).text
    assert listed(page, "gains")[-1] == f"WI WIDG cost not known {NONE}"
    assert tiles(page)[1] == (
        "Gain or loss +10.00 +3.33% On a cost of 300.00; 1 without a known cost"
    )


def test_the_bills_stand_on_a_line_of_days_one_bar_a_day(client: TestClient) -> None:
    page = client.get("/holdings").text
    bars = re.findall(r"<g><title>([^<]*)</title>\s*<rect x=\"([^\"]*)\"", page)
    assert bars == [
        (f"2026-09-28: 912797WW4 ({OWNER}) 5,000.00 EUR today", "-9.0"),  # matured: at the start
        (f"2026-12-24: 912797ZZ1 ({OWNER}) 9,872.50 EUR today", "991.0"),
    ]
    assert '<text class="day" x="1000.0" y="108">24 Dec</text>' in page
    # where the line is too narrow for its words, a phone, the same as a list
    due = re.search(r'<ol class="due">(.*?)</ol>', page, re.DOTALL)
    assert due is not None
    assert [text(item) for item in re.findall(r"<li>(.*?)</li>", due[1])] == [
        "2026-09-28 matured 5,000.00",
        "2026-12-24 in 85 days 9,872.50",
    ]


def test_bills_due_the_same_day_are_one_bar(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)

    def other(cusip: str, maturity: date) -> SecurityTrade:
        return bought(
            cusip, "5000", "-4950", day(16), asset_tab="Bonds", price=Decimal(99), maturity=maturity
        )

    close(
        conn,
        ibkr(
            other("912797AA9", day(30, 11)),
            other("912797BB8", day(30, 11)),
            other("912797CC7", day(2, 10)),
            opening="50000",
        ),
    )
    conn.execute("UPDATE instruments SET maturity = NULL WHERE symbol = '912797CC7'")
    conn.close()
    page = at(path).get("/holdings", params={"currency": "USD", "owner": OWNER}).text
    bars = re.findall(r"<g><title>([^<]*)</title>\s*<rect x=\"[^\"]*\" y=\"([^\"]*)\"", page)
    # the two of the same day together, the tallest; the one without a day has no place
    assert bars == [("2026-11-30: 912797AA9 4,959.33, 912797BB8 4,959.33 USD today", "0.0")]
    assert listed(page, "gains")[-1].startswith("912797CC7")


def test_past_the_colours_there_are_the_smallest_accounts_are_grey_together() -> None:
    def held_in(account: str, value: int) -> portfolio.Position:
        return portfolio.Position(
            OWNER,
            account,
            "VT",
            "",
            "etf",
            "ETFs",
            "USD",
            Decimal(1),
            None,
            None,
            False,
            None,
            Decimal(value),
            Decimal(value),
        )

    values = {f"Broker {n}": 10 * n for n in range(1, 10)}
    held = portfolio.Portfolio(
        "USD", NOW, tuple(held_in(name, value) for name, value in values.items()), ()
    )
    accounts = dict(webapp._lenses(held, [], OWNER))["Account"]
    assert [(piece.name, piece.value, piece.slot) for piece in accounts] == [
        ("Broker 9", Decimal(90), 1),
        ("Broker 8", Decimal(80), 2),
        ("Broker 7", Decimal(70), 3),
        ("Broker 6", Decimal(60), 4),
        ("Broker 5", Decimal(50), 5),
        ("Broker 4", Decimal(40), 6),
        ("3 others", Decimal(60), 8),
    ]
    seven = portfolio.Portfolio("USD", NOW, held.positions[:7], ())
    shown = dict(webapp._lenses(seven, [], OWNER))["Account"]
    assert [piece.slot for piece in shown] == [1, 2, 3, 4, 5, 6, 7]
