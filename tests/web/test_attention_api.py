"""`/api/attention`: the rows to categorise and the recurring payments that changed, besides
what the statements wait for."""

from pathlib import Path

from starlette.testclient import TestClient

from scrooge import categories, importing
from scrooge.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER
from tests.test_attention import build

SITE = "http://127.0.0.1:8765"


def _client(path: Path, *, unmatched: str = categories.UNSORTED, rate: bool = True) -> TestClient:
    setup = importing.Setup(
        [path / "rules.yaml"], path / "instruments.yaml", {}, {}, unmatched=unmatched
    )
    database = build(path / "scrooge.db", rate=rate)
    return TestClient(webapp.create_app(database, imports=setup, currency="CHF"), base_url=SITE)


def test_rows_to_categorise_and_payments_that_changed_are_listed(tmp_path: Path) -> None:
    sent = _client(tmp_path).get("/api/attention").json()
    assert (sent["currency"], sent["unmatched"]) == ("CHF", categories.UNSORTED)
    assert sent["uncategorised"] == [{"owner": PARTNER, "count": 1}, {"owner": OWNER, "count": 2}]
    assert sent["recurring"] == [
        {
            "name": "Streamio",
            "account": "Migros",
            "owner": OWNER,
            "every": "monthly",
            "kind": "up",
            "change": "2.00",
            "since": "2026-09-03",
        },
        {
            "name": "Gymly",
            "account": "Migros",
            "owner": OWNER,
            "every": "monthly",
            "kind": "new",
            "change": "50.00",
            "since": "2026-08-03",
        },
    ]
    assert sent["errors"] == []


def test_one_person_and_another_currency(tmp_path: Path) -> None:
    client = _client(tmp_path)
    hers = client.get("/api/attention", params={"owner": PARTNER, "currency": "EUR"}).json()
    assert (hers["currency"], hers["uncategorised"], hers["recurring"]) == (
        "EUR",
        [{"owner": PARTNER, "count": 1}],
        [],
    )
    his = client.get("/api/attention", params={"owner": OWNER, "currency": "EUR"}).json()
    assert [p["change"] for p in his["recurring"]] == ["2.50", "62.50"]


def test_a_missing_rate_is_a_sentence_and_the_change_is_unknown(tmp_path: Path) -> None:
    sent = _client(tmp_path, rate=False).get("/api/attention", params={"currency": "EUR"}).json()
    assert [p["change"] for p in sent["recurring"]] == [None, None]
    assert len(sent["errors"]) == 1
    assert sent["errors"][0].endswith(".")


def test_without_unmatched_there_is_nothing_to_categorise(tmp_path: Path) -> None:
    sent = _client(tmp_path, unmatched="").get("/api/attention").json()
    assert (sent["unmatched"], sent["uncategorised"]) == (None, [])


def test_nobody_of_that_name_is_not_found(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/attention", params={"owner": "Zoe"})
    assert response.status_code == 404
    assert response.json() == {"errors": ["No owner named 'Zoe'."]}


def test_a_currency_that_cannot_be_read_is_refused(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/attention", params={"currency": "XYZ"})
    assert response.status_code == 400
    assert response.json()["errors"]
