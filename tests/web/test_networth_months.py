"""`/api/networth/months`: net worth at the end of every month, in several currencies."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import networth
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import PARTNER
from tests.test_networth import NOW, account, close, deposit_and_pensions, rate
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
        "liquid": format(october.liquid, "f"),
        "frozen": "0.00",  # nothing in a pension or an account named frozen
        "arrived": "0.00",
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


def test_a_currency_without_rates_is_said_and_left_out_the_others_still_sent(
    database: Path,
) -> None:
    sent = months(database, currencies=("CHF", "JPY")).get("/api/networth/months").json()
    assert sent["currencies"] == ["CHF", "JPY", "EUR"]
    [error] = sent["errors"]
    assert error.startswith("No exchange rate is stored for JPY")
    assert len(sent["months"]) == 3
    assert all(list(month["values"]) == ["CHF", "EUR"] for month in sent["months"])


def test_when_no_currency_has_rates_each_is_said_and_no_month_is_sent(database: Path) -> None:
    client = months(database, currency="JPY", currencies=("JPY", "GBP"))
    sent = client.get("/api/networth/months").json()
    assert sent["currencies"] == ["JPY", "GBP"]
    assert sent["months"] == []
    assert sent["errors"] == [
        "No exchange rate is stored for JPY.",
        "No exchange rate is stored for GBP.",
    ]


def test_an_account_held_in_a_currency_without_rates_is_said_once(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    household(conn)
    rate(conn, "USD", date(2026, 10, 15), "0.9")
    close(conn, account("revolut", "ACME JPY", "JPY", "1000"), owner=PARTNER)
    conn.close()
    sent = months(path).get("/api/networth/months").json()
    # Every currency fails alike, for want of a rate for the account's own
    [error] = sent["errors"]
    assert error.startswith("No exchange rate is stored for JPY")
    assert sent["months"] == []


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


# ------------------------------------------------------------------------ liquid and frozen


@pytest.fixture(scope="module")
def deposit(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("liquidity") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    deposit_and_pensions(conn)
    conn.close()
    return path


def test_each_month_end_says_what_is_liquid_and_what_frozen_in_each_currency(
    deposit: Path,
) -> None:
    named = (networth.FrozenAccount("Rent deposit"),)
    app = webapp.create_app(deposit, currency="CHF", currencies=("CHF", "USD"), frozen=named)
    sent = TestClient(app, base_url=SITE).get("/api/networth/months").json()
    assert sent["errors"] == []
    october, september, _ = sent["months"]
    assert october["partial"]
    assert september["values"]["CHF"] == {
        "total": "25289.80",
        "change": "1.80",  # what the bill earned
        "percent": "0.03",
        "liquid": "1789.80",
        "frozen": "23500.00",
        "arrived": "20000.00",  # the second pillar, first counted on 10 September
    }
    # A month's change and what came into view in it add up to the difference of the totals.
    total = Decimal(september["values"]["CHF"]["total"])
    before = Decimal(sent["months"][2]["values"]["CHF"]["total"])
    assert Decimal("1.80") + Decimal("20000.00") == total - before
    assert october["values"]["USD"]["frozen"] == "26111.11"  # 23500 francs at 0.90
    for month in sent["months"]:
        for figure in month["values"].values():
            parts = Decimal(figure["liquid"]) + Decimal(figure["frozen"])
            assert parts == Decimal(figure["total"])


def test_a_frozen_name_no_account_has_is_said_and_freezes_nothing(deposit: Path) -> None:
    named = (networth.FrozenAccount("Rent depot"),)
    app = webapp.create_app(deposit, currency="CHF", currencies=("CHF", "USD"), frozen=named)
    sent = TestClient(app, base_url=SITE).get("/api/networth/months").json()
    assert sent["errors"] == [
        (
            "No account is called Rent depot, which 'frozen' in scrooge.yaml names: write it as "
            "the pages name the account."
        )
    ]
    september = next(m for m in sent["months"] if m["month"] == "2026-09")["values"]
    assert september["CHF"]["frozen"] == "20500.00"
