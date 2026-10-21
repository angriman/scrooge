"""The draft on the import page: what is staged for a person, its totals by category, and
the forms that give categories, remove a statement and commit the rest."""

import re
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from finanze import staging
from finanze.models import CashBalance
from finanze.storage import db
from finanze.web import app as webapp
from tests.storage.helpers import PARTNER, PEOPLE, batch, statement, trade, txn
from tests.web.builders import (
    BUILT,
    CLICKED,
    SITE,
    category_of,
    committed_files,
    file_id,
    household,
    row_id,
    stage_rows,
    token,
)
from tests.web.test_app import text
from tests.web.test_cashflow_page import tables

WEB = Path(webapp.__file__).parent


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "finanze.db"
    household(path)
    return path


@pytest.fixture
def client(database: Path) -> TestClient:
    app = webapp.create_app(database, clock=lambda: CLICKED)
    return TestClient(app, base_url=SITE, follow_redirects=False)


def bare(page: str) -> str:
    """The page without its category menus, which hold every category of every row."""
    return re.sub(r"<select.*?</select>", "[menu]", page, flags=re.DOTALL)


def draft(page: str) -> list[list[list[str]]]:
    return tables(bare(page))


def form(client: TestClient, owner: str = "Lukas", **fields: str) -> dict[str, str]:
    """What every form of the page sends back, and the fields of this one."""
    return {"token": client.app.state.token, "owner": owner} | fields  # type: ignore[attr-defined]  # the Starlette app


def test_the_draft_lists_the_staged_statements_and_what_stops_the_commit(
    client: TestClient, database: Path
) -> None:
    page = client.get("/import", params={"owner": "Lukas"}).text
    files, totals, waiting, rows = draft(page)
    assert files == [
        [
            "Migros Bank account statement",
            "migros.csv",
            "2026-09-01 to 2026-09-30",
            "3",
            "Remove",
        ]
    ]
    assert re.findall(r'<ul id="blocked">(.*?)</ul>', page, re.DOTALL) == [
        "\n    <li>1 transaction still needs a category</li>\n  "
    ]
    assert totals == [
        ["Spesa", "-12.50 CHF", "1"],
        ["Giroconto transfer", "-1,000.00 CHF", "1"],
        ["Waiting for a category", "-80.00 CHF", "1"],
    ]
    assert [row[:2] for row in waiting] == [
        ["&lt;i&gt;Kiosk&lt;/i&gt; paid · KONSUM", "2026-09-05 · Migros · -80.00 CHF"]
    ]
    assert [row[:4] for row in rows] == [
        ["2026-09-04", "Migros", "Bakery KONSUM statement", "-12.50 CHF"],
        ["2026-09-05", "Migros", "&lt;i&gt;Kiosk&lt;/i&gt; KONSUM statement", "-80.00 CHF"],
        ["2026-09-20", "Migros", "SAVINGS TOPUP statement", "-1,000.00 CHF"],
    ]
    assert [row[4] for row in rows] == ["[menu]"] * 3
    statement_id = file_id(database, "Lukas", committed=False)
    assert f'<a href="{SITE}/statements/{statement_id}">migros.csv</a>' in page
    assert f'<form method="post" action="{SITE}/statements/{statement_id}/discard">' in page


def test_each_person_has_a_draft_of_their_own(client: TestClient) -> None:
    anna = client.get("/import", params={"owner": "Anna"}).text
    files, totals, rows = draft(anna)
    assert [row[1] for row in files] == ["migros.csv"]
    assert totals == [["Spesa", "-60.00 CHF", "1"]]
    assert len(rows) == 1
    assert "Nothing stands in the way. A copy of the database is saved first." in text(anna)
    assert 'id="blocked"' not in anna
    assert "Commit 1 statement</button>" in anna


def test_a_row_a_statement_categorizes_itself_has_no_menu(tmp_path: Path) -> None:
    path = tmp_path / "finanze.db"
    conn = db.connect(path)
    db.migrate(conn, BUILT)
    rows = [
        trade("-1012.50", "WIDG", "10"),
        txn("-5", source_file="ibkr.csv", account="IBKR USD", currency="USD"),
    ]
    stage_rows(conn, "Lukas", (date(2026, 9, 1), date(2026, 9, 30)), ("ibkr", rows))
    conn.close()
    page = TestClient(webapp.create_app(path), base_url=SITE).get("/import?owner=Lukas").text
    shown = draft(page)[-1]
    assert [(row[3], row[4]) for row in shown] == [
        ("-5.00 USD", "[menu]"),
        ("-1,012.50 USD", "Investimenti"),
    ]
    assert page.count("<select name=") == 1


def test_the_menu_of_a_row_that_has_a_category_keeps_it_and_the_waiting_one_asks(
    client: TestClient, database: Path
) -> None:
    page = client.get("/import", params={"owner": "Lukas"}).text
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    bakery = row_id(database, "Lukas", "Bakery")
    assert (
        f'<select name="category:{kiosk}" aria-label="Category of &lt;i&gt;Kiosk&lt;/i&gt;">'
        in page
    )
    assert re.search(
        rf'name="category:{kiosk}".*?<option value="">Choose</option>', page, re.DOTALL
    )
    assert re.search(
        rf'name="category:{bakery}".*?<option value="">Spesa</option>', page, re.DOTALL
    )
    assert '<option data-side="uscite">Affitto</option>' in page
    assert '<option data-side="entrate">Stipendio</option>' in page
    assert '<option data-side="transfer">Giroconto</option>' in page
    assert "<option>Casa</option>" not in page


def test_the_page_has_what_the_live_totals_work_from(client: TestClient, database: Path) -> None:
    page = client.get("/import", params={"owner": "Lukas"}).text
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    assert f'<script src="{SITE}/static/draft.js" defer></script>' in page
    assert re.findall(r"<script[^>]*>", page) == [f'<script src="{SITE}/static/draft.js" defer>']
    assert not re.search(r"\son[a-z]+=", page)  # no inline handler: the policy allows none
    assert 'style="' not in page
    assert '<section id="rows">' in page
    assert '<section id="totals">' in page
    assert (
        f'<tr data-id="{kiosk}" data-amount="-80" data-currency="CHF" data-side="" '
        'data-category="">'
    ) in page
    assert '<tr data-side="" data-category="" data-currency="CHF">' in page  # the waiting line
    assert '<tr data-side="uscite" data-category="Spesa" data-currency="CHF">' in page
    assert '<tr data-side="transfer" data-category="Giroconto" data-currency="CHF">' in page
    assert re.search(r'<td class="number total sum" data-unit="CHF">-80.00 CHF</td>', page)
    assert re.search(r'<td class="number count" data-label="Rows">1</td>', page)
    assert 'id="draft-form"' in page


def test_the_script_counts_in_billionths_and_loads_nothing() -> None:
    script = (WEB / "static" / "draft.js").read_text(encoding="utf-8")
    assert "BigInt" in script
    assert "parseFloat" not in script
    assert "Number(" not in script
    assert not re.search(r"fetch|XMLHttpRequest|eval\(|innerHTML|https?://", script)
    assert (WEB / "static" / "draft.js").is_file()


def test_the_stylesheet_hides_what_the_script_hides() -> None:
    css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
    assert "[hidden] {\n  display: none !important;\n}" in css
    assert "tr.changed" in css


def test_a_server_answers_the_script_with_a_script(client: TestClient) -> None:
    response = client.get("/static/draft.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_the_page_works_without_the_script_save_categories_applies_the_choices(
    client: TestClient, database: Path
) -> None:
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    transfer = row_id(database, "Lukas", "SAVINGS TOPUP")
    assert category_of(database, transfer) == ("Giroconto", "rule")
    fields = form(client)
    wrong = row_id(database, "Lukas", "Bakery")
    sent = fields | {f"category:{kiosk}": "Regali", f"category:{wrong}": "Spesa"}
    response = client.post("/import/save", data=sent)
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/import?owner=Lukas"
    assert category_of(database, kiosk) == ("Regali", "manual")
    assert category_of(database, wrong) == ("Spesa", "rule")  # chosen again: untouched
    after = client.get("/import", params={"owner": "Lukas"}).text
    assert draft(after)[1] == [
        ["Spesa", "-12.50 CHF", "1"],
        ["Regali", "-80.00 CHF", "1"],
        ["Giroconto transfer", "-1,000.00 CHF", "1"],
    ]
    assert 'id="blocked"' not in after


def test_a_category_that_was_wrong_is_changed_as_a_change_not_as_a_first_choice(
    client: TestClient, database: Path
) -> None:
    bakery = row_id(database, "Lukas", "Bakery")
    sent = form(client) | {f"category:{bakery}": "Regali"}
    assert client.post("/import/save", data=sent).status_code == 303
    assert category_of(database, bakery) == ("Regali", "manual")
    conn = sqlite3.connect(database)
    (override,) = conn.execute("SELECT count(*) FROM overrides").fetchone()
    conn.close()
    assert override == 1


def test_rows_going_to_one_category_are_one_change(client: TestClient, database: Path) -> None:
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    bakery = row_id(database, "Lukas", "Bakery")
    sent = form(client) | {f"category:{kiosk}": "Regali", f"category:{bakery}": "Regali"}
    # One waiting and one categorized row are two calls: the first choice and a change.
    assert client.post("/import/save", data=sent).status_code == 303
    assert category_of(database, kiosk) == ("Regali", "manual")
    assert category_of(database, bakery) == ("Regali", "manual")


@pytest.mark.parametrize(
    ("category", "message"),
    [
        ("Casa", "No category was set: unknown category &#39;Casa&#39;."),
        ("Nothing", "No category was set: unknown category &#39;Nothing&#39;."),
    ],
)
def test_a_choice_that_cannot_be_made_is_said_and_nothing_changes(
    client: TestClient, database: Path, category: str, message: str
) -> None:
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    response = client.post("/import/save", data=form(client) | {f"category:{kiosk}": category})
    assert response.status_code == 409
    assert message in response.text
    assert category_of(database, kiosk) == (None, None)
    assert "<form" in response.text  # the draft is still there to be worked on


def test_a_row_that_is_not_staged_any_more_is_said(client: TestClient, database: Path) -> None:
    sent = form(client) | {"category:gone": "Regali"}
    response = client.post("/import/save", data=sent)
    assert response.status_code == 409
    assert "those rows are not staged any more: load the page again" in text(response.text)


def test_choosing_nothing_saves_nothing(client: TestClient) -> None:
    assert client.post("/import/save", data=form(client)).status_code == 303


def test_commit_gives_the_chosen_categories_then_commits_with_a_copy_from_before(
    client: TestClient, database: Path
) -> None:
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    sent = form(client) | {f"category:{kiosk}": "Regali"}
    response = client.post("/import/commit", data=sent)
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/import?owner=Lukas&committed=1"
    assert committed_files(database) == ["Lukas migros.csv", "Lukas migros.csv"]
    assert category_of(database, kiosk) == ("Regali", "manual")
    assert [copy.name for copy in (database.parent / "backups").iterdir()] == [
        "finanze-20261012T093000.db"
    ]
    done = client.get(response.headers["location"])
    assert "1 statement committed: the other pages show them now." in text(done.text)
    assert "Nothing is staged for Lukas." in text(done.text)
    # The rest of the site follows at once.
    shown = client.get("/transactions", params={"owner": "Lukas", "category": "Regali"}).text
    assert "Kiosk" in shown


def test_anna_is_committed_without_touching_lukas(client: TestClient, database: Path) -> None:
    response = client.post("/import/commit", data=form(client, "Anna"))
    assert response.status_code == 303
    assert committed_files(database) == ["Anna migros.csv", "Lukas migros.csv"]
    assert "Lukas migros.csv" in committed_files(database)
    assert "migros.csv" in text(client.get("/import", params={"owner": "Lukas"}).text)


def test_a_commit_that_cannot_be_made_is_refused_and_says_why(
    client: TestClient, database: Path
) -> None:
    before = committed_files(database)
    response = client.post("/import/commit", data=form(client))
    assert response.status_code == 409
    assert "Not committed: 1 staged transactions still need a category." in text(response.text)
    assert response.text.count('role="alert"') == 1
    assert committed_files(database) == before
    assert not list((database.parent / "backups").iterdir())


def test_a_commit_whose_choice_cannot_be_given_commits_nothing(
    client: TestClient, database: Path
) -> None:
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    response = client.post("/import/commit", data=form(client) | {f"category:{kiosk}": "Casa"})
    assert response.status_code == 409
    assert "No category was set: unknown category" in text(response.text)
    assert committed_files(database) == ["Lukas migros.csv"]


def test_a_reconciliation_failure_cannot_be_committed_from_the_page(
    client: TestClient, database: Path
) -> None:
    # A later statement of Anna's bank starts from a balance no statement leads to.
    later = statement(
        "migros",
        [txn("-1", date=date(2026, 9, 25), source_file="later.csv")],
        [CashBalance("Migros", "CHF", Decimal(100), Decimal(99))],
        month=date(2026, 9, 20),
        end=date(2026, 9, 30),
        filename="later.csv",
    )
    conn = db.connect(database)
    staging.stage(conn, batch(later, owner=PARTNER, month=date(2026, 9, 20)), BUILT)
    conn.close()
    page = client.get("/import", params={"owner": "Anna"}).text
    assert 'id="blocked"' in page
    response = client.post("/import/commit", data=form(client, "Anna"))
    assert response.status_code == 409
    assert "Not committed: " in text(response.text)
    assert "Anna" not in " ".join(committed_files(database))
    assert not list((database.parent / "backups").iterdir())


def test_a_commit_with_nothing_staged_says_so(client: TestClient, database: Path) -> None:
    assert client.post("/import/commit", data=form(client, "Anna")).status_code == 303
    again = client.post("/import/commit", data=form(client, "Anna"))
    assert again.headers["location"] == f"{SITE}/import?owner=Anna&committed=0"
    assert "0 statements committed." in text(client.get(again.headers["location"]).text)
    assert [copy.name for copy in (database.parent / "backups").iterdir()] == [
        "finanze-20261012T093000.db"
    ]


def test_the_waiting_rows_are_asked_about_by_counterparty(
    client: TestClient, database: Path
) -> None:
    page = client.get("/import", params={"owner": "Lukas"}).text
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    assert "<h2>Need a category</h2>" in page
    assert f'<input type="hidden" name="id" value="{kiosk}">' in page
    assert 'action="http://127.0.0.1:8765/import/group"' in page
    assert 'type="radio"' not in page
    assert "For these rows only." in page
    sent = form(client) | {"id": kiosk, "category": "Regali", "remember": "rule"}
    response = client.post("/import/group", data=sent)
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/import?owner=Lukas"
    assert category_of(database, kiosk) == ("Regali", "manual")  # no rules file: not remembered
    assert "Need a category" not in client.get("/import", params={"owner": "Lukas"}).text


def test_a_category_to_remember_is_saved_as_a_rule(database: Path, tmp_path: Path) -> None:
    rules = webapp.Rules(tmp_path / "rules.local.yaml", PEOPLE)
    client = TestClient(webapp.create_app(database, rules=rules), base_url=SITE)
    page = client.get("/import", params={"owner": "Lukas"}).text
    assert '<input type="radio" name="remember" value="rule" checked>' in page
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    sent = form(client) | {"id": kiosk, "category": "Regali", "remember": "rule"}
    assert client.post("/import/group", data=sent, follow_redirects=False).status_code == 303
    assert category_of(database, kiosk) == ("Regali", "rule")
    assert rules.shared.read_text(encoding="utf-8") == (
        'rules:\n  - {match: "(?m)^<i>Kiosk</i>$", category: "Regali", direction: out}\n'
    )


def test_a_group_choice_that_cannot_be_made_is_said(client: TestClient, database: Path) -> None:
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    base = form(client) | {"id": kiosk, "remember": "rows"}
    empty = client.post("/import/group", data=base | {"category": ""})
    assert empty.status_code == 409
    assert "No category was set: no category was chosen." in text(empty.text)
    unknown = client.post("/import/group", data=base | {"category": "Casa"})
    assert "No category was set: unknown category &#39;Casa&#39;." in unknown.text
    gone = client.post("/import/group", data=base | {"category": "Regali", "id": "gone"})
    assert "those rows are no longer waiting" in gone.text
    assert category_of(database, kiosk) == (None, None)
    nobody = client.post("/import/group", data=form(client) | {"owner": "Zoe", "id": "x"})
    assert nobody.status_code == 404


def test_a_category_is_added_from_the_import_page_and_can_then_be_chosen(
    database: Path, tmp_path: Path
) -> None:
    rules = webapp.Rules(tmp_path / "rules.local.yaml", PEOPLE)
    client = TestClient(webapp.create_app(database, rules=rules), base_url=SITE)
    client.follow_redirects = False
    page = client.get("/import", params={"owner": "Lukas"}).text
    assert "<h2>Add a category</h2>" in page
    assert '<option value="uscite:Casa">Expenses, Casa</option>' in page
    assert '<option data-side="uscite">Animali</option>' not in page
    sent = form(client) | {"name": "Animali", "under": "uscite:Casa"}
    response = client.post("/import/category", data=sent)
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/import?owner=Lukas"
    assert (
        '<option data-side="uscite">Animali</option>'
        in client.get("/import", params={"owner": "Lukas"}).text
    )
    added = (tmp_path / "categories.yaml").read_text(encoding="utf-8")
    assert added == "categories:\n  uscite:\n    Casa:\n    - Animali\n  entrate: []\n"
    again = client.post("/import/category", data=sent)
    assert again.status_code == 409
    assert "No category was added: there is already a category &#39;Animali&#39;." in again.text


def test_without_a_rules_file_no_category_can_be_added(client: TestClient) -> None:
    page = client.get("/import", params={"owner": "Lukas"}).text
    assert "Add a category" not in page
    sent = form(client) | {"name": "Animali", "under": "entrate:"}
    assert client.post("/import/category", data=sent).status_code == 404


def test_a_statement_is_removed_from_the_draft(client: TestClient, database: Path) -> None:
    number = file_id(database, "Anna", committed=False)
    page = client.get("/import", params={"owner": "Anna"}).text
    response = client.post(f"/statements/{number}/discard", data={"token": token(page)})
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/import?owner=Anna"
    assert "Nothing is staged for Anna." in text(client.get(response.headers["location"]).text)
    conn = sqlite3.connect(database)
    (left,) = conn.execute("SELECT count(*) FROM statement_files").fetchone()
    conn.close()
    assert left == 2


@pytest.mark.parametrize(
    "path", ["/import/save", "/import/commit", "/import/group", "/import/category"]
)
def test_a_form_without_the_token_of_these_pages_changes_nothing(
    client: TestClient, database: Path, path: str
) -> None:
    before = committed_files(database)
    assert client.post(path).status_code == 403
    assert client.post(path, data={"token": "guessed"}).status_code == 403
    good = {"token": token(client.get("/import").text), "owner": "Anna"}
    elsewhere = client.post(path, data=good, headers={"origin": "https://evil.example"})
    assert elsewhere.status_code == 403
    assert committed_files(database) == before
    form_type = {"content-type": "application/x-www-form-urlencoded"}
    assert client.post(path, content=b"token=\xff", headers=form_type).status_code == 400


def test_a_browser_that_withholds_the_origin_can_still_send_the_forms(client: TestClient) -> None:
    # `referrer-policy: no-referrer` makes a browser send `Origin: null` with these forms.
    page = client.get("/import")
    assert page.headers["referrer-policy"] == "no-referrer"
    sent = {"token": token(page.text), "owner": "Lukas"}
    assert client.post("/import/save", data=sent, headers={"origin": "null"}).status_code == 303
    assert client.post("/import/save", data=sent, headers={"origin": SITE}).status_code == 303


def test_a_draft_cannot_be_changed_by_following_a_link(client: TestClient) -> None:
    assert client.get("/import/commit").status_code == 405
    assert client.get("/import/save").status_code == 405
