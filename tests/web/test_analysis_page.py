import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze import spending
from finanze.models import Kind
from finanze.storage import db, store
from finanze.web import app as webapp
from tests import test_spending as spent
from tests.storage.helpers import OWNER, PARTNER, txn
from tests.test_income import salaries
from tests.web.builders import BUILT, close
from tests.web.test_app import text
from tests.web.test_cashflow_page import tables

SITE = "http://127.0.0.1:8765"
TRADE = {"kind": Kind.TRADING, "category": "Trading"}
USD = {"account": "IBKR USD", "currency": "USD", "source_file": "ibkr.csv"}
LUKAS = [
    *salaries("5000", date(2025, 1, 1), 20),
    txn("500", date=date(2026, 2, 10), source_ref="1", **TRADE),
    txn("-300", date=date(2026, 5, 10), source_ref="2", **TRADE),  # a month that lost
    txn("15", date=date(2026, 7, 5), source_ref="3", kind=Kind.INCOME, category="Cashback"),
]
LUKAS_USD = [
    txn(
        "100",
        date=date(2026, 3, 16),
        source_ref="1",
        kind=Kind.DIVIDEND,
        category="Dividendi",
        **USD,
    )
]
IN_2026 = {"from": "2026-01", "currency": "CHF"}


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """Lukas, January 2025 to August 2026: a salary every month, trading, a dividend."""
    path: Path = tmp_path_factory.mktemp("analysis") / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    close(
        conn, OWNER, (date(2025, 1, 1), date(2026, 8, 31)), ("migros", LUKAS), ("ibkr", LUKAS_USD)
    )
    store.set_rate(conn, date(2025, 1, 1), "EUR", Decimal("0.95"), source="fetched:yahoo")
    store.set_rate(conn, date(2025, 1, 1), "USD", Decimal("0.90"), source="fetched:yahoo")
    conn.close()
    return TestClient(webapp.create_app(path), base_url=SITE)


def test_the_last_twelve_months_unless_asked(client: TestClient) -> None:
    response = client.get("/analysis", params={"currency": "CHF"})
    assert response.status_code == 200
    page = response.text
    assert f'<a href="{SITE}/analysis" aria-current="page">Analysis</a>' in page
    assert "From 2025-09-01 to 2026-08-31, in CHF" in text(page)
    ranges = re.findall(
        r'<a href="([^"]+)"( aria-current="true")?>([^<]+)</a>',
        page.partition('aria-label="Range"')[2].partition("</nav>")[0],
    )
    assert ranges == [
        (f"{SITE}/analysis?currency=CHF", ' aria-current="true"', "Last 12 months"),
        (f"{SITE}/analysis?from=2026&amp;to=2026&amp;currency=CHF", "", "2026"),
        (f"{SITE}/analysis?from=2025&amp;to=2025&amp;currency=CHF", "", "2025"),
        (f"{SITE}/analysis?from=2025&amp;currency=CHF", "", "All"),
    ]


def test_income_by_source(client: TestClient) -> None:
    page = client.get("/analysis", params=IN_2026).text
    assert "40,305.00 CHF of income." in text(page)
    ring, periods, sources = tables(page)
    assert ring == [
        ["Lavoro", "40,000.00", "99.2%"],
        ["Investimenti", "290.00", "0.7%"],
        ["Altro", "15.00", "0.0%"],
    ]
    # Nothing is spent: all income is saved.
    assert periods[0] == ["2026-08", "5,000.00", "0.00", "0.00", "5,000.00", "99.5%", "100.0%"]
    assert periods[3] == ["2026-05", "5,000.00", "-300.00", "0.00", "4,700.00", "99.5%", "100.0%"]
    assert periods[5] == ["2026-03", "5,000.00", "90.00", "0.00", "5,090.00", "99.0%", "100.0%"]
    assert periods[7] == ["2026-01", "5,000.00", "0.00", "0.00", "5,000.00", "100.0%", "100.0%"]
    assert sources == [
        ["Stipendio", "40,000.00", "99.2%", "8"],
        ["Trading", "200.00", "0.5%", "2"],
        ["Dividendi", "90.00", "0.2%", "1"],
        ["Cashback", "15.00", "0.0%", "1"],
    ]
    assert "Statements up to 2026-08-31, for both of you." in text(page)


def test_a_loss_is_not_in_the_ring(client: TestClient) -> None:
    page = client.get(
        "/analysis", params={"from": "2026-05", "to": "2026-05", "currency": "CHF"}
    ).text
    assert "Not in the ring: Investimenti -300.00 CHF." in text(page)


def test_before_twelve_months_of_income_there_is_no_share(client: TestClient) -> None:
    _, periods, _ = tables(
        client.get("/analysis", params={"currency": "CHF", "to": "2025-12"}).text
    )
    assert [row[5] for row in periods[:2]] == ["100.0%", "\N{EN DASH}"]  # December, November


def test_the_figures_follow_the_currency_chosen(client: TestClient) -> None:
    page = client.get("/analysis", params={"from": "2026-08", "currency": "EUR"}).text
    assert "5,263.16 EUR of income" in text(page)
    assert "<option selected>EUR</option>" in page.partition("<main>")[0]


def test_the_chart_stacks_each_period_and_hangs_a_loss_below_zero(client: TestClient) -> None:
    page = client.get("/analysis", params=IN_2026).text
    rects = re.findall(
        r'<rect class="slot-(\d)" x="[\d.]+" y="([\d.]+)" width="[\d.]+" height="([\d.]+)"/>',
        page,
    )
    assert len(rects) == 12  # a salary each month, three months of investments, one cashback
    # 5500 at the top (February), 300 below zero (May): zero is drawn at 5500 / 5800.
    assert '<line class="axis" x1="0" x2="940" y1="208.6" y2="208.6"/>' in page
    assert ("2", "208.6", "11.4") in rects
    [line] = re.findall(r'<polyline class="share" points="([^"]+)"/>', page)
    assert len(line.split()) == 8
    assert (
        "<title>2026-05: 4,700.00 CHF, Lavoro 5,000.00, Investimenti -300.00; "
        "99.5% from work over 12 months</title>" in page
    )
    assert "style=" not in page  # the page's own policy would refuse it


def test_the_share_stops_where_the_statements_do(client: TestClient) -> None:
    page = client.get("/analysis", params={**IN_2026, "to": "2026-12"}).text
    [line] = re.findall(r'<polyline class="share" points="([^"]+)"/>', page)
    assert len(line.split()) == 8  # September to December: no day of them is committed


def test_sources_link_to_their_transactions(client: TestClient) -> None:
    page = client.get("/analysis", params={**IN_2026, "owner": OWNER}).text.replace("&amp;", "&")
    link = f"{SITE}/transactions?from=2026-01-01&to=2026-08-31&category=Trading&owner={OWNER}"
    assert f'<a href="{link}">Trading</a>' in page
    assert "for both of you" not in page


def test_what_cannot_be_shown_is_said(client: TestClient) -> None:
    gbp = client.get("/analysis", params={"currency": "GBP"})
    assert gbp.status_code == 200
    assert "No exchange rate is stored for GBP: run `finanze prices`." in text(gbp.text)
    assert "Where income comes from" not in gbp.text
    later = client.get("/analysis", params={"from": "2030", "by": "decade", "currency": "CHF"})
    assert "&#39;decade&#39; is not week, month, quarter or year." in later.text
    assert "Nothing is committed in this range." in later.text
    assert client.get("/analysis", params={"owner": "Zoe"}).status_code == 404


@pytest.fixture(scope="module")
def spender(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """The rows of the spending tests, and a bill that gives Casa a second leaf."""
    path: Path = tmp_path_factory.mktemp("spending") / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    bill = txn("-90", date=date(2026, 7, 3), source_ref="bill", category="Utenze")
    lukas = (("migros", [*spent.LUKAS, bill]), ("ibkr", spent.LUKAS_USD))
    close(conn, OWNER, (spent.START, spent.END), *lukas)
    close(conn, PARTNER, (date(2026, 7, 1), spent.END), ("migros", spent.ANNA))
    store.set_rate(conn, spent.START, "USD", Decimal("0.90"), source="fetched:yahoo")
    conn.close()
    return TestClient(webapp.create_app(path), base_url=SITE)


def test_the_headline_figures_come_first(spender: TestClient) -> None:
    page = spender.get("/analysis", params={**IN_2026, "owner": OWNER}).text
    tiles = text(page.partition('<div class="tiles">')[2].partition("</div>")[0])
    assert tiles == (
        "Income 40,000.00 +0% on a year before "
        "Spent 14,654.50 -4% on a year before "
        "Saved 25,345.50 63.4% 61.8% a year before "
        "From work 100.0% 100.0% a year before"
    )
    order = [
        page.index(heading)
        for heading in ('class="tiles"', "Where the money goes", "Where income comes from")
    ]
    assert order == sorted(order)


def test_where_the_money_goes(spender: TestClient) -> None:
    page = spender.get("/analysis", params={**IN_2026, "owner": OWNER}).text
    assert "14,654.50 CHF spent once Anna paid back 900.00, 1,831.81 a month." in text(page)
    assert "Not in the ring: Paid back -900.00 CHF." in text(page)
    ring, categories, merchants, *_ = tables(page)
    assert ring == [
        ["Casa", "14,490.00", "93.2%", "+1%"],
        ["Alimentari", "820.00", "5.3%", "+2%"],
        ["Regali", "200.00", "1.3%", "\N{EN DASH}"],
        ["Ristoranti e bar", "40.00", "0.3%", "\N{EN DASH}"],
        ["Banca e broker", "4.50", "0.0%", "\N{EN DASH}"],
    ]
    # The tables are folded under the charts.
    assert page.index("<summary>Every category") < page.index('<table class="cards">')
    assert "what Anna paid you back lowers the total" in text(page)
    assert categories[:3] == [
        ["Casa", "14,490.00", "93.2%", "1,811.25", "1,800.00", "+1%", "14,400.00", "+1%"],
        ["Affitto", "14,400.00", ""],
        ["Utenze", "90.00", ""],
    ]
    # Nothing was spent on it before: there is no change to give.
    none = "\N{EN DASH}"
    assert ["Regali", "200.00", "1.3%", "25.00", "0.00", none, "0.00", none] in categories
    assert merchants[0] == [
        "Hans Muster",
        "Casa",
        "14,400.00",
        "8",
        "1,800.00",
        "2026-01-01",
        "2026-08-01",
    ]
    assert ["Migros", "Casa", "90.00", "1", "90.00", "2026-07-03", "2026-07-03"] in merchants


def test_a_change_of_a_tenth_is_coloured_by_whether_it_is_good_news(spender: TestClient) -> None:
    page = spender.get(
        "/analysis", params={"from": "2026-07", "to": "2026-07", "owner": OWNER, "currency": "CHF"}
    ).text
    # July's food: 120 against the usual 100, more spent; less spent than the July before.
    assert '<span class="worse">+20%</span>' in page
    assert '<span class="better">-31%</span> on a year before' in page
    # A change under a tenth is not coloured.
    assert "<span>+0%</span> on a year before" in page


def test_what_was_paid_back_hangs_below_zero(spender: TestClient) -> None:
    july = {"from": "2026-07", "to": "2026-07", "currency": "CHF", "owner": OWNER}
    page = spender.get("/analysis", params=july).text
    spending_part = page.partition("Where the money goes")[2].partition("Where income comes from")[
        0
    ]
    legend = re.findall(r'<span class="key slot-(\d)"></span>([^<]+)</span>', spending_part)
    assert legend == [("1", "Casa"), ("2", "Regali"), ("3", "Alimentari"), ("0", "Paid back")]
    [zero] = re.findall(r'<line class="axis" x1="0" x2="940" y1="([\d.]+)"', spending_part)
    assert re.search(rf'<rect class="slot-0" x="[\d.]+" y="{zero}"', spending_part)
    assert '<polyline class="share"' not in spending_part


def test_both_people_spend_without_what_they_pay_each_other(spender: TestClient) -> None:
    page = spender.get("/analysis", params={"from": "2026-07", "currency": "CHF"}).text
    assert "what the two of you pay each other is left out" in text(page)
    assert "paid back" not in text(page).partition("Where the money goes")[2]


def test_many_merchants_are_added_up_past_the_largest(
    spender: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spending, "MERCHANTS", 2)
    page = spender.get("/analysis", params={**IN_2026, "owner": OWNER}).text
    assert "<summary>The 2 largest merchants</summary>" in page
    assert "And 4 more, 334.50 CHF together." in text(page)


def test_the_ring_puts_the_smaller_categories_together(
    spender: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spending, "SHOWN", 1)
    page = spender.get("/analysis", params={**IN_2026, "owner": OWNER}).text
    ring, *_ = tables(page)
    assert ring == [
        ["Casa", "14,490.00", "93.2%", "+1%"],
        ["Others", "1,064.50", "6.8%", "\N{EN DASH}"],
    ]


def test_on_a_phone_every_other_period_is_named(client: TestClient) -> None:
    page = client.get("/analysis", params=IN_2026).text
    income = page.partition("Where income comes from")[2]
    named = re.findall(
        r'<text class="period( minor)?" x="[\d.]+" y="[\d.]+">([^<]+)</text>', income
    )
    assert named[-2:] == [(" minor", "2026-07"), ("", "2026-08")]
    assert len(named) == 8
