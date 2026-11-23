"""The JSON of `/api`, and the frontend of its own at /app."""

import base64
import hashlib
from datetime import date
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import overview, staging
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import OCTOBER, OWNER, PARTNER, batch
from tests.test_networth import NOW, account, close
from tests.test_overview import household
from tests.web.test_login import STORED

SITE = "http://127.0.0.1:8765"
NOVEMBER = date(2026, 11, 1)


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("api") / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    household(conn)
    conn.close()
    return path


@pytest.fixture(scope="module")
def client(database: Path) -> TestClient:
    return TestClient(webapp.create_app(database, currency="CHF"), base_url=SITE)


def test_the_session_names_the_people_and_the_currencies(client: TestClient) -> None:
    sent = client.get("/api/session").json()
    assert [person["name"] for person in sent["people"]] == sorted([OWNER, PARTNER])
    assert sent["currency"] == "CHF"
    assert "EUR" in sent["currencies"]
    assert sent["read_only"] is False
    assert sent["signing"] is False
    assert sent["token"]


def test_the_overview_is_the_services_figures_as_text(client: TestClient, database: Path) -> None:
    sent = client.get("/api/overview").json()
    with db.reading(database) as conn:
        shown = overview.run(conn, currency="CHF")
    assert sent["errors"] == []
    figures = sent["overview"]
    assert figures["currency"] == "CHF"
    assert shown.day is not None
    assert figures["day"] == shown.day.isoformat()
    assert figures["total"] == format(shown.total, "f")
    assert [part["name"] for part in figures["assets"]] == [part.name for part in shown.assets]
    assert [holding["symbol"] for holding in figures["top"]] == [h.symbol for h in shown.top]
    assert [part["name"] for part in figures["people"]] == sorted([OWNER, PARTNER])
    assert isinstance(sent["checks"], list)


def test_one_persons_overview_has_no_people(client: TestClient) -> None:
    sent = client.get("/api/overview", params={"owner": OWNER, "currency": "EUR"}).json()
    assert sent["overview"]["currency"] == "EUR"
    assert sent["overview"]["people"] == []


def test_a_field_that_cannot_be_read_is_refused(client: TestClient) -> None:
    response = client.get("/api/overview", params={"currency": "XYZ"})
    assert response.status_code == 400
    assert response.json() == {"errors": ["'XYZ' is not one of EUR, CHF, USD, GBP, JPY."]}
    assert client.get("/api/networth", params={"by": "colour"}).status_code == 400


def test_someone_unknown_is_not_found_as_json(client: TestClient) -> None:
    response = client.get("/api/networth", params={"owner": "Nobody"})
    assert response.status_code == 404
    assert response.json() == {"errors": ["No owner named 'Nobody'."]}


def test_net_worth_runs_day_by_day_with_its_parts(client: TestClient) -> None:
    series = client.get("/api/networth", params={"by": "owner"}).json()["series"]
    assert series["currency"] == "CHF"
    assert [part["name"] for part in series["parts"]] == sorted([OWNER, PARTNER])
    assert all(len(part["values"]) == len(series["total"]) for part in series["parts"])
    assert client.get("/api/networth", params={"by": "total"}).json()["series"]["parts"] == []


def test_an_empty_database_has_nothing_to_show(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    conn.close()
    client = TestClient(webapp.create_app(path), base_url=SITE)
    figures = client.get("/api/overview").json()["overview"]
    assert figures["day"] is None
    assert figures["top"] == []
    series = client.get("/api/networth").json()["series"]
    assert series == {
        "currency": "EUR",
        "first": None,
        "total": [],
        "arrived": {},
        "parts": [],
        "notes": [],
    }
    assert client.get("/api/month").json() == {"month": None, "errors": []}


def test_a_missing_rate_is_said_and_the_rest_is_shown(client: TestClient) -> None:
    sent = client.get("/api/overview", params={"currency": "JPY"}).json()
    assert sent["overview"] is None
    assert sent["errors"]
    assert client.get("/api/networth", params={"currency": "JPY"}).json()["series"] is None
    month = client.get("/api/month", params={"currency": "JPY"}).json()
    assert month["month"] is None
    assert month["errors"]


def test_a_month_is_how_net_worth_moved_and_what_was_spent_as_text(client: TestClient) -> None:
    sent = client.get("/api/month", params={"currency": "USD"}).json()
    assert sent["errors"] == []
    shown = sent["month"]
    # The last month every account covers, and the months either side of it there are.
    assert (shown["month"], shown["first"], shown["last"], shown["over"]) == (
        "2026-09",
        "2026-09-01",
        "2026-09-30",
        True,
    )
    assert (shown["earlier"], shown["later"]) == ("2026-08", None)
    assert [shown[k] for k in ("before", "after", "moved", "income", "spent", "saved")] == [
        "30975.00",
        "30847.50",
        "-127.50",
        "0.00",
        "150.00",
        "-150.00",
    ]
    assert (shown["other"], shown["rate"], shown["rate_year"]) == ("22.50", None, None)
    assert shown["arrived"] == "0"
    assert shown["categories"] == [
        {"name": "Alimentari", "spent": "150.00", "usual": None, "against_usual": None}
    ]
    assert shown["missing"] == []
    # August: the history starts on its last day, so how it moved is not known.
    august = client.get("/api/month", params={"month": "2026-08"}).json()["month"]
    assert (august["month"], august["before"], august["moved"]) == ("2026-08", None, None)


def test_a_month_that_cannot_be_read_is_refused(client: TestClient) -> None:
    response = client.get("/api/month", params={"month": "September"})
    assert response.status_code == 400
    assert response.json() == {"errors": ["'September' is not a month written as 2026-10."]}


def test_what_waits_is_what_is_staged_and_the_accounts_left_behind(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    household(conn)
    # Two more months of Lukas's bank account: his card and his broker end two months before.
    for month in (OCTOBER, NOVEMBER):
        close(conn, account("migros", "Migros", "CHF", "700", month=month), month=month)
    # October of Anna's Revolut account, staged and not committed.
    revolut = account("revolut", "Revolut USD", "USD", "50", month=OCTOBER)
    staging.stage(conn, batch(revolut, owner=PARTNER, month=OCTOBER), NOW)
    conn.close()
    client = TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)
    sent = client.get("/api/attention").json()
    assert sent["staged"] == [{"owner": PARTNER, "count": 1}]
    assert [(line["owner"], line["account"], line["committed"]) for line in sent["behind"]] == [
        (OWNER, "IBKR USD", "2026-09-30"),
        (OWNER, "Swisscard", "2026-09-30"),
    ]
    assert (sent["differ"], sent["errors"]) == (0, [])
    hers = client.get("/api/attention", params={"owner": PARTNER}).json()
    assert (hers["staged"], hers["behind"]) == ([{"owner": PARTNER, "count": 1}], [])


def test_without_signing_in_the_api_says_so(database: Path) -> None:
    app = webapp.create_app(database, password=STORED)
    response = TestClient(app, base_url=SITE).get("/api/overview", follow_redirects=False)
    assert response.status_code == 401
    assert response.json() == {"errors": ["Sign in first."]}


SHELL = '<html><body><script>start("/app")</script></body></html>'


@pytest.fixture
def spa(tmp_path: Path) -> Path:
    folder = tmp_path / "spa"
    (folder / "_app").mkdir(parents=True)
    (folder / "index.html").write_text(SHELL)
    (folder / "_app" / "start.js").write_text("export {}")
    (tmp_path / "secret.txt").write_text("not served")
    return folder


def test_every_address_under_app_is_its_page_or_one_of_its_files(database: Path, spa: Path) -> None:
    client = TestClient(webapp.create_app(database, spa=spa), base_url=SITE)
    for address in ("/app", "/app/", "/app/holdings"):
        assert client.get(address).text == SHELL
    assert client.get("/app/_app/start.js").text == "export {}"
    assert client.get("/app/..%2Fsecret.txt").text == SHELL  # never a file outside it
    assert client.get("/apple").status_code == 404


def test_the_page_may_run_its_own_script_and_no_other(database: Path, spa: Path) -> None:
    client = TestClient(webapp.create_app(database, spa=spa), base_url=SITE)
    digest = base64.b64encode(hashlib.sha256(b'start("/app")').digest()).decode()
    policy = (
        f"default-src 'self'; script-src 'self' 'sha256-{digest}'; "
        "style-src 'self'; style-src-attr 'unsafe-inline'; frame-ancestors 'none'"
    )
    assert client.get("/app").headers["content-security-policy"] == policy
    assert client.get("/app/holdings").headers["content-security-policy"] == policy
    assert client.get("/app/_app/start.js").headers["content-security-policy"] == policy


def test_the_servers_own_pages_keep_the_strict_policy(database: Path, spa: Path) -> None:
    client = TestClient(webapp.create_app(database, spa=spa), base_url=SITE)
    strict = "default-src 'self'; frame-ancestors 'none'"
    for address in ("/", "/api/session", "/apple"):
        assert client.get(address).headers["content-security-policy"] == strict


def test_before_it_is_built_app_says_how_to_build_it(database: Path, tmp_path: Path) -> None:
    client = TestClient(webapp.create_app(database, spa=tmp_path / "none"), base_url=SITE)
    response = client.get("/app/")
    assert response.status_code == 404
    assert "make web" in response.text
    assert client.get("/").headers["content-security-policy"] == (
        "default-src 'self'; frame-ancestors 'none'"
    )


def test_without_a_frontend_there_is_no_app(client: TestClient) -> None:
    assert client.get("/app/").status_code == 404


def test_a_page_without_a_script_of_its_own_changes_no_policy(
    database: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "index.html").write_text('<script src="start.js"></script>')
    client = TestClient(webapp.create_app(database, spa=folder), base_url=SITE)
    assert client.get("/app").headers["content-security-policy"] == (
        "default-src 'self'; frame-ancestors 'none'"
    )


def test_what_is_held_is_sent_with_each_instrument_its_positions_and_its_spread(
    client: TestClient,
) -> None:
    sent = client.get("/api/holdings", params={"currency": "USD"}).json()
    assert sent["errors"] == []
    held = sent["holdings"]
    assert [held[k] for k in ("total", "cost", "gain", "return_percent", "unknown_cost")] == [
        "11672.50",
        "11650.00",
        "22.50",
        "0.19",
        0,
    ]
    assert held["accounts"] == 3
    # Each instrument, largest first, with its positions account by account.
    assert [(h["symbol"], h["share"], len(h["positions"])) for h in held["holdings"]] == [
        ("912797ZZ1", "84.58", 1),
        ("GADG", "7.71", 1),
        ("WIDG", "7.71", 3),
    ]
    widget = held["holdings"][2]["positions"]
    assert [(p["owner"], p["account"], p["value"]) for p in widget] == [
        (OWNER, "IBKR USD", "500.00"),
        (PARTNER, "IBKR USD", "350.00"),
        (PARTNER, "Revolut USD", "50.00"),
    ]
    assert list(held["spread"]) == ["asset", "currency", "account", "owner"]
    assert held["spread"]["currency"] == [{"name": "USD", "value": "11672.50", "share": "100.00"}]
    [bill] = held["ladder"]
    assert (bill["maturity"], bill["face"], bill["interest"]) == ("2026-12-24", "10000", "150.00")


def test_what_is_held_without_a_rate_says_so(client: TestClient, tmp_path: Path) -> None:
    sent = client.get("/api/holdings", params={"currency": "JPY"}).json()
    assert (sent["holdings"], sent["errors"][0][:28]) == (None, "No exchange rate is stored f")
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    conn.close()
    empty = TestClient(webapp.create_app(path), base_url=SITE)
    assert empty.get("/api/holdings").json() == {"holdings": None, "logos": {}, "errors": []}


def test_every_account_is_sent_with_its_statements_and_what_it_holds(client: TestClient) -> None:
    sent = client.get("/api/statements", params={"currency": "USD"}).json()
    assert (sent["covered"], sent["errors"], sent["checks"]) == ("2026-09-30", [], [])
    accounts = {(a["owner"], a["account"]): a for a in sent["accounts"]}
    migros = accounts[OWNER, "Migros"]
    assert (migros["committed"], migros["staged"], migros["behind"]) == ("2026-09-30", None, False)
    assert (migros["balance"], migros["monthly"]) == ("875.00", True)
    assert migros["spans"] == [{"first": "2026-09-01", "last": "2026-09-30", "committed": True}]
    [statement] = migros["statements"]
    assert (statement["filename"], statement["committed"], statement["original"]) == (
        "Migros 2026-09.csv",
        True,
        None,
    )
    assert client.get("/api/statements", params={"currency": "JPY"}).json()["errors"]
    assert client.get("/api/statements", params={"currency": "XYZ"}).status_code == 400


def test_transactions_are_sent_newest_first_with_what_they_add_up_to(client: TestClient) -> None:
    sent = client.get("/api/transactions").json()
    dates = [row["date"] for row in sent["rows"]]
    assert dates == sorted(dates, reverse=True)
    assert sent["count"] == len(sent["rows"])
    assert sum(t["count"] for t in sent["totals"]) == sent["count"]
    assert sum(c["count"] for c in sent["by_category"]) == sent["count"]
    assert sent["choices"]["leaves"]
    assert "expense" in sent["kinds"]
    assert {"recurring", "changeable", "category_parent"} <= set(sent["rows"][0])
    page = client.get("/api/transactions", params={"offset": "1", "limit": "2"}).json()
    assert (page["offset"], page["rows"]) == (1, sent["rows"][1:3])
    assert page["count"] == sent["count"]


def test_transactions_follow_the_filter_and_refuse_one_that_cannot_be_read(
    client: TestClient,
) -> None:
    hers = client.get("/api/transactions", params={"owner": PARTNER}).json()
    assert hers["rows"]
    assert {row["owner"] for row in hers["rows"]} == {PARTNER}
    refused = client.get("/api/transactions", params={"from": "yesterday"})
    assert refused.status_code == 400
    assert refused.json()["errors"]


@pytest.fixture
def writable(tmp_path: Path) -> TestClient:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    household(conn)
    conn.close()
    return TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)


def _changeable(client: TestClient) -> dict[str, str]:
    rows: list[dict[str, str]] = client.get("/api/transactions").json()["rows"]
    return next(row for row in rows if row["changeable"])


def test_a_category_is_changed_with_the_token_of_the_session(writable: TestClient) -> None:
    row = _changeable(writable)
    other = next(
        name
        for group in writable.get("/api/transactions").json()["choices"]["leaves"]
        for name in group["names"]
        if name != row["category"]
    )
    token = writable.get("/api/session").json()["token"]
    sent = {"owner": row["owner"], "ids": [row["id"]], "category": other, "remember": False}
    response = writable.post("/api/transactions/category", json=sent, headers={"X-Token": token})
    assert response.json() == {"changed": 1, "errors": []}
    rows = writable.get("/api/transactions").json()["rows"]
    assert next(r for r in rows if r["id"] == row["id"])["category"] == other


def test_a_change_without_the_token_or_from_another_site_is_refused(writable: TestClient) -> None:
    row = _changeable(writable)
    sent = {"owner": row["owner"], "ids": [row["id"]], "category": "Affitto"}
    assert writable.post("/api/transactions/category", json=sent).status_code == 403
    token = writable.get("/api/session").json()["token"]
    elsewhere = {"X-Token": token, "Origin": "https://example.com"}
    response = writable.post("/api/transactions/category", json=sent, headers=elsewhere)
    assert response.status_code == 403
    assert _changeable(writable)["category"] == row["category"]


def test_a_change_that_cannot_be_made_says_why(writable: TestClient) -> None:
    row = _changeable(writable)
    token = {"X-Token": writable.get("/api/session").json()["token"]}
    unknown = {"owner": row["owner"], "ids": [row["id"]], "category": "Nowhere"}
    response = writable.post("/api/transactions/category", json=unknown, headers=token)
    assert response.status_code == 409
    assert response.json()["errors"]
    garbled = writable.post("/api/transactions/category", content=b"[1, 2]", headers=token)
    assert garbled.status_code == 400


def test_a_copy_refuses_every_change(database: Path) -> None:
    copy = TestClient(webapp.create_app(database, read_only=True), base_url=SITE)
    session = copy.get("/api/session").json()
    assert session["rules"] is False
    row = _changeable(copy)
    sent = {"owner": row["owner"], "ids": [row["id"]], "category": "Affitto"}
    headers = {"X-Token": session["token"]}
    assert copy.post("/api/transactions/category", json=sent, headers=headers).status_code == 403


def test_the_cash_flow_is_of_the_year_to_date_unless_a_range_is_asked(client: TestClient) -> None:
    sent = client.get("/api/cashflow").json()
    assert (sent["first"], sent["last"], sent["by"], sent["errors"]) == (
        "2026-01-01",
        None,
        "month",
        [],
    )
    assert [r["label"] for r in sent["ranges"]] == ["Year to date", "Last 12 months"]
    shown = sent["report"]
    assert shown["currency"] == "CHF"
    assert [period["name"] for period in shown["periods"]][-1] == "2026-09"
    assert shown["periods"][-1]["spent_by"] == {"Alimentari": "120.00"}
    assert [category["name"] for category in shown["categories"]] == ["Alimentari"]
    assert shown["totals"]["spent"] == "120.00"
    assert {line["currency"] for line in shown["as_paid"]} <= {"CHF", "EUR", "USD"}
    quarters = client.get("/api/cashflow", params={"by": "quarter", "from": "2026-07"}).json()
    assert [period["name"] for period in quarters["report"]["periods"]] == ["2026-Q3"]


def test_a_cash_flow_that_cannot_be_read_is_refused(client: TestClient) -> None:
    assert client.get("/api/cashflow", params={"by": "decade"}).status_code == 400
    assert client.get("/api/cashflow", params={"owner": "Zoe"}).status_code == 400
    assert client.get("/api/cashflow", params={"currency": "XYZ"}).status_code == 400


def test_an_empty_database_has_no_cash_flow(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    conn.close()
    sent = TestClient(webapp.create_app(path), base_url=SITE).get("/api/cashflow").json()
    assert (sent["first"], sent["ranges"], sent["errors"]) == (None, [], [])


def test_rates_and_shares_of_the_cash_flow_are_in_percent(client: TestClient) -> None:
    shown = client.get("/api/cashflow").json()["report"]
    assert shown["categories"][0]["share"] == "100.000"
