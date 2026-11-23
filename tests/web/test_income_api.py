"""`/api/income`: what investments paid, by month and by holding."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge.storage import db, logos, store
from scrooge.web import app as webapp
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close
from tests.test_payouts import ANNA, END, ISIN, LUKAS, LUKAS_FUND, LUKAS_USD, START

SITE = "http://127.0.0.1:8765"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(16)


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    path: Path = tmp_path_factory.mktemp("income") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, CLOSED_AT)
    files = (("ibkr", LUKAS_USD), ("finpension", LUKAS_FUND), ("migros", LUKAS))
    close(conn, OWNER, (START, END), END, *files)
    close(conn, PARTNER, (date(2026, 1, 1), END), END, ("migros", ANNA))
    store.instrument_id(conn, "ACME", "Stocks", "USD", name="ACME Corp")
    store.instrument_id(conn, ISIN, "Funds", "CHF", name="ACME Global Fund")
    store.set_logo(conn, "ACME", logos.read(PNG), "acme.png", CLOSED_AT.isoformat())
    store.set_rate(conn, date(2025, 3, 14), "USD", Decimal("0.80"), source="fetched:yahoo")
    store.set_rate(conn, date(2026, 3, 16), "USD", Decimal("0.90"), source="fetched:yahoo")
    conn.close()
    return TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)


def test_the_last_twelve_months_are_sent_unless_a_range_is_asked(client: TestClient) -> None:
    sent = client.get("/api/income", params={"owner": OWNER}).json()
    assert sent["errors"] == []
    shown = sent["income"]
    assert (shown["currency"], shown["first"], shown["last"]) == ("CHF", "2025-09-01", "2026-08-31")
    assert shown["totals"] == {
        "dividends": "230.00",
        "interest": "95.00",
        "tax": "-18.00",
        "net": "307.00",
    }
    assert shown["year_before"] is None
    assert len(shown["months"]) == 12
    assert shown["months"][6] == {
        "month": "2026-03",
        "dividends": "90.00",
        "interest": "0.00",
        "tax": "-13.50",
    }
    first = shown["holdings"][0]
    assert first == {
        "key": "ACME",
        "name": "ACME Corp",
        "symbol": "ACME",
        "account": None,
        "dividends": "180.00",
        "interest": "0.00",
        "tax": "-13.50",
        "count": 3,
        "last": "2026-06-15",
    }
    accounts = [h["account"] for h in shown["holdings"] if h["account"]]
    assert accounts == ["IBKR USD", "Migros"]
    assert list(sent["logos"]) == ["ACME"]
    assert [r["label"] for r in sent["ranges"]] == ["Year to date", "Last 12 months", "2025", "All"]


def test_a_range_is_compared_with_the_year_before(client: TestClient) -> None:
    asked = {"owner": OWNER, "from": "2026-01", "to": "2026-03"}
    shown = client.get("/api/income", params=asked).json()["income"]
    assert shown["year_before"] == {
        "dividends": "80.00",
        "interest": "0.00",
        "tax": "0.00",
        "net": "80.00",
    }


def test_a_missing_rate_is_said_and_nothing_is_shown(client: TestClient) -> None:
    sent = client.get("/api/income", params={"currency": "GBP"}).json()
    assert sent["income"] is None
    assert len(sent["errors"]) == 1


def test_a_field_that_cannot_be_read_is_refused(client: TestClient) -> None:
    assert client.get("/api/income", params={"from": "spring"}).status_code == 400
    assert client.get("/api/income", params={"owner": "Zoe"}).status_code == 400
    assert client.get("/api/income", params={"currency": "XYZ"}).status_code == 400


def test_an_empty_database_has_no_range(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, CLOSED_AT)
    conn.close()
    sent = TestClient(webapp.create_app(path), base_url=SITE).get("/api/income").json()
    assert sent["income"]["first"] is None
    assert sent["income"]["holdings"] == []
    assert sent["ranges"] == []
