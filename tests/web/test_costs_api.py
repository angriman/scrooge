"""`/api/costs`: what investing cost, year by year and account by account."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import categories
from scrooge.storage import db, store
from scrooge.web import app as webapp
from scrooge.web import wire
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, close
from tests.test_costs import (
    ANNA_FUND,
    ANNA_TASTY,
    ANNA_USD,
    END,
    LUKAS,
    LUKAS_FUND,
    LUKAS_GBP,
    LUKAS_USD,
    START,
)

SITE = "http://127.0.0.1:8765"
COSTS_TS = Path(__file__).parents[2] / "frontend" / "src" / "lib" / "investments" / "costs.ts"


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    path: Path = tmp_path_factory.mktemp("costs") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, CLOSED_AT)
    files = (("ibkr", LUKAS_USD), ("ibkr", LUKAS_GBP), ("finpension", LUKAS_FUND))
    close(conn, OWNER, (START, END), END, *files, ("migros", LUKAS))
    close(
        conn,
        PARTNER,
        (date(2026, 1, 1), END),
        END,
        ("ibkr", ANNA_USD),
        ("tastytrade", ANNA_TASTY),
        ("finpension", ANNA_FUND),
    )
    store.set_rate(conn, date(2025, 3, 14), "USD", Decimal("0.80"), source="fetched:yahoo")
    store.set_rate(conn, date(2026, 3, 16), "USD", Decimal("0.90"), source="fetched:yahoo")
    conn.close()
    return TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)


def test_each_year_and_account_is_sent_and_a_currency_without_a_rate_said(
    client: TestClient,
) -> None:
    sent = client.get("/api/costs", params={"owner": OWNER}).json()
    assert sent["errors"] == ["No exchange rate is stored for GBP: run `scrooge prices`."]
    shown = sent["costs"]
    assert (shown["currency"], shown["through"]) == ("CHF", "2026-08-31")
    assert shown["years"] == [
        {
            "year": 2025,
            "commissions": "1.60",
            "tax": "0.00",
            "total": "1.60",
            "paid": "80.00",
            "tax_share": "0",
            "last": "2025-12-31",
            "whole": True,
        },
        {
            "year": 2026,
            "commissions": "11.80",
            "tax": "15.25",
            "total": "27.05",
            "paid": "145.00",
            "tax_share": wire.number(Decimal("15.25") / 145 * 100),
            "last": "2026-08-31",
            "whole": False,
        },
    ]
    assert shown["totals"] == {
        "commissions": "13.40",
        "tax": "15.25",
        "total": "28.65",
        "paid": "225.00",
        "tax_share": wire.number(Decimal("15.25") / 225 * 100),
    }
    assert shown["accounts"][0] == {
        "owner": OWNER,
        "account": "IBKR USD",
        "name": "IBKR USD",
        "broker": True,
        "commissions": "3.40",
        "tax": "13.50",
        "total": "16.90",
    }


def test_for_everyone_two_accounts_of_a_name_are_named_after_their_owners(
    client: TestClient,
) -> None:
    shown = client.get("/api/costs").json()["costs"]
    names = [(each["name"], each["owner"]) for each in shown["accounts"]]
    assert (f"IBKR USD ({PARTNER})", PARTNER) in names
    # A bank's account is no broker's: its link opens its tax withheld alone.
    assert [each["broker"] for each in shown["accounts"] if each["account"] == "Migros"] == [False]
    hers = client.get("/api/costs", params={"owner": PARTNER}).json()["costs"]
    assert hers["totals"]["tax_share"] is None


def test_transactions_can_be_cut_to_broker_accounts(client: TestClient) -> None:
    params = {"owner": OWNER, "category": categories.FEES, "accounts": "broker"}
    sent = client.get("/api/transactions", params=params).json()
    assert sent["errors"] == []
    assert {row["account"] for row in sent["rows"]} == {"IBKR USD", "IBKR GBP", "Finpension"}
    refused = client.get("/api/transactions", params={"accounts": "savings"})
    assert refused.status_code == 400
    assert refused.json()["errors"] == ["'savings' is not broker, bank or card accounts."]


def test_the_panel_links_to_the_categories_by_their_keys() -> None:
    """The keys the costs panel opens the transactions with are the services' own: a category
    renamed here and not there would open nothing."""
    written = COSTS_TS.read_text()
    assert f"COMMISSIONS = '{categories.FEES}'" in written
    assert f"TAX = '{categories.WITHHOLDING_TAX}'" in written


def test_a_field_that_cannot_be_read_is_refused(client: TestClient) -> None:
    for params in ({"owner": "Nobody"}, {"currency": "XX"}):
        response = client.get("/api/costs", params=params)
        assert response.status_code == 400
        assert response.json()["errors"]
