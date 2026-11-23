"""`/api/networth/months`: net worth at the end of every month, in several currencies."""

from datetime import date
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import networth
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import PARTNER
from tests.test_networth import NOW, rate
from tests.test_overview import household

SITE = "http://127.0.0.1:8765"


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """September for both, and a rate of 15 October: the days run to it."""
    path: Path = tmp_path_factory.mktemp("months") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    household(conn)
    rate(conn, "USD", date(2026, 10, 15), "0.9")
    conn.close()
    return path


def months(
    database: Path, currency: str = "EUR", currencies: tuple[str, ...] = networth.DEFAULT_COLUMNS
) -> TestClient:
    app = webapp.create_app(database, currency=currency, currencies=currencies)
    return TestClient(app, base_url=SITE)


def test_every_month_end_is_sent_newest_first_in_each_currency_of_the_config(
    database: Path,
) -> None:
    sent = months(database, currency="CHF").get("/api/networth/months").json()
    assert sent["errors"] == []
    assert sent["currencies"] == list(networth.DEFAULT_COLUMNS)
    with db.reading(database) as conn:
        ends = networth.month_ends(conn, networth.value(conn, None), networth.DEFAULT_COLUMNS)
    assert [(m["month"], m["day"], m["partial"]) for m in sent["months"]] == [
        ("2026-10", "2026-10-15", True),
        ("2026-09", "2026-09-30", False),
        ("2026-08", "2026-08-31", False),
    ]
    october = ends[0].values["USD"]
    assert october.change is not None
    assert sent["months"][0]["values"]["USD"] == {
        "total": format(october.total, "f"),
        "change": format(october.change, "f"),
        "percent": None if october.percent is None else format(october.percent, "f"),
    }
    assert list(sent["months"][0]["values"]) == list(networth.DEFAULT_COLUMNS)
    assert sent["months"][-1]["values"]["CHF"]["change"] is None


def test_the_currency_shown_is_added_when_the_config_does_not_name_it(database: Path) -> None:
    client = months(database, currencies=("USD", "CHF"))
    sent = client.get("/api/networth/months", params={"currency": "EUR"}).json()
    assert sent["currencies"] == ["USD", "CHF", "EUR"]
    assert list(sent["months"][0]["values"]) == ["USD", "CHF", "EUR"]
    sent = client.get("/api/networth/months", params={"currency": "CHF"}).json()
    assert sent["currencies"] == ["USD", "CHF"]


def test_one_person_is_their_month_ends_alone(database: Path) -> None:
    client = months(database, currency="CHF")
    everyone = client.get("/api/networth/months").json()["months"][0]["values"]["CHF"]
    anna = client.get("/api/networth/months", params={"owner": PARTNER}).json()
    assert anna["errors"] == []
    assert anna["months"][0]["values"]["CHF"]["total"] != everyone["total"]


def test_a_currency_without_rates_is_said_and_no_month_is_sent(database: Path) -> None:
    sent = months(database, currencies=("CHF", "JPY")).get("/api/networth/months").json()
    assert sent["currencies"] == ["CHF", "JPY", "EUR"]
    assert sent["months"] == []
    assert sent["errors"][0].startswith("No exchange rate is stored for JPY")


def test_an_unreadable_currency_is_refused(database: Path) -> None:
    answer = months(database).get("/api/networth/months", params={"currency": "XAU"})
    assert answer.status_code == 400
    assert answer.json()["errors"]


def test_an_empty_database_has_no_month(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    conn.close()
    sent = months(path, currency="CHF").get("/api/networth/months").json()
    assert sent == {"currencies": ["CHF", "USD", "EUR"], "months": [], "errors": []}
