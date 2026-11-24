"""`/api/performance`: what the investments returned over a range, and the money put in."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import categories
from scrooge.models import Kind
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER
from tests.test_networth import NOW, account, bought, close, day, ibkr, price, rate, spent

SITE = "http://127.0.0.1:8765"


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    path: Path = tmp_path_factory.mktemp("performance") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    deposit = spent(
        "5000",
        day(10),
        account="IBKR USD",
        currency="USD",
        kind=Kind.TRANSFER,
        category=categories.OWN_TRANSFER,
    )
    close(
        conn,
        ibkr(deposit, bought("WIDG", "10", "-500", day(15))),
        account("tastytrade", "Tastytrade", "CHF", "2000"),
    )
    close(conn, account("migros", "Migros", "CHF", "400"), owner=PARTNER)
    price(conn, "WIDG", day(15), "50")
    price(conn, "WIDG", day(25), "55")
    rate(conn, "USD", day(1), "0.8")
    conn.close()
    return TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)


def test_the_year_to_date_is_sent_unless_a_range_is_asked(client: TestClient) -> None:
    sent = client.get("/api/performance", params={"currency": "USD"}).json()
    assert sent["errors"] == []
    shown = sent["performance"]
    assert (shown["currency"], shown["range"]) == ("USD", "ytd")
    # The history starts within the year: the range starts with it, from nothing.
    assert (shown["first"], shown["last"]) == ("2026-08-31", "2026-09-30")
    assert shown["totals"] == {
        "start": "0.00",
        "end": "17550.00",
        "net_flows": "17500.00",
        "gain": "50.00",
        "twr": "0.29",
        "mwr": shown["totals"]["mwr"],
        # A month: the money-weighted return over it, not a rate a year.
        "mwr_a_year": False,
        "twr_since": None,
    }
    assert shown["totals"]["mwr"] is not None
    assert shown["series"][0] == {"day": "2026-08-30", "value": "0.00", "invested": "0.00"}
    assert shown["series"][-1] == {
        "day": "2026-09-30",
        "value": "17550.00",
        "invested": "17500.00",
    }
    assert [(row["owner"], row["name"], row["accounts"]) for row in shown["accounts"]] == [
        (OWNER, "IBKR", ["IBKR USD"]),
        (OWNER, "Tastytrade", ["Tastytrade"]),
    ]
    assert shown["accounts"][0]["gain"] == "50.00"
    assert shown["accounts"][1]["gain"] == "0.00"


def test_a_range_ends_with_the_last_day_and_starts_so_many_days_before(
    client: TestClient,
) -> None:
    shown = client.get("/api/performance", params={"currency": "USD", "range": "1M"}).json()
    totals = shown["performance"]["totals"]
    assert shown["performance"]["first"] == "2026-09-01"
    assert (totals["start"], totals["net_flows"], totals["gain"]) == (
        "12500.00",
        "5000.00",
        "50.00",
    )


def test_a_person_without_investment_accounts_has_nothing_to_measure(client: TestClient) -> None:
    shown = client.get("/api/performance", params={"owner": PARTNER}).json()["performance"]
    assert (shown["totals"], shown["series"], shown["accounts"]) == (None, [], [])


def test_accounts_without_a_rate_are_left_out_and_said(client: TestClient) -> None:
    sent = client.get("/api/performance", params={"currency": "CHF", "owner": OWNER}).json()
    assert sent["errors"] == []
    gbp = client.get("/api/performance", params={"currency": "GBP"}).json()
    assert gbp["performance"]["accounts"] == []
    assert gbp["errors"] == [
        "No exchange rate is stored for GBP: run `scrooge prices`.",
        f"Left out for want of a rate: IBKR ({OWNER}), Tastytrade ({OWNER}).",
    ]


def test_a_field_that_cannot_be_read_is_refused(client: TestClient) -> None:
    assert client.get("/api/performance", params={"range": "5y"}).status_code == 400
    assert client.get("/api/performance", params={"owner": "Zoe"}).status_code == 400
    assert client.get("/api/performance", params={"currency": "XYZ"}).status_code == 400
