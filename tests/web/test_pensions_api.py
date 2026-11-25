"""`/api/pensions`: the pension accounts as a group, what each is worth and how it changed."""

from datetime import date
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import staging
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER, certificate, certified
from tests.test_networth import NOW
from tests.test_pensions import three_a

SITE = "http://127.0.0.1:8765"


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    path: Path = tmp_path_factory.mktemp("pensions") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    three_a(conn)
    scans = (certificate(date(2025, 12, 31), "40000"), certificate(date(2026, 6, 30), "43000"))
    staging.stage(conn, certified(*scans, owner=PARTNER), NOW)
    staging.commit(conn, owner=PARTNER, now=NOW)
    conn.close()
    return TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)


def test_each_pension_is_sent_with_its_changes_over_a_year_and_the_year_to_date(
    client: TestClient,
) -> None:
    sent = client.get("/api/pensions", params={"currency": "CHF"}).json()
    assert sent["errors"] == []
    shown = sent["pensions"]
    assert (shown["currency"], shown["day"]) == ("CHF", "2026-09-30")
    fund, three = shown["pensions"]
    assert fund == {
        "owner": PARTNER,
        "account": "Pillar 2 AXA",
        "pillar": "Pillar 2",
        "value": "43000.00",
        "cash": None,
        "stated": "2026-06-30",
        "typed": False,
        "changes": {
            "1y": {
                "since": "2025-09-30",
                "start": None,
                "paid_in": None,
                "twr": None,
                "mwr": None,
                "mwr_a_year": False,
                "twr_since": None,
            },
            "ytd": {
                "since": "2025-12-31",
                "start": "40000.00",
                "paid_in": None,
                "twr": None,
                "mwr": None,
                "mwr_a_year": False,
                "twr_since": None,
            },
        },
    }
    assert (three["owner"], three["pillar"], three["value"], three["cash"]) == (
        OWNER,
        "Pillar 3a",
        "2345.00",
        "145.00",
    )
    assert (three["stated"], three["typed"]) == (None, False)
    year = three["changes"]["1y"]
    assert (year["start"], year["paid_in"], year["twr"], year["mwr_a_year"]) == (
        "1000.00",
        "1000.00",
        "22.83",
        True,
    )


def test_one_person_is_shown_alone(client: TestClient) -> None:
    shown = client.get("/api/pensions", params={"owner": OWNER, "currency": "CHF"}).json()
    assert [p["account"] for p in shown["pensions"]["pensions"]] == ["Finpension 3a"]


def test_pensions_without_a_rate_are_left_out_and_said(client: TestClient) -> None:
    sent = client.get("/api/pensions", params={"currency": "GBP"}).json()
    assert sent["pensions"]["pensions"] == []
    assert sent["errors"] == [
        "No exchange rate is stored for GBP: run `scrooge prices`.",
        f"Left out for want of a rate: Finpension 3a ({OWNER}), Pillar 2 AXA ({PARTNER}).",
    ]


def test_a_field_that_cannot_be_read_is_refused(client: TestClient) -> None:
    assert client.get("/api/pensions", params={"owner": "Zoe"}).status_code == 400
    assert client.get("/api/pensions", params={"currency": "XYZ"}).status_code == 400


def test_the_securities_of_the_holdings_view_leave_the_3a_units_to_the_pensions(
    client: TestClient,
) -> None:
    held = client.get("/api/holdings", params={"currency": "CHF"}).json()["holdings"]
    assert (held["day"], held["holdings"], held["total"]) == ("2026-09-30", [], "0")
    assert "pension_cash" not in held
    # The pages of their own still list the units, and say where the cash is counted.
    page = client.get("/holdings").text
    assert "CH0000000001" in page
    assert "145.00 CHF, is counted in the Pillar 3a line" in page
