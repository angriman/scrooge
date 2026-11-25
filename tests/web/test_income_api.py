"""`/api/income`: what investments paid, by month and by holding."""

import sqlite3
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import query
from scrooge.storage import db, logos, store
from scrooge.web import app as webapp
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close
from tests.test_payouts import (
    ANNA,
    DIVIDEND,
    END,
    ISIN,
    LUKAS,
    LUKAS_FUND,
    LUKAS_USD,
    START,
    paid,
)

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
    assert shown["trailing"] == shown["totals"]
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
        "owner": None,
        "dividends": "180.00",
        "interest": "0.00",
        "tax": "-13.50",
        "count": 3,
        "last": "2026-06-15",
    }
    accounts = [(h["name"], h["owner"]) for h in shown["holdings"] if h["account"]]
    assert accounts == [("IBKR USD", None), ("Migros", None)]
    assert list(sent["logos"]) == ["ACME"]
    assert [r["label"] for r in sent["ranges"]] == ["Year to date", "Last 12 months", "2025", "All"]
    assert [r["key"] for r in sent["ranges"]] == ["ytd", "12m", "2025", "all"]


def test_a_range_is_compared_with_the_year_before_and_the_last_twelve_months_sent(
    client: TestClient,
) -> None:
    asked = {"owner": OWNER, "from": "2026-01", "to": "2026-03"}
    shown = client.get("/api/income", params=asked).json()["income"]
    assert shown["year_before"] == {
        "dividends": "80.00",
        "interest": "0.00",
        "tax": "0.00",
        "net": "80.00",
    }
    assert shown["totals"]["net"] == "76.50"
    # Whatever the range, what the twelve months up to the last day committed paid.
    assert shown["trailing"] == {
        "dividends": "230.00",
        "interest": "95.00",
        "tax": "-18.00",
        "net": "307.00",
    }


def test_for_everyone_each_person_s_account_of_a_shared_name_is_a_line(
    client: TestClient,
) -> None:
    shown = client.get("/api/income", params={"from": "2026-01"}).json()["income"]
    accounts = [(h["name"], h["account"], h["owner"]) for h in shown["holdings"] if h["account"]]
    assert accounts == [
        ("IBKR USD", "IBKR USD", None),
        (f"Migros ({OWNER})", "Migros", OWNER),
        (f"Migros ({PARTNER})", "Migros", PARTNER),
    ]


def test_rows_without_a_rate_are_left_out_and_said(client: TestClient) -> None:
    sent = client.get("/api/income", params={"owner": OWNER, "currency": "GBP"}).json()
    # Nothing is in pounds, and no rate is stored for them: what is shown is empty, not gone.
    assert sent["income"]["totals"]["net"] == "0.00"
    assert sent["income"]["through"] == "2026-08-31"
    assert sent["errors"] == ["No exchange rate is stored for GBP: run `scrooge prices`."]


def test_a_row_in_a_currency_without_a_rate_leaves_the_others_shown(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, CLOSED_AT)
    pounds = {"account": "Revolut GBP", "currency": "GBP", "source_file": "revolut.csv"}
    files = (("migros", LUKAS), ("revolut", [paid("50", END, "1", "BRIT", **DIVIDEND, **pounds)]))
    close(conn, OWNER, (START, END), END, *files)
    conn.close()
    sent = TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE).get("/api/income")
    shown = sent.json()
    assert shown["income"]["totals"]["interest"] == "5.00"
    assert [h["key"] for h in shown["income"]["holdings"]] == ["Migros"]
    assert shown["errors"] == ["No exchange rate is stored for GBP: run `scrooge prices`."]


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


def test_no_range_is_offered_without_a_first_day(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A last day committed with no first one: there is no year to start the ranges from.
    people = query.people

    def without_first(conn: sqlite3.Connection) -> tuple[query.Person, ...]:
        return tuple(replace(person, first=None) for person in people(conn))

    monkeypatch.setattr(query, "people", without_first)
    sent = client.get("/api/income", params={"owner": OWNER}).json()
    assert sent["income"]["through"] == "2026-08-31"
    assert sent["ranges"] == []
