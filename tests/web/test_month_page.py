import re
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze import monthclose
from finanze.models import CashBalance, Kind
from finanze.storage import db
from finanze.web import app as webapp
from tests.storage.helpers import (
    AUGUST,
    CLOSED_AT,
    OWNER,
    PARTNER,
    PEOPLE,
    batch,
    close,
    statement,
    txn,
)
from tests.web.test_app import rows, text
from tests.web.test_cashflow_page import tables

CLICKED = datetime(2026, 10, 9, 8, 15, tzinfo=UTC)
SITE = "http://127.0.0.1:8765"
LUKAS = [
    txn("-45.20", date=date(2026, 8, 3), source_ref="1"),
    txn("6000", date=date(2026, 8, 25), source_ref="2", kind=Kind.INCOME, category="Stipendio"),
    txn("-1000", date=date(2026, 9, 4), source_ref="3", kind=Kind.TRANSFER, category="Giroconto"),
    txn("-80", date=date(2026, 9, 5), source_ref="4", category="", counterparty="<i>Kiosk</i>"),
]
ANNA = [txn("-60", date=date(2026, 9, 12), source_ref="1")]


@pytest.fixture
def database(tmp_path: Path) -> Path:
    """Lukas: August committed, September open with a row to categorize. Anna: September open."""
    path = tmp_path / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, CLOSED_AT)
    close(conn, OWNER, (AUGUST, date(2026, 9, 30)), AUGUST, ("migros", LUKAS))
    hers = statement("migros", ANNA, [CashBalance("Migros", "CHF", Decimal(0), Decimal(-60))])
    monthclose.stage(conn, batch(hers, owner=PARTNER), CLOSED_AT)
    conn.close()
    return path


@pytest.fixture
def client(database: Path) -> TestClient:
    app = webapp.create_app(database, clock=lambda: CLICKED)
    return TestClient(app, base_url=SITE, follow_redirects=False)


def token(page: str) -> str:
    found = re.search(r'name="token" value="([^"]+)"', page)
    assert found
    return found.group(1)


def status(database: Path) -> dict[tuple[str, str], str]:
    conn = sqlite3.connect(database)
    found = conn.execute(
        "SELECT o.name, m.month, m.status FROM months m JOIN owners o ON o.id = m.owner_id"
    ).fetchall()
    conn.close()
    return {(owner, month[:7]): state for owner, month, state in found}


def test_the_months_are_listed_newest_first_and_link_to_their_page(client: TestClient) -> None:
    response = client.get("/months")
    assert rows(response.text) == [
        ["2026-09", "Anna", "open", "1", "1", ""],
        ["2026-09", "Lukas", "open", "1", "2", "1"],
        ["2026-08", "Lukas", "committed", "1", "2", ""],
    ]
    assert f'<a href="{SITE}/months/Anna/2026-09">2026-09</a>' in response.text
    assert f'<a href="{SITE}/months" aria-current="page">Months</a>' in response.text
    only = client.get("/months", params={"owner": "Anna"})
    assert [row[1] for row in rows(only.text)] == ["Anna"]
    assert client.get("/months", params={"owner": "Zoe"}).status_code == 404


def test_the_months_page_starts_with_what_each_person_has_committed(client: TestClient) -> None:
    response = client.get("/months")
    summary, listed = tables(response.text)
    assert summary == [
        ["Anna", "none", "0", "1"],
        ["Lukas", "2026-08 to 2026-08 (1)", "2", "1"],
    ]
    assert len(listed) == 3
    assert "Only committed months are shown on the other pages." in response.text
    only = tables(client.get("/months", params={"owner": "Lukas"}).text)[0]
    assert only == [["Lukas", "2026-08 to 2026-08 (1)", "2", "1"]]


def test_an_empty_database_says_how_to_start(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    conn = db.connect(path)
    db.migrate(conn, CLOSED_AT)
    conn.close()
    page = TestClient(webapp.create_app(path), base_url=SITE).get("/months").text
    assert "Nothing is imported yet" in page
    assert "<table" not in page


def test_an_open_month_says_what_blocks_it_and_has_no_commit_button(client: TestClient) -> None:
    response = client.get("/months/Lukas/2026-09")
    assert response.status_code == 200
    page = text(response.text)
    assert "September 2026, Lukas" in page
    assert "It cannot be committed yet: 1 transaction still needs a category" in page
    assert "<button>Commit" not in response.text
    statements, pending, totals = tables(response.text)
    assert statements == [["migros", "migros.csv", "2026-08-01 to 2026-09-30", "yes", "2"]]
    assert [row[:2] for row in pending] == [
        ["&lt;i&gt;Kiosk&lt;/i&gt; paid · KONSUM", "2026-09-05 · Migros · -80.00 CHF"]
    ]
    assert totals == [["Giroconto transfer", "-1,000.00 CHF", "1"]]
    assert f'<a href="{SITE}/months?owner=Lukas" aria-current="page">Months</a>' in response.text


def test_a_month_that_is_ready_is_committed_with_a_copy_from_before(
    client: TestClient, database: Path
) -> None:
    page = client.get("/months/Anna/2026-09").text
    assert "nothing stands in the way" in page
    assert "<button>Commit the month</button>" in page
    response = client.post("/months/Anna/2026-09/commit", data={"token": token(page)})
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/months/Anna/2026-09"
    assert status(database)["Anna", "2026-09"] == "committed"
    assert [copy.name for copy in (database.parent / "backups").iterdir()] == [
        "finanze-20261009T081500.db"
    ]

    after = client.get("/months/Anna/2026-09").text
    assert "Committed on 2026-10-09." in after
    assert "<button>Reopen the month</button>" in after
    assert tables(after)[-1] == [["Spesa", "-60.00 CHF", "1"]]
    reopened = client.post("/months/Anna/2026-09/reopen", data={"token": token(after)})
    assert reopened.status_code == 303
    assert status(database)["Anna", "2026-09"] == "open"


def test_a_month_that_is_not_ready_is_refused_and_says_why(
    client: TestClient, database: Path
) -> None:
    page = client.get("/months/Lukas/2026-08").text
    response = client.post("/months/Lukas/2026-09/commit", data={"token": token(page)})
    assert response.status_code == 409
    assert "Not committed: 1 transactions of 2026-09 still need a category." in text(response.text)
    assert response.text.count('role="alert"') == 1
    assert status(database)["Lukas", "2026-09"] == "open"
    assert not list((database.parent / "backups").iterdir())


def test_a_reconciliation_failure_cannot_be_committed_from_the_page(
    client: TestClient, database: Path
) -> None:
    # A later statement of Anna's bank starts from a balance no statement leads to.
    later = statement(
        "migros",
        [txn("-1", date=date(2026, 11, 3))],
        [CashBalance("Migros", "CHF", Decimal(100), Decimal(99))],
        month=date(2026, 11, 1),
    )
    conn = db.connect(database)
    monthclose.stage(conn, batch(later, owner=PARTNER, month=date(2026, 11, 1)), CLOSED_AT)
    conn.close()
    page = client.get("/months/Anna/2026-09").text
    assert "Migros (CHF): no statement covers the days after 2026-09-30" in page
    assert "<button>" not in page
    response = client.post("/months/Anna/2026-09/commit", data={"token": token_of(client)})
    assert response.status_code == 409
    assert "Not committed: Migros (CHF): no statement covers" in text(response.text)
    assert status(database)["Anna", "2026-09"] == "open"


def token_of(client: TestClient) -> str:
    """The token, from a page that has a form."""
    return token(client.get("/months/Lukas/2026-08").text)


@pytest.mark.parametrize("action", ["commit", "reopen"])
def test_a_form_without_the_token_of_these_pages_changes_nothing(
    client: TestClient, database: Path, action: str
) -> None:
    before = status(database)
    url = f"/months/Lukas/2026-08/{action}"
    assert client.post(url).status_code == 403
    assert client.post(url, data={"token": "guessed"}).status_code == 403
    good = {"token": token_of(client)}
    elsewhere = client.post(url, data=good, headers={"origin": "https://evil.example"})
    assert elsewhere.status_code == 403
    assert status(database) == before
    here = client.post(url, data=good, headers={"origin": SITE})
    assert here.status_code == 303


def test_a_browser_that_withholds_the_origin_can_still_send_the_forms(client: TestClient) -> None:
    # `referrer-policy: no-referrer` makes a browser send `Origin: null` with these forms.
    page = client.get("/months/Lukas/2026-08")
    assert page.headers["referrer-policy"] == "no-referrer"
    url = "/months/Lukas/2026-08/reopen"
    sent = client.post(url, data={"token": token(page.text)}, headers={"origin": "null"})
    assert sent.status_code == 303
    assert client.post(url, data={"token": "x"}, headers={"origin": "null"}).status_code == 403


def test_a_form_that_is_not_text_is_refused(client: TestClient, database: Path) -> None:
    before = status(database)
    url = "/months/Lukas/2026-08/reopen"
    form = {"content-type": "application/x-www-form-urlencoded"}
    assert client.post(url, content=b"token=%C3%A9", headers=form).status_code == 403
    assert client.post(url, content=b"token=\xff", headers=form).status_code == 400
    assert status(database) == before


def test_a_month_cannot_be_changed_by_following_a_link(client: TestClient) -> None:
    assert client.get("/months/Lukas/2026-08/reopen").status_code == 405


@pytest.mark.parametrize(
    "path", ["/months/Zoe/2026-09", "/months/Lukas/2020-01", "/months/Lukas/September"]
)
def test_a_month_that_does_not_exist_is_not_found(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404
    assert client.post(f"{path}/reopen", data={"token": token_of(client)}).status_code == 404


def test_each_start_of_the_app_has_a_token_of_its_own(database: Path) -> None:
    first, second = webapp.create_app(database), webapp.create_app(database)
    assert first.state.token != second.state.token
    assert len(first.state.token) >= 32


def waiting(database: Path) -> str:
    conn = sqlite3.connect(database)
    (found,) = conn.execute("SELECT id FROM transactions WHERE category_id IS NULL").fetchall()
    conn.close()
    return str(found[0])


def stored(database: Path, txn_id: str) -> tuple[str | None, str | None]:
    conn = sqlite3.connect(database)
    (found,) = conn.execute(
        "SELECT c.name, t.category_source FROM transactions t "
        "LEFT JOIN categories c ON c.id = t.category_id WHERE t.id = ?",
        (txn_id,),
    ).fetchall()
    conn.close()
    return (found[0], found[1])


def test_the_form_offers_leaf_categories_and_only_these_rows_without_rules(
    client: TestClient, database: Path
) -> None:
    page = client.get("/months/Lukas/2026-09").text
    assert f'<input type="hidden" name="id" value="{waiting(database)}">' in page
    assert "<option>Affitto</option>" in page
    assert "<option>Casa</option>" not in page
    assert 'type="radio"' not in page
    assert "For these rows only." in page


def test_a_category_set_for_these_rows_unblocks_the_month(
    client: TestClient, database: Path
) -> None:
    row = waiting(database)
    form = {"token": token_of(client), "id": row, "category": "Regali", "remember": "rule"}
    response = client.post("/months/Lukas/2026-09/categorize", data=form)
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/months/Lukas/2026-09"
    assert stored(database, row) == ("Regali", "manual")  # no rules file: not remembered
    page = client.get("/months/Lukas/2026-09").text
    assert "Need a category" not in page
    assert "<button>Commit the month</button>" in page


def test_a_category_to_remember_is_saved_as_a_rule(database: Path, tmp_path: Path) -> None:
    rules = webapp.Rules(tmp_path / "rules.local.yaml", PEOPLE)
    client = TestClient(webapp.create_app(database, rules=rules), base_url=SITE)
    page = client.get("/months/Lukas/2026-09").text
    assert '<input type="radio" name="remember" value="rule" checked>' in page
    row = waiting(database)
    form = {"token": token(page), "id": row, "category": "Regali", "remember": "rule"}
    assert client.post("/months/Lukas/2026-09/categorize", data=form).status_code == 200
    assert stored(database, row) == ("Regali", "rule")
    assert rules.shared.read_text(encoding="utf-8") == (
        'rules:\n  - {match: "(?m)^<i>Kiosk</i>$", category: "Regali", direction: out}\n'
    )
    assert rules.of("Lukas").own == tmp_path / "rules.lukas.yaml"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"category": ""}, "No category was set: no category was chosen."),
        ({"category": "Casa"}, "No category was set: unknown category &#39;Casa&#39;."),
        ({"id": "gone"}, "No category was set: those rows are no longer waiting"),
    ],
)
def test_a_choice_that_cannot_be_made_is_said(
    client: TestClient, database: Path, change: dict[str, str], message: str
) -> None:
    row = waiting(database)
    form = {"token": token_of(client), "id": row, "category": "Regali", "remember": "rows"}
    response = client.post("/months/Lukas/2026-09/categorize", data=form | change)
    assert response.status_code == 409
    assert message in response.text
    assert stored(database, row) == (None, None)


def test_rows_of_a_committed_or_missing_month_cannot_be_categorized(client: TestClient) -> None:
    form = {"token": token_of(client), "id": "x", "category": "Regali"}
    response = client.post("/months/Lukas/2026-08/categorize", data=form)
    assert response.status_code == 409
    assert "2026-08 is committed for Lukas: reopen it first" in response.text
    assert client.post("/months/Zoe/2026-08/categorize", data=form).status_code == 404
    forged = client.post("/months/Lukas/2026-08/categorize", data=form | {"token": "x"})
    assert forged.status_code == 403
