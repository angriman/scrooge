"""The JSON of `/api`, and the frontend of its own at /app."""

import base64
import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import categories, networth, overview, staging
from scrooge.models import Kind
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import OCTOBER, OWNER, PARTNER, batch
from tests.test_income import SALARY
from tests.test_networth import NOW, account, close, deposit_and_pensions, spent
from tests.test_overview import household
from tests.web import builders
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
    assert sent["language"] == "en"
    assert sent["languages"] == ["en", "it"]
    assert sent["read_only"] is False
    assert sent["signing"] is False
    assert sent["token"]

    assert {person["covered"] for person in sent["people"]} == {"2026-09-30"}


def test_the_session_says_up_to_which_day_each_persons_statements_are_in(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    builders.household(path)  # Lukas: August committed; Anna: September staged, none committed
    client = TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)
    covered = {p["name"]: p["covered"] for p in client.get("/api/session").json()["people"]}
    assert covered == {OWNER: "2026-08-31", PARTNER: None}
    conn = db.connect(path)
    staging.commit(conn, owner=PARTNER, now=builders.BUILT)
    conn.close()
    covered = {p["name"]: p["covered"] for p in client.get("/api/session").json()["people"]}
    assert covered == {OWNER: "2026-08-31", PARTNER: "2026-09-30"}


def test_someone_whose_only_account_is_not_monthly_has_no_day_covered(tmp_path: Path) -> None:
    """A broker that reports quarterly says nothing of a month: there is no day up to which
    Anna's statements are all in, so the page shows no "Complete up to" for her."""
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    close(conn, account("migros", "Migros", "CHF", "1000"))
    close(conn, account("morganstanley", "Morgan Stanley", "USD", "5000"), owner=PARTNER)
    conn.close()
    client = TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)
    covered = {p["name"]: p["covered"] for p in client.get("/api/session").json()["people"]}
    assert covered == {OWNER: "2026-09-30", PARTNER: None}


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
    assert figures["securities"]["value"] == format(shown.securities.value, "f")
    assert [part["name"] for part in figures["people"]] == sorted([OWNER, PARTNER])
    # Shares to the tenth the app shows, rounded once from the values: each cut adds up to 100.
    for cut in ("assets", "held", "people"):
        shares = [Decimal(part["share"]) for part in figures[cut]]
        assert all(share == share.quantize(Decimal("0.1")) for share in shares)
        assert sum(shares) == 100
    exact = shown.securities.value * 100 / shown.total
    assert figures["securities"]["share"] == str(exact.quantize(Decimal("0.1")))
    assert isinstance(sent["checks"], list)
    # The holdings are the investments page's: the overview sends none of them.
    assert set(sent) == {"overview", "checks", "errors"}
    assert "top" not in figures


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


def test_net_worth_is_cut_into_what_is_liquid_and_what_frozen(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    deposit_and_pensions(conn)
    conn.close()
    named = (networth.FrozenAccount("Rent deposit"), networth.FrozenAccount("Rent depot"))
    client = TestClient(webapp.create_app(path, currency="CHF", frozen=named), base_url=SITE)
    sent = client.get("/api/networth", params={"by": "liquidity"}).json()
    [error] = sent["errors"]
    assert error.startswith("No account is called Rent depot")
    series = sent["series"]
    assert [part["name"] for part in series["parts"]] == ["Frozen", "Liquid"]  # frozen below
    frozen, liquid = series["parts"]
    assert (frozen["values"][-1], liquid["values"][-1]) == ("23500.00", "1890.55")
    # The second pillar came into view on 10 September, the tenth day after 31 August.
    assert (frozen["arrived"], liquid["arrived"]) == ({"10": "20000.00"}, {})
    assert series["arrived"] == frozen["arrived"]
    # Only the cut by liquidity is about what is frozen: the others say nothing of it.
    assert client.get("/api/networth", params={"by": "asset"}).json()["errors"] == []


def test_an_empty_database_has_nothing_to_show(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    conn.close()
    client = TestClient(webapp.create_app(path), base_url=SITE)
    figures = client.get("/api/overview").json()["overview"]
    assert figures["day"] is None
    assert figures["assets"] == []
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
    assert (shown["work"], shown["rate_salary"], shown["rate_salary_year"]) == ("0.00", None, None)
    assert shown["arrived"] == "0"
    assert shown["categories"] == [
        {"name": "Food", "spent": "150.00", "usual": None, "against_usual": None}
    ]
    assert shown["missing"] == []
    # August: the history starts on its last day, so how it moved is not known.
    august = client.get("/api/month", params={"month": "2026-08"}).json()["month"]
    assert (august["month"], august["before"], august["moved"]) == ("2026-08", None, None)


def test_a_month_sends_what_was_saved_of_income_and_of_salary(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    rows = (
        spent("5000", date(2026, 9, 25), **SALARY),
        spent("1000", date(2026, 9, 26), kind=Kind.INCOME, category=categories.INTEREST),
        spent("-1500", date(2026, 9, 10)),
    )
    close(conn, account("migros", "Migros", "CHF", "1000", *rows))
    conn.close()
    client = TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)
    shown = client.get("/api/month").json()["month"]
    # 4500 kept of the 6000 that came in, and of the 5000 of salary.
    assert [shown[k] for k in ("income", "work", "rate", "rate_salary")] == [
        "6000.00",
        "5000.00",
        "75.00",
        "90.0",
    ]
    assert (shown["rate_year"], shown["rate_salary_year"]) == (None, None)


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
    # The old addresses too: their pages are sent on to the new ones in the browser.
    for address in ("/app", "/app/", "/app/investments", "/app/accounts", "/app/holdings"):
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
    for address in ("/app/investments", "/app/accounts", "/app/holdings"):
        assert client.get(address).headers["content-security-policy"] == policy
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
    # Each instrument, largest first, with its positions account by account; its share to
    # the tenth the app shows, shared out of the exact values so that they add up to 100.
    assert [(h["symbol"], h["share"], len(h["positions"])) for h in held["holdings"]] == [
        ("912797ZZ1", "84.6", 1),
        ("GADG", "7.7", 1),
        ("WIDG", "7.7", 3),
    ]
    widget = held["holdings"][2]["positions"]
    assert [(p["owner"], p["account"], p["value"]) for p in widget] == [
        (OWNER, "IBKR USD", "500.00"),
        (PARTNER, "IBKR USD", "350.00"),
        (PARTNER, "Revolut USD", "50.00"),
    ]
    assert list(held["spread"]) == ["asset", "currency", "account", "owner"]
    assert held["spread"]["currency"] == [{"name": "USD", "value": "11672.50", "share": "100.0"}]
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
    assert all("key" in c for c in sent["by_category"])
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
    sent = {"owner": row["owner"], "ids": [row["id"]], "category": categories.RENT}
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
    sent = {"owner": row["owner"], "ids": [row["id"]], "category": categories.RENT}
    headers = {"X-Token": session["token"]}
    assert copy.post("/api/transactions/category", json=sent, headers=headers).status_code == 403


def test_the_cash_flow_is_of_the_year_to_date_unless_a_range_is_asked(client: TestClient) -> None:
    sent = client.get("/api/cashflow").json()
    # The fixture's statements only cover September 2026, so YTD starts at the first month.
    assert (sent["first"], sent["last"], sent["by"], sent["errors"]) == (
        "2026-09-01",
        None,
        "month",
        [],
    )
    assert [r["label"] for r in sent["ranges"]] == ["Year to date", "Last 12 months"]
    assert [r["key"] for r in sent["ranges"]] == ["ytd", "12m"]
    shown = sent["report"]
    assert shown["currency"] == "CHF"
    assert [period["name"] for period in shown["periods"]] == ["2026-09"]
    assert shown["months"] == 1
    assert shown["periods"][-1]["spent_by"] == {"Food": "120.00"}
    assert [category["name"] for category in shown["categories"]] == ["Food"]
    assert shown["categories"][0]["monthly"] == "120.00"
    assert shown["totals"]["spent"] == "120.00"
    assert {line["currency"] for line in shown["as_paid"]} <= {"CHF", "EUR", "USD"}
    quarters = client.get("/api/cashflow", params={"by": "quarter", "from": "2026-07"}).json()
    assert [period["name"] for period in quarters["report"]["periods"]] == ["2026-Q3"]


def test_a_period_of_another_page_picks_the_months_of_the_cash_flow(client: TestClient) -> None:
    def first(**params: str) -> str | None:
        found: str | None = client.get("/api/cashflow", params=params).json()["first"]
        return found

    # Statements only start in September 2026, so every broader range clamps to that month.
    assert first(range="1m") == "2026-09-01"
    assert first(range="3m") == "2026-09-01"
    assert first(range="1y") == "2026-09-01"
    assert first(range="ytd") == "2026-09-01"
    assert first(range="3m", **{"from": "2026-05"}) == "2026-09-01"
    sent = client.get("/api/cashflow").json()
    assert [r["range"] for r in sent["ranges"]] == ["ytd", "1y"]
    assert client.get("/api/cashflow", params={"range": "2w"}).status_code == 400


def test_cash_flow_clamps_ranges_to_the_first_statement_month(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    # Statements start mid-August and run through September: 2 months in a 9-month YTD range.
    aug = date(2026, 8, 1)
    sep = date(2026, 9, 1)
    close(
        conn,
        account(
            "migros",
            "Migros",
            "CHF",
            "1000",
            spent("-100", date(2026, 8, 12)),
            month=aug,
        ),
        month=aug,
    )
    close(
        conn,
        account(
            "migros",
            "Migros",
            "CHF",
            "900",
            spent("-200", date(2026, 9, 15)),
            month=sep,
        ),
        month=sep,
    )
    conn.close()
    local = TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)

    for params in ({}, {"range": "ytd"}, {"range": "1y"}):
        sent = local.get("/api/cashflow", params=params).json()
        assert sent["first"] == "2026-08-01"
        shown = sent["report"]
        assert [p["name"] for p in shown["periods"]] == ["2026-08", "2026-09"]
        assert shown["months"] == 2
        # 300 over 2 statement-covered months = 150/month (not divided by 9 or 12).
        assert shown["categories"][0]["monthly"] == "150.00"

    quarter = local.get("/api/cashflow", params={"range": "1y", "by": "quarter"}).json()
    assert [p["name"] for p in quarter["report"]["periods"]] == ["2026-Q3"]
    assert quarter["report"]["months"] == 2

    year = local.get("/api/cashflow", params={"range": "1y", "by": "year"}).json()
    assert [p["name"] for p in year["report"]["periods"]] == ["2026"]
    assert year["report"]["months"] == 2

    earlier = local.get("/api/cashflow", params={"from": "2025", "to": "2025"}).json()
    assert earlier["report"]["totals"] is None
    assert earlier["report"]["periods"] == []
    assert earlier["report"]["months"] == 0


def test_cash_flow_clamps_earlier_year_and_all_when_history_starts_mid_year(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
    conn = db.connect(path)
    db.migrate(conn, NOW)
    jul_2025 = date(2025, 7, 1)
    sep_2026 = date(2026, 9, 1)
    close(
        conn,
        account(
            "migros",
            "Migros",
            "CHF",
            "1000",
            spent("-600", date(2025, 7, 15)),
            month=jul_2025,
        ),
        month=jul_2025,
    )
    close(
        conn,
        account(
            "migros",
            "Migros",
            "CHF",
            "400",
            spent("-300", date(2026, 9, 15)),
            month=sep_2026,
        ),
        month=sep_2026,
    )
    conn.close()
    local = TestClient(webapp.create_app(path, currency="CHF"), base_url=SITE)

    # Unclamped periods within the history keep their exact starts.
    assert local.get("/api/cashflow", params={"range": "1m"}).json()["first"] == "2026-09-01"
    assert local.get("/api/cashflow", params={"range": "3m"}).json()["first"] == "2026-07-01"
    assert local.get("/api/cashflow", params={"range": "ytd"}).json()["first"] == "2026-01-01"
    assert local.get("/api/cashflow", params={"range": "1y"}).json()["first"] == "2025-10-01"
    assert (
        local.get("/api/cashflow", params={"range": "3m", "from": "2026-05"}).json()["first"]
        == "2026-05-01"
    )

    # "All" and "2025" both start in July 2025 (never January 2025).
    all_sent = local.get("/api/cashflow", params={"range": "all"}).json()
    assert all_sent["first"] == "2025-07-01"
    assert all_sent["report"]["months"] == 15
    assert all_sent["report"]["periods"][0]["name"] == "2025-07"

    y2025 = local.get("/api/cashflow", params={"from": "2025", "to": "2025"}).json()
    assert (y2025["first"], y2025["last"]) == ("2025-07-01", "2025-12-31")
    assert y2025["report"]["months"] == 6
    assert y2025["report"]["categories"][0]["monthly"] == "100.00"


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
    assert shown["categories"][0]["share"] == "100"


def test_choosing_a_language_remembers_it_in_a_cookie_and_translates_categories(
    database: Path,
) -> None:
    client = TestClient(webapp.create_app(database, currency="CHF"), base_url=SITE)
    token = client.get("/api/session").json()["token"]
    assert client.post("/api/language", json={"language": "it"}).status_code == 403
    assert (
        client.post(
            "/api/language", json={"language": "fr"}, headers={"x-token": token}
        ).status_code
        == 400
    )
    assert (
        client.post("/api/language", content="not-json", headers={"x-token": token}).status_code
        == 400
    )

    chosen = client.post("/api/language", json={"language": "it"}, headers={"x-token": token})
    assert chosen.status_code == 200
    assert chosen.json()["language"] == "it"
    assert client.get("/api/session").json()["language"] == "it"

    shown = client.get("/api/cashflow").json()["report"]
    expected_food = categories.LABELS_IT[categories.GROUP_FOOD]
    assert [category["name"] for category in shown["categories"]] == [expected_food]
    assert (
        client.get("/api/cashflow", params={"lang": "en"}).json()["report"]["categories"][0]["name"]
        == categories.LABELS_EN[categories.GROUP_FOOD]
    )
    csv_it = client.get("/transactions.csv").text
    assert categories.LABELS_IT[categories.GROCERIES] in csv_it
    assert expected_food in csv_it


def test_choosing_a_language_is_allowed_in_read_only_mode(database: Path) -> None:
    client = TestClient(webapp.create_app(database, currency="CHF", read_only=True), base_url=SITE)
    token = client.get("/api/session").json()["token"]
    chosen = client.post("/api/language", json={"language": "it"}, headers={"x-token": token})
    assert chosen.status_code == 200
    assert client.get("/api/session").json()["language"] == "it"
