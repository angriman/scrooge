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


def test_income_by_source_period_by_period(client: TestClient) -> None:
    response = client.get("/analysis", params=IN_2026)
    assert response.status_code == 200
    assert f'<a href="{SITE}/analysis" aria-current="page">Analysis</a>' in response.text
    assert "40,305.00 CHF of income, 99.2% of it from work." in text(response.text)
    periods, sources = tables(response.text)
    assert periods[0] == ["2026-08", "5,000.00", "0.00", "0.00", "5,000.00", "99.5%"]
    assert periods[3] == ["2026-05", "5,000.00", "-300.00", "0.00", "4,700.00", "99.5%"]
    assert periods[5] == ["2026-03", "5,000.00", "90.00", "0.00", "5,090.00", "99.0%"]
    assert periods[7] == ["2026-01", "5,000.00", "0.00", "0.00", "5,000.00", "100.0%"]
    assert sources == [
        ["Stipendio", "40,000.00", "99.2%", "8"],
        ["Trading", "200.00", "0.5%", "2"],
        ["Dividendi", "90.00", "0.2%", "1"],
        ["Cashback", "15.00", "0.0%", "1"],
    ]
    assert "Statements up to 2026-08-31, for both of you." in text(response.text)


def test_before_twelve_months_of_income_there_is_no_share(client: TestClient) -> None:
    periods, _ = tables(client.get("/analysis", params={"currency": "CHF", "to": "2025-12"}).text)
    assert [row[-1] for row in periods[:2]] == ["100.0%", "\N{EN DASH}"]  # December, then November


def test_the_figures_follow_the_currency_chosen(client: TestClient) -> None:
    page = client.get("/analysis", params={"from": "2026-08", "currency": "EUR"}).text
    assert "5,263.16 EUR of income" in text(page)
    assert "<option selected>EUR</option>" in page.partition("<main>")[0]


def test_the_chart_stacks_each_period_and_hangs_a_loss_below_zero(client: TestClient) -> None:
    page = client.get("/analysis", params=IN_2026).text
    rects = re.findall(
        r'<rect class="slot-(\d)" x="[\d.]+" y="([\d.]+)" width="[\d.]+" height="([\d.]+)"/>', page
    )
    assert len(rects) == 12  # a salary each month, three months of investments, one cashback
    # 5500 at the top (February), 300 below zero (May): zero is drawn at 5500 / 5800.
    assert '<line class="axis" x1="0" x2="600" y1="189.7" y2="189.7"/>' in page
    assert ("2", "189.7", "10.3") in rects
    [line] = re.findall(r'<polyline class="share" points="([^"]+)"/>', page)
    assert len(line.split()) == 8
    assert (
        "<title>2026-05: 4,700.00 CHF, Lavoro 5,000.00, Investimenti -300.00; "
        "99.5% from work over 12 months</title>" in page
    )
    assert "style=" not in page  # the page's own policy would refuse it


def test_sources_link_to_their_transactions(client: TestClient) -> None:
    page = client.get("/analysis", params={**IN_2026, "owner": OWNER}).text.replace("&amp;", "&")
    assert (
        f'<a href="{SITE}/transactions?from=2026-01-01&to=&category=Trading&owner={OWNER}">'
        "Trading</a>" in page
    )
    assert "for both of you" not in page


def test_what_cannot_be_shown_is_said(client: TestClient) -> None:
    gbp = client.get("/analysis", params={"currency": "GBP"})
    assert gbp.status_code == 200
    assert "No exchange rate is stored for GBP: run `finanze prices`." in text(gbp.text)
    assert "Income by source" not in gbp.text
    later = client.get("/analysis", params={"from": "2030", "by": "decade", "currency": "CHF"})
    assert "&#39;decade&#39; is not week, month, quarter or year." in later.text
    assert "Nothing is committed in this range." in later.text
    assert client.get("/analysis", params={"owner": "Zoe"}).status_code == 404


def test_the_share_stops_where_the_statements_do(client: TestClient) -> None:
    page = client.get("/analysis", params={**IN_2026, "to": "2026-12"}).text
    [line] = re.findall(r'<polyline class="share" points="([^"]+)"/>', page)
    assert len(line.split()) == 8  # September to December: no day of them is committed


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


def test_where_the_money_goes(spender: TestClient) -> None:
    page = spender.get("/analysis", params={**IN_2026, "owner": OWNER}).text
    assert "14,654.50 CHF spent once Anna paid back 900.00, 1,831.81 a month." in text(page)
    assert "what Anna paid you back lowers the total" in text(page)
    _, _, categories, merchants = tables(page)
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


def test_what_was_paid_back_hangs_below_zero(spender: TestClient) -> None:
    july = {"from": "2026-07", "to": "2026-07", "currency": "CHF", "owner": OWNER}
    page = spender.get("/analysis", params=july).text
    spending = page.partition("Where the money goes")[2]
    legend = re.findall(r'<span class="key slot-(\d)"></span>([^<]+)</span>', spending)
    assert legend == [("1", "Casa"), ("2", "Regali"), ("3", "Alimentari"), ("0", "Paid back")]
    [zero] = re.findall(r'<line class="axis" x1="0" x2="600" y1="([\d.]+)"', spending)
    assert re.search(rf'<rect class="slot-0" x="[\d.]+" y="{zero}"', spending)
    assert '<polyline class="share"' not in spending


def test_both_people_spend_without_what_they_pay_each_other(spender: TestClient) -> None:
    page = spender.get("/analysis", params={"from": "2026-07", "currency": "CHF"}).text
    assert "what the two of you pay each other is left out" in text(page)
    assert "paid back" not in text(page).partition("Where the money goes")[2]


def test_many_merchants_are_added_up_past_the_largest(
    spender: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spending, "MERCHANTS", 2)
    page = spender.get("/analysis", params={**IN_2026, "owner": OWNER}).text
    assert "And 4 more, 334.50 CHF together." in text(page)
