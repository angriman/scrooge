"""The months: how far each account is committed, the months committed statements cover, and
one month's page, where the category of a committed row is changed."""

import re
from datetime import date
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze import staging
from finanze.storage import db
from finanze.web import app as webapp
from tests.storage.helpers import OWNER, PEOPLE, certificate, certified, txn
from tests.web.builders import (
    AUGUST,
    BUILT,
    LUKAS_AUGUST,
    SITE,
    category_of,
    close,
    household,
    row_id,
)
from tests.web.test_app import text
from tests.web.test_cashflow_page import tables


@pytest.fixture
def database(tmp_path: Path) -> Path:
    """Lukas: August committed, September staged. Anna: September staged."""
    path = tmp_path / "finanze.db"
    household(path)
    return path


@pytest.fixture
def client(database: Path) -> TestClient:
    return TestClient(webapp.create_app(database), base_url=SITE, follow_redirects=False)


def sent(client: TestClient, **fields: str) -> dict[str, str]:
    return {"token": client.app.state.token} | fields  # type: ignore[attr-defined]  # the Starlette app


def test_the_months_page_starts_with_how_far_each_account_is_committed(client: TestClient) -> None:
    response = client.get("/months")
    assert response.status_code == 200
    coverage, people, listed = tables(response.text)
    assert coverage == [
        ["Anna", "Migros", "Migros Bank account statement", "nothing", "staged to 2026-09-30"],
        ["Lukas", "Migros", "Migros Bank account statement", "2026-08-31", "staged to 2026-09-30"],
    ]
    assert people == [
        ["Anna", "none", "0", "1"],
        ["Lukas", "2026-08-01 to 2026-08-31 (1 statement)", "3", "1"],
    ]
    assert listed == [["2026-08", "Lukas", "1", "3"]]
    assert f'<a href="{SITE}/import?owner=Lukas">1</a>' in response.text
    assert "Only committed statements are shown on the other pages." in response.text
    assert "commit" not in " ".join(row[2] for row in listed).lower()
    only = tables(client.get("/months", params={"owner": "Lukas"}).text)
    assert [row[0] for row in only[0]] == ["Lukas"]
    assert client.get("/months", params={"owner": "Zoe"}).status_code == 404


def committed_pair(path: Path, revolut: tuple[date, date]) -> TestClient:
    """Lukas, with his bank committed for August and his other account for `revolut`."""
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    close(conn, OWNER, AUGUST, ("migros", LUKAS_AUGUST))
    row = txn("-9", source_ref="9", account="Revolut CHF", date=revolut[0])
    close(conn, OWNER, revolut, ("revolut", [row]))
    conn.close()
    return TestClient(webapp.create_app(path), base_url=SITE)


def test_the_months_are_listed_newest_first_and_link_to_their_page(tmp_path: Path) -> None:
    september = (date(2026, 9, 1), date(2026, 9, 30))
    response = committed_pair(tmp_path / "f.db", september).get("/months")
    listed = tables(response.text)[-1]
    assert [row[:2] for row in listed] == [["2026-09", "Lukas"], ["2026-08", "Lukas"]]
    assert f'<a href="{SITE}/months/Lukas/2026-09">2026-09</a>' in response.text
    assert f'<a href="{SITE}/months" aria-current="page">Months</a>' in response.text
    assert 'class="behind"' not in response.text


def test_an_account_far_behind_the_newest_day_committed_is_flagged(tmp_path: Path) -> None:
    june = (date(2026, 6, 1), date(2026, 6, 30))
    page = committed_pair(tmp_path / "f.db", june).get("/months").text
    assert page.count('<tr class="behind">') == 1
    assert "2026-06-30 <strong>behind</strong>" in page
    assert "more than 35 days before the newest day committed" in text(page)
    flagged = page.partition('<tr class="behind">')[2].partition("</tr>")[0]
    assert "Revolut account statement" in flagged
    assert "Lukas" in flagged


def test_a_pension_fund_is_never_behind(tmp_path: Path) -> None:
    # Its certificates come once or twice a year, not with every statement.
    path = tmp_path / "f.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    close(conn, OWNER, AUGUST, ("migros", LUKAS_AUGUST))
    staging.stage(conn, certified(certificate(date(2026, 3, 1), "35000")), BUILT)
    staging.commit(conn, owner=OWNER, now=BUILT)
    conn.close()
    page = TestClient(webapp.create_app(path), base_url=SITE).get("/months").text
    assert "Pillar 2 AXA" in page
    assert 'class="behind"' not in page


def test_an_account_a_few_weeks_behind_is_not_flagged(tmp_path: Path) -> None:
    july = (date(2026, 7, 1), date(2026, 7, 31))
    assert (
        "behind"
        not in committed_pair(tmp_path / "f.db", july).get("/months").text.partition("</table>")[0]
    )


def test_an_empty_database_says_how_to_start(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    conn.close()
    page = TestClient(webapp.create_app(path), base_url=SITE).get("/months").text
    assert "Nothing is committed yet" in page
    assert "<table" not in page


def test_a_month_says_which_committed_statements_cover_it_and_what_they_hold(
    client: TestClient,
) -> None:
    response = client.get("/months/Lukas/2026-08")
    assert response.status_code == 200
    page = text(response.text)
    assert "August 2026, Lukas" in page
    assert "Commit" not in response.text
    assert "Reopen" not in response.text
    statements, changeable, totals = tables(response.text)
    assert statements == [
        ["Migros Bank account statement", "migros.csv", "2026-08-01 to 2026-08-31", "yes", "3"]
    ]
    assert [row[:3] for row in changeable] == [
        ["KONSUM paid", "2026-08-03 · Migros · -45.20 CHF", "Spesa"],
        ["KONSUM received", "2026-08-25 · Migros · 6,000.00 CHF", "Stipendio"],
        ["SAVINGS TOPUP AUG paid", "2026-08-28 · Migros · -1,000.00 CHF", "Giroconto"],
    ]
    assert ["Giroconto transfer", "-1,000.00 CHF", "1"] in totals
    assert f'<a href="{SITE}/months?owner=Lukas" aria-current="page">Months</a>' in response.text


def test_a_month_nothing_committed_covers_says_so(client: TestClient) -> None:
    page = client.get("/months/Lukas/2026-09")
    assert page.status_code == 200
    assert "No committed statement covers this month." in page.text
    assert "No categorized transactions are booked in this month." in page.text
    assert client.get("/months/Anna/2026-09").status_code == 200


@pytest.mark.parametrize("action", ["commit", "reopen"])
def test_a_month_can_no_longer_be_committed_or_reopened(client: TestClient, action: str) -> None:
    url = f"/months/Lukas/2026-08/{action}"
    assert client.post(url, data=sent(client)).status_code == 404
    assert client.get(url).status_code == 404


@pytest.mark.parametrize(
    "path", ["/months/Zoe/2026-09", "/months/Lukas/2020-01x", "/months/Lukas/September"]
)
def test_a_month_that_does_not_exist_is_not_found(client: TestClient, path: str) -> None:
    if path.endswith("2020-01x"):
        assert client.get(path).status_code == 404
    else:
        assert client.get(path).status_code == 404
    assert client.post(f"{path}/categorize", data=sent(client)).status_code == 404


def test_each_start_of_the_app_has_a_token_of_its_own(database: Path) -> None:
    first, second = webapp.create_app(database), webapp.create_app(database)
    assert first.state.token != second.state.token
    assert len(first.state.token) >= 32


def test_the_form_offers_leaf_categories_and_only_these_rows_without_rules(
    client: TestClient, database: Path
) -> None:
    page = client.get("/months/Lukas/2026-08").text
    assert (
        f'<input type="hidden" name="id" value="{row_id(database, "Lukas", "SAVINGS TOPUP AUG")}">'
        in page
    )
    assert '<input type="hidden" name="change" value="1">' in page
    assert '<input type="hidden" name="owner" value="Lukas">' in page
    assert (
        '<input name="category" list="categories" placeholder="Choose" autocomplete="off" '
        'required aria-label="Category for SAVINGS TOPUP AUG">'
    ) in page
    assert page.count('<datalist id="categories">') == 1
    assert '<option value="Affitto" label="Casa" data-side="uscite">' in page
    assert '<option value="Casa"' not in page
    assert re.findall(r"<script[^>]*>", page) == [f'<script src="{SITE}/static/picker.js" defer>']
    assert 'type="radio"' not in page
    assert "For these rows only." in page


def test_a_category_that_was_wrong_in_a_committed_row_is_changed_from_the_page(
    client: TestClient, database: Path
) -> None:
    row = row_id(database, "Lukas", "SAVINGS TOPUP AUG")
    form = sent(client, id=row, category="Regali", remember="rows", owner="Lukas")
    wrong = client.post("/months/Lukas/2026-08/categorize", data=form)  # not said to be a change
    assert wrong.status_code == 409
    assert "those rows are no longer waiting for a category" in wrong.text
    response = client.post("/months/Lukas/2026-08/categorize", data=form | {"change": "1"})
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/months/Lukas/2026-08"
    assert category_of(database, row) == ("Regali", "manual")
    assert '<td data-label="Now">Regali</td>' in client.get("/months/Lukas/2026-08").text
    # The other pages follow at once.
    assert "Regali" in client.get("/transactions", params={"owner": "Lukas"}).text


def test_a_choice_for_committed_rows_that_cannot_be_made_is_said(
    client: TestClient, database: Path
) -> None:
    row = row_id(database, "Lukas", "SAVINGS TOPUP AUG")
    form = sent(client, id=row, category="Casa", remember="rows", change="1")
    response = client.post("/months/Lukas/2026-08/categorize", data=form)
    assert response.status_code == 409
    assert "No category was set: unknown category &#39;Casa&#39;." in response.text
    assert category_of(database, row) == ("Giroconto", "rule")
    gone = client.post("/months/Lukas/2026-08/categorize", data=form | {"id": "gone"})
    assert "those rows cannot be given another category" in gone.text
    forged = client.post("/months/Lukas/2026-08/categorize", data=form | {"token": "x"})
    assert forged.status_code == 403
    assert (
        client.post("/months/Zoe/2026-08/categorize", data=form | {"owner": "Zoe"}).status_code
        == 404
    )


def test_a_category_to_remember_is_saved_as_a_rule(database: Path, tmp_path: Path) -> None:
    rules = webapp.Rules(tmp_path / "rules.local.yaml", PEOPLE)
    client = TestClient(webapp.create_app(database, rules=rules), base_url=SITE)
    page = client.get("/months/Lukas/2026-08").text
    assert '<input type="radio" name="remember" value="rule" checked>' in page
    row = row_id(database, "Lukas", "SAVINGS TOPUP AUG")
    form = sent(client, id=row, category="Regali", remember="rule", change="1")
    assert (
        client.post(
            "/months/Lukas/2026-08/categorize", data=form, follow_redirects=False
        ).status_code
        == 303
    )
    assert category_of(database, row) == ("Regali", "manual")
    assert rules.shared.read_text(encoding="utf-8").startswith("rules:\n")


def test_a_category_is_added_from_the_page_and_can_then_be_chosen(
    database: Path, tmp_path: Path
) -> None:
    rules = webapp.Rules(tmp_path / "rules.local.yaml", PEOPLE)
    app = webapp.create_app(database, rules=rules)
    client = TestClient(app, base_url=SITE, follow_redirects=False)
    page = client.get("/months/Lukas/2026-08").text
    assert "<h2>Add a category</h2>" in page
    assert '<option value="uscite:Casa">Expenses, Casa</option>' in page
    assert '<option value="Animali"' not in page
    form = sent(client, name="Animali", under="uscite:Casa", owner="Lukas")
    response = client.post("/months/Lukas/2026-08/category", data=form)
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/months/Lukas/2026-08"
    assert (
        '<option value="Animali" label="Casa" data-side="uscite">'
        in client.get("/months/Lukas/2026-08").text
    )
    added = (tmp_path / "categories.yaml").read_text(encoding="utf-8")
    assert added == "categories:\n  uscite:\n    Casa:\n    - Animali\n  entrate: []\n"
    again = client.post("/months/Lukas/2026-08/category", data=form)
    assert again.status_code == 409
    assert "No category was added: there is already a category &#39;Animali&#39;." in again.text


def test_without_a_rules_file_no_category_can_be_added(client: TestClient) -> None:
    page = client.get("/months/Lukas/2026-08").text
    assert "Add a category" not in page
    form = sent(client, name="Animali", under="entrate:")
    assert client.post("/months/Lukas/2026-08/category", data=form).status_code == 404


def test_a_form_without_the_token_of_these_pages_changes_nothing(
    client: TestClient, database: Path
) -> None:
    row = row_id(database, "Lukas", "SAVINGS TOPUP AUG")
    url = "/months/Lukas/2026-08/categorize"
    fields = {"id": row, "category": "Regali", "change": "1"}
    assert client.post(url).status_code == 403
    assert client.post(url, data={"token": "guessed"}).status_code == 403
    good = sent(client, **fields)
    elsewhere = client.post(url, data=good, headers={"origin": "https://evil.example"})
    assert elsewhere.status_code == 403
    assert category_of(database, row) == ("Giroconto", "rule")
    form = {"content-type": "application/x-www-form-urlencoded"}
    assert client.post(url, content=b"token=\xff", headers=form).status_code == 400
    assert client.post(url, data=good, headers={"origin": SITE}).status_code == 303


def test_a_month_cannot_be_changed_by_following_a_link(client: TestClient) -> None:
    assert client.get("/months/Lukas/2026-08/categorize").status_code == 405
