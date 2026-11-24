"""`/api/recurring`: the recurring payments of the cash-flow page."""

from pathlib import Path

from starlette.testclient import TestClient

from scrooge.web import app as webapp
from tests.test_recurring import build

SITE = "http://127.0.0.1:8765"


def _client(path: Path, *, rate: bool) -> TestClient:
    return TestClient(webapp.create_app(build(path / "scrooge.db", rate=rate)), base_url=SITE)


def test_recurring_payments_are_amounts_paid_with_their_monthly_cost(tmp_path: Path) -> None:
    sent = _client(tmp_path, rate=True).get("/api/recurring", params={"currency": "CHF"}).json()
    assert (sent["currency"], sent["through"], sent["monthly"], sent["errors"]) == (
        "CHF",
        "2026-10-25",
        "1562.00",
        [],
    )
    assert [p["name"] for p in sent["active"]] == ["Landlord AG", "Insurer", "Streamio"]
    ids = [p.pop("id") for p in sent["active"] + sent["ended"]]
    assert len(set(ids)) == len(ids) == 4
    assert all(isinstance(each, str) and each for each in ids)
    assert sent["active"][2] == {
        "name": "Streamio",
        "owner": "Lukas",
        "account": "Migros",
        "every": "monthly",
        "amount": "12",
        "currency": "CHF",
        "previous": "10",
        "count": 6,
        "first": "2026-05-03",
        "last": "2026-10-03",
        "next": "2026-11-03",
        "monthly": "12.00",
    }
    assert [p["name"] for p in sent["ended"]] == ["Gym"]


def test_a_missing_rate_is_a_sentence(tmp_path: Path) -> None:
    sent = _client(tmp_path, rate=False).get("/api/recurring", params={"currency": "EUR"}).json()
    assert sent["monthly"] is None
    assert sent["errors"][0].endswith(".")


def test_recurring_payments_that_cannot_be_read_are_refused(tmp_path: Path) -> None:
    client = _client(tmp_path, rate=True)
    assert client.get("/api/recurring", params={"owner": "Zoe"}).status_code == 400
    assert client.get("/api/recurring", params={"currency": "XYZ"}).status_code == 400
