from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import merchants
from scrooge.models import Kind
from scrooge.storage import db, store
from scrooge.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER, txn
from tests.web.builders import BUILT, SITE, close

SPAN = (date(2025, 7, 1), date(2026, 8, 31))  # fourteen months, the last one over
SHOP = "KONSUM-1 ZUERICH"


def month(n: int, day: int) -> date:
    """The `day` of the `n`th month of SPAN."""
    return date(2025 + (6 + n) // 12, (6 + n) % 12 + 1, day)


LUKAS = [
    *(txn("-100", date=month(n, 5), source_ref=f"f{n}", counterparty=SHOP) for n in range(14)),
    txn("6000", date=month(13, 25), source_ref="p", kind=Kind.INCOME, category="Salary"),
    txn("-30", date=date(2026, 8, 9), source_ref="1", counterparty="Coop-3 Bern"),
    txn("-45", date=date(2025, 7, 9), source_ref="2", counterparty="Jelmoli"),
]
LUKAS_USD = [
    txn(
        "-10",
        date=date(2026, 3, 2),
        source_ref="1",
        kind=Kind.COMMISSION,
        category="Fees",
        account="IBKR USD",
        currency="USD",
        source_file="ibkr.csv",
    )
]
ANNA = [txn("-60", date=date(2026, 8, 12), source_ref="1", counterparty=SHOP)]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    path: Path = tmp_path_factory.mktemp("transactions-merchants") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    close(conn, OWNER, SPAN, ("migros", LUKAS), ("ibkr", LUKAS_USD))
    close(conn, PARTNER, (date(2026, 8, 1), SPAN[1]), ("migros", ANNA))
    store.set_rate(conn, SPAN[0], "USD", Decimal("0.80"), source="fetched:yahoo")
    conn.close()
    return TestClient(webapp.create_app(path), base_url=SITE)


def asked(client: TestClient, **params: str) -> dict[str, object]:
    response = client.get("/api/transactions/merchants", params={"currency": "CHF", **params})
    assert response.status_code == 200, response.text
    sent: dict[str, object] = response.json()
    return sent


def test_the_merchants_spent_at_the_largest_first(client: TestClient) -> None:
    assert asked(client, owner=OWNER) == {
        "currency": "CHF",
        "since": "2025-09-01",
        "through": "2026-08-31",
        "merchants": [
            {"name": "KONSUM ZUERICH", "spent": "1200.00", "count": 12, "last": "2026-08-05"},
            {"name": "Coop Bern", "spent": "30.00", "count": 1, "last": "2026-08-09"},
            {"name": "IBKR USD", "spent": "8.00", "count": 1, "last": "2026-03-02"},
            # Not spent at in the twelve months: still offered, after the others.
            {"name": "Jelmoli", "spent": "0.00", "count": 0, "last": "2025-07-09"},
        ],
        "others": [],
        "errors": [],
    }


def test_each_name_cuts_the_transactions_to_what_was_spent_there(client: TestClient) -> None:
    names = asked(client)["merchants"]
    assert isinstance(names, list)
    # Both people together: Anna's too.
    assert names[0] == {
        "name": "KONSUM ZUERICH",
        "spent": "1260.00",
        "count": 13,
        "last": "2026-08-12",
    }
    for merchant in names:
        listed = client.get("/api/transactions", params={"merchant": merchant["name"]}).json()
        assert listed["rows"][0]["date"] == merchant["last"]  # the latest first


def test_past_the_first_ones_only_names_are_sent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(merchants, "SHOWN", 1)
    sent = asked(client, owner=OWNER)
    shown = sent["merchants"]
    assert isinstance(shown, list)
    assert [each["name"] for each in shown] == ["KONSUM ZUERICH"]
    assert sent["others"] == ["Coop Bern", "IBKR USD", "Jelmoli"]


def test_a_missing_rate_lists_nobody_and_says_why(client: TestClient) -> None:
    sent = asked(client, owner=OWNER, currency="GBP")
    assert (sent["merchants"], sent["since"]) == ([], None)
    errors = sent["errors"]
    assert isinstance(errors, list)
    assert len(errors) == 1
    assert "GBP" in errors[0]


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"owner": "Nobody"}, "No owner named 'Nobody'."),
        ({"currency": "XYZ"}, "'XYZ' is not one of EUR, CHF, USD, GBP, JPY."),
    ],
)
def test_what_cannot_be_read_is_a_400_with_a_sentence(
    client: TestClient, params: dict[str, str], error: str
) -> None:
    response = client.get("/api/transactions/merchants", params={"currency": "CHF", **params})
    assert (response.status_code, response.json()) == (400, {"errors": [error]})
