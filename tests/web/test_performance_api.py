"""`/api/performance`: what the investments returned over a range, and the money put in."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import categories, importing
from scrooge.models import Kind
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.fakes import FakeSource
from tests.storage.helpers import OWNER, PARTNER
from tests.test_networth import NOW, account, bought, close, day, ibkr, price, rate, spent
from tests.web.test_login import STORED

SITE = "http://127.0.0.1:8765"


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    path: Path = tmp_path_factory.mktemp("performance") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    deposit = spent(
        "5000",
        day(10),
        account="IBKR USD",
        currency="USD",
        kind=Kind.TRANSFER,
        category=categories.OWN_TRANSFER,
    )
    close(
        conn,
        ibkr(deposit, bought("WIDG", "10", "-500", day(15))),
        account("tastytrade", "Tastytrade", "CHF", "2000"),
    )
    close(conn, account("migros", "Migros", "CHF", "400"), owner=PARTNER)
    price(conn, "WIDG", day(15), "50")
    price(conn, "WIDG", day(25), "55")
    rate(conn, "USD", day(1), "0.8")
    conn.close()
    return TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)


def test_the_year_to_date_is_sent_unless_a_range_is_asked(client: TestClient) -> None:
    sent = client.get("/api/performance", params={"currency": "USD"}).json()
    assert sent["errors"] == []
    shown = sent["performance"]
    assert (shown["currency"], shown["range"]) == ("USD", "ytd")
    # The history starts within the year: the range starts with it, from nothing.
    assert (shown["first"], shown["last"]) == ("2026-08-31", "2026-09-30")
    assert shown["totals"] == {
        "start": "0.00",
        "end": "17550.00",
        "net_flows": "17500.00",
        "gain": "50.00",
        "twr": "0.29",
        "mwr": shown["totals"]["mwr"],
        # A month: the money-weighted return over it, not a rate a year.
        "mwr_a_year": False,
        "twr_since": None,
    }
    assert shown["totals"]["mwr"] is not None
    assert shown["series"][0] == {
        "day": "2026-08-30",
        "value": "0.00",
        "invested": "0.00",
        "benchmarks": [],
    }
    assert shown["series"][-1] == {
        "day": "2026-09-30",
        "value": "17550.00",
        "invested": "17500.00",
        "benchmarks": [],
    }
    assert shown["benchmarks"] == []
    assert shown["missing_benchmarks"] == []
    assert [(row["owner"], row["name"], row["accounts"]) for row in shown["accounts"]] == [
        (OWNER, "IBKR", ["IBKR USD"]),
        (OWNER, "Tastytrade", ["Tastytrade"]),
    ]
    assert shown["accounts"][0]["gain"] == "50.00"
    assert shown["accounts"][1]["gain"] == "0.00"


def test_a_range_ends_with_the_last_day_and_starts_so_many_days_before(
    client: TestClient,
) -> None:
    shown = client.get("/api/performance", params={"currency": "USD", "range": "1M"}).json()
    totals = shown["performance"]["totals"]
    assert shown["performance"]["first"] == "2026-09-01"
    assert (totals["start"], totals["net_flows"], totals["gain"]) == (
        "12500.00",
        "5000.00",
        "50.00",
    )


def test_a_person_without_investment_accounts_has_nothing_to_measure(client: TestClient) -> None:
    shown = client.get("/api/performance", params={"owner": PARTNER}).json()["performance"]
    assert (shown["totals"], shown["series"], shown["accounts"], shown["missing_benchmarks"]) == (
        None,
        [],
        [],
        [],
    )


def test_accounts_without_a_rate_are_left_out_and_said(client: TestClient) -> None:
    sent = client.get("/api/performance", params={"currency": "CHF", "owner": OWNER}).json()
    assert sent["errors"] == []
    gbp = client.get("/api/performance", params={"currency": "GBP"}).json()
    assert gbp["performance"]["accounts"] == []
    assert gbp["errors"] == [
        "No exchange rate is stored for GBP.",
        f"Left out for want of a rate: IBKR ({OWNER}), Tastytrade ({OWNER}).",
    ]


def test_a_field_that_cannot_be_read_is_refused(client: TestClient) -> None:
    assert client.get("/api/performance", params={"range": "5y"}).status_code == 400
    assert client.get("/api/performance", params={"owner": "Zoe"}).status_code == 400
    assert client.get("/api/performance", params={"currency": "XYZ"}).status_code == 400


def test_the_benchmarks_of_the_config_are_set_beside_and_one_without_closes_said(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    close(conn, account("ibkr", "IBKR USD", "USD", "1000"))
    conn.execute(
        "INSERT INTO instruments (symbol, type, currency, price_symbol) "
        "VALUES ('VT', 'etf', 'USD', 'VT')"
    )
    price(conn, "VT", day(30, 8), "100")
    price(conn, "VT", day(30), "110")
    conn.commit()
    conn.close()
    app = webapp.create_app(path, benchmarks=("VT", "VOO"))
    sent = TestClient(app, base_url=SITE).get("/api/performance", params={"currency": "USD"})
    shown = sent.json()
    assert shown["performance"]["benchmarks"] == [{"symbol": "VT", "return": "10.00"}]
    assert shown["performance"]["missing_benchmarks"] == ["VOO"]
    assert shown["performance"]["series"][-1]["benchmarks"] == ["1100.00"]
    assert shown["errors"] == []


def test_missing_benchmarks_are_listed_on_performance_without_error_banners(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    close(conn, account("ibkr", "IBKR USD", "USD", "1000"))
    conn.close()
    app = webapp.create_app(path, benchmarks=("VT", "VOO"))
    sent = TestClient(app, base_url=SITE).get("/api/performance", params={"currency": "USD"})
    shown = sent.json()
    assert shown["errors"] == []
    assert shown["performance"]["benchmarks"] == []
    assert shown["performance"]["missing_benchmarks"] == ["VT", "VOO"]


def _fx_closes(on: date) -> dict[str, list[tuple[date, str]]]:
    return {
        "EURCHF=X": [(on, "0.95")],
        "USDCHF=X": [(on, "0.80")],
        "GBPCHF=X": [(on, "1.10")],
        "JPYCHF=X": [(on, "0.006")],
    }


def test_posting_prices_fetches_missing_benchmarks_and_honors_price_symbol(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15))))
    conn.close()
    instruments = tmp_path / "instruments.yaml"
    instruments.write_text("instruments:\n  WIDG:\n    price_symbol: WIDG.US\n", encoding="utf-8")
    setup = importing.Setup([], instruments, {OWNER: []}, {}, "")
    source = FakeSource(
        {
            "WIDG.US": [(day(15), "50"), (day(30), "55")],
            "VT": [(day(30, 8), "100"), (day(30), "110")],
            "VOO": [(day(30, 8), "200"), (day(30), "210")],
            **_fx_closes(day(30, 8)),
        }
    )
    app = webapp.create_app(
        path,
        clock=lambda: datetime(2026, 10, 1, 12, tzinfo=UTC),
        imports=setup,
        price_source=source,
        benchmarks=("VT", "VOO"),
    )
    web = TestClient(app, base_url=SITE)
    session = web.get("/api/session").json()
    assert session["prices"] is True

    before = web.get("/api/performance", params={"currency": "USD"}).json()
    assert before["errors"] == []
    assert before["performance"]["missing_benchmarks"] == ["VT", "VOO"]

    fetched = web.post("/api/prices", headers={"X-Token": session["token"]})
    assert fetched.status_code == 200
    assert fetched.json() == {"closes": 6, "rates": 4, "splits": 0, "errors": []}

    after = web.get("/api/performance", params={"currency": "USD"}).json()
    assert after["errors"] == []
    assert after["performance"]["missing_benchmarks"] == []
    assert after["performance"]["benchmarks"] == [
        {"symbol": "VT", "return": "10.00"},
        {"symbol": "VOO", "return": "5.00"},
    ]
    assert any(symbol == "WIDG.US" for symbol, _, _ in source.asked)


def test_posting_prices_reports_partial_errors_and_handles_later_inception(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    july, august = date(2026, 7, 1), date(2026, 8, 1)
    close(conn, account("ibkr", "IBKR USD", "USD", "1000", month=july), month=july)
    close(conn, account("ibkr", "IBKR USD", "USD", "1000", month=august), month=august)
    close(conn, account("ibkr", "IBKR USD", "USD", "1000"))
    conn.close()
    # VT starts on 31 Aug (after the 31 Jul history start: missing on `all`, fine on `1m`).
    # VOO is absent from the source so fetching reports an error while keeping VT's closes.
    source = FakeSource(
        {
            "VT": [(day(31, 8), "100"), (day(30), "110")],
            **_fx_closes(day(30, 7)),
        }
    )
    app = webapp.create_app(
        path,
        clock=lambda: datetime(2026, 10, 1, 12, tzinfo=UTC),
        price_source=source,
        benchmarks=("VT", "VOO"),
    )
    web = TestClient(app, base_url=SITE)
    token = web.get("/api/session").json()["token"]
    fetched = web.post("/api/prices", headers={"X-Token": token})
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["closes"] == 2
    assert body["rates"] == 4
    assert body["errors"] == ["VOO: no prices (VOO: No data found)."]

    all_range = web.get("/api/performance", params={"currency": "USD", "range": "all"}).json()
    assert all_range["performance"]["missing_benchmarks"] == ["VT", "VOO"]
    short_range = web.get("/api/performance", params={"currency": "USD", "range": "1m"}).json()
    assert short_range["performance"]["benchmarks"] == [{"symbol": "VT", "return": "10.00"}]
    assert short_range["performance"]["missing_benchmarks"] == ["VOO"]

    # Subsequent fetch makes no redundant call for VT because asked_from was recorded.
    vt_calls_before = sum(1 for sym, _, _ in source.asked if sym == "VT")
    web.post("/api/prices", headers={"X-Token": token})
    vt_calls_after = sum(1 for sym, _, _ in source.asked if sym == "VT")
    assert vt_calls_after == vt_calls_before


def test_posting_prices_enforces_auth_read_only_source_and_instruments_config(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    close(conn, account("ibkr", "IBKR USD", "USD", "1000"))
    conn.close()

    # Without a price source: 404, and session advertises prices=False.
    no_source = TestClient(webapp.create_app(path), base_url=SITE)
    no_source_session = no_source.get("/api/session").json()
    assert no_source_session["prices"] is False
    assert (
        no_source.post("/api/prices", headers={"X-Token": no_source_session["token"]}).status_code
        == 404
    )

    # Read-only mode: 403 even when a price_source is passed, and session advertises prices=False.
    copy = TestClient(
        webapp.create_app(path, read_only=True, price_source=FakeSource()), base_url=SITE
    )
    copy_session = copy.get("/api/session").json()
    assert copy_session["prices"] is False
    assert copy.post("/api/prices", headers={"X-Token": copy_session["token"]}).status_code == 403

    # Missing/bad token and untrusted Origin: 403.
    bad_yaml = tmp_path / "instruments.yaml"
    bad_yaml.write_text("instruments: [\n", encoding="utf-8")
    setup = importing.Setup([], bad_yaml, {OWNER: []}, {}, "")
    guarded = TestClient(
        webapp.create_app(path, imports=setup, price_source=FakeSource()), base_url=SITE
    )
    token = guarded.get("/api/session").json()["token"]
    assert guarded.post("/api/prices").status_code == 403
    assert guarded.post("/api/prices", headers={"X-Token": "wrong"}).status_code == 403
    assert (
        guarded.post(
            "/api/prices", headers={"X-Token": token, "Origin": "https://example.com"}
        ).status_code
        == 403
    )

    # Malformed instruments.yaml: 409 before opening the DB write connection.
    malformed = guarded.post("/api/prices", headers={"X-Token": token})
    assert malformed.status_code == 409
    assert malformed.json()["errors"]

    # Password-protected app without sign-in cookie: 401.
    locked = TestClient(
        webapp.create_app(path, password=STORED, price_source=FakeSource()), base_url=SITE
    )
    assert locked.post("/api/prices").status_code == 401
