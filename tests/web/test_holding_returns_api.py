"""`/api/holdings`: what each holding, and each of its positions, returned."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import categories
from scrooge.models import Kind
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.test_networth import NOW, bought, close, day, ibkr, price, spent

SITE = "http://127.0.0.1:8765"


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    path: Path = tmp_path_factory.mktemp("returns") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    dividend = spent(
        "5",
        day(25),
        account="IBKR USD",
        currency="USD",
        kind=Kind.DIVIDEND,
        category=categories.DIVIDENDS,
        counterparty="WIDG",
    )
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15)), dividend))
    price(conn, "WIDG", day(15), "50")
    price(conn, "WIDG", day(20), "55")
    conn.close()
    return TestClient(webapp.create_app(path), base_url=SITE)


def test_each_holding_and_position_says_what_it_returned(client: TestClient) -> None:
    sent = client.get("/api/holdings", params={"currency": "USD"}).json()
    [holding] = sent["holdings"]["holdings"]
    # 500 in, 550 at the end and 5 paid out: 11%.
    expected = {
        "periods": [
            {"range": "1m", "first": None, "percent": None, "amount": None},
            {"range": "ytd", "first": None, "percent": None, "amount": None},
            {"range": "1y", "first": None, "percent": None, "amount": None},
            {"range": "all", "first": "2026-09-15", "percent": "11.00", "amount": "55.00"},
        ],
        "realised": "0.00",
        "unrealised": "50.00",
        "dividends": "5.00",
        "tax": "0.00",
        "sold": False,
        "too_small": False,
    }
    assert holding["returns"] == expected
    [position] = holding["positions"]
    assert position["returns"] == expected
