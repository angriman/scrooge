"""The draft on the import page: what is staged for a person, its totals by category, and
the forms that give categories, remove a statement and commit the rest."""

import re
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from scrooge import categories, staging
from scrooge.models import CashBalance
from scrooge.storage import db
from scrooge.web import app as webapp
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
from tests.web.test_analysis_cashflow import tables
from tests.web.test_app import text

WEB = Path(webapp.__file__).parent


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "scrooge.db"
    household(path)
    return path


@pytest.fixture
def client(database: Path) -> TestClient:
    app = webapp.create_app(database, clock=lambda: CLICKED)
    return TestClient(app, base_url=SITE, follow_redirects=False)


def bare(page: str) -> str:
    """The page with each category field as [menu], without the list of every category."""
    page = re.sub(r"<datalist.*?</datalist>", "", page, flags=re.DOTALL)
    return re.sub(r'<input [^>]*list="categories"[^>]*>', "[menu]", page)


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
        ["Groceries", "-12.50 CHF", "1"],
        ["Own transfer transfer", "-1,000.00 CHF", "1"],
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
    assert totals == [["Groceries", "-60.00 CHF", "1"]]
    assert len(rows) == 1
    assert "Nothing stands in the way. A copy of the database is saved first." in text(anna)
    assert 'id="blocked"' not in anna
    assert "Commit 1 statement</button>" in anna


def test_a_row_a_statement_categorizes_itself_has_no_menu(tmp_path: Path) -> None:
    path = tmp_path / "scrooge.db"
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
        ("-1,012.50 USD", "Investments"),
    ]
    assert page.count('name="category:') == 1


def test_the_menu_of_a_row_that_has_a_category_keeps_it_and_the_waiting_one_asks(
    client: TestClient, database: Path
) -> None:
    page = client.get("/import", params={"owner": "Lukas"}).text
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    bakery = row_id(database, "Lukas", "Bakery")
    assert (
        f'<input name="category:{kiosk}" list="categories" placeholder="Choose" '
        'autocomplete="off" aria-label="Category of &lt;i&gt;Kiosk&lt;/i&gt;">'
    ) in page
    assert f'<input name="category:{bakery}" list="categories" placeholder="Groceries" ' in page
    # One list of the categories, which every field suggests from as a name is typed.
    assert page.count('<datalist id="categories">') == 1
    assert '<option value="Rent" label="Home" data-side="expenses">' in page
    assert '<option value="Salary" label="Income" data-side="income">' in page
    assert '<option value="Own transfer" label="Transfer" data-side="transfer">' in page
    assert '<option value="Home"' not in page
    assert (
        "<select name=" not in page.partition("<main>")[2].partition("<h2>Add a category</h2>")[0]
    )


def test_the_page_has_what_the_live_totals_work_from(client: TestClient, database: Path) -> None:
    page = client.get("/import", params={"owner": "Lukas"}).text
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    assert re.findall(r"<script[^>]*>", page) == [
        f'<script src="{SITE}/static/choose.js" defer>',
        f'<script src="{SITE}/static/picker.js" defer>',
        f'<script src="{SITE}/static/draft.js" defer>',
    ]
    assert not re.search(r"\son[a-z]+=", page)  # no inline handler: the policy allows none
    assert 'style="' not in page
    assert '<section id="rows">' in page
    assert '<section id="totals">' in page
    assert (
        f'<tr data-id="{kiosk}" data-amount="-80" data-currency="CHF" data-side="" '
        'data-category="" data-group="0">'
    ) in page
    assert f'<input type="hidden" name="ids:0" value="{kiosk}" form="draft-form">' in page
    assert '<tr data-side="" data-category="" data-currency="CHF">' in page  # the waiting line
    assert '<tr data-side="expenses" data-category="Groceries" data-currency="CHF">' in page
    assert '<tr data-side="transfer" data-category="Own transfer" data-currency="CHF">' in page
    assert re.search(r'<td class="number total sum" data-unit="CHF">-80.00 CHF</td>', page)
    assert re.search(r'<td class="number count" data-label="Rows">1</td>', page)
    assert 'id="draft-form"' in page


def test_the_script_counts_in_billionths_and_loads_nothing() -> None:
    script = (WEB / "static" / "draft.js").read_text(encoding="utf-8")
    assert "BigInt" in script
    assert "parseFloat" not in script
    assert "Number(" not in script
    assert not re.search(r"fetch|XMLHttpRequest|eval\(|innerHTML|https?://", script)
    picker = (WEB / "static" / "picker.js").read_text(encoding="utf-8")
    assert "setCustomValidity" in picker
    assert not re.search(r"fetch|XMLHttpRequest|eval\(|innerHTML|https?://", picker)


def test_the_stylesheet_hides_what_the_script_hides() -> None:
    css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
    assert "[hidden] {\n  display: none !important;\n}" in css
    assert "tr.changed" in css


@pytest.mark.parametrize("script", ["draft.js", "picker.js"])
def test_a_server_answers_the_script_with_a_script(client: TestClient, script: str) -> None:
    response = client.get(f"/static/{script}")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_the_page_works_without_the_script_save_categories_applies_the_choices(
    client: TestClient, database: Path
) -> None:
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    transfer = row_id(database, "Lukas", "SAVINGS TOPUP")
    assert category_of(database, transfer) == (categories.OWN_TRANSFER, "rule")
    fields = form(client)
    wrong = row_id(database, "Lukas", "Bakery")
    sent = fields | {
        f"category:{kiosk}": categories.GIFTS,
        f"category:{wrong}": categories.GROCERIES,
    }
    response = client.post("/import/save", data=sent)
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/import?owner=Lukas"
    assert category_of(database, kiosk) == (categories.GIFTS, "manual")
    assert category_of(database, wrong) == (categories.GROCERIES, "rule")  # chosen again: untouched
    after = client.get("/import", params={"owner": "Lukas"}).text
    assert draft(after)[1] == [
        ["Groceries", "-12.50 CHF", "1"],
        ["Gifts", "-80.00 CHF", "1"],
        ["Own transfer transfer", "-1,000.00 CHF", "1"],
    ]
    assert 'id="blocked"' not in after


def test_a_category_that_was_wrong_is_changed_as_a_change_not_as_a_first_choice(
    client: TestClient, database: Path
) -> None:
    bakery = row_id(database, "Lukas", "Bakery")
    sent = form(client) | {f"category:{bakery}": categories.GIFTS}
    assert client.post("/import/save", data=sent).status_code == 303
    assert category_of(database, bakery) == (categories.GIFTS, "manual")
    conn = sqlite3.connect(database)
    (override,) = conn.execute("SELECT count(*) FROM overrides").fetchone()
    conn.close()
    assert override == 1


def test_rows_going_to_one_category_are_one_change(client: TestClient, database: Path) -> None:
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    bakery = row_id(database, "Lukas", "Bakery")
    sent = form(client) | {
        f"category:{kiosk}": categories.GIFTS,
        f"category:{bakery}": categories.GIFTS,
    }
    # One waiting and one categorized row are two calls: the first choice and a change.
    assert client.post("/import/save", data=sent).status_code == 303
    assert category_of(database, kiosk) == (categories.GIFTS, "manual")
    assert category_of(database, bakery) == (categories.GIFTS, "manual")


@pytest.mark.parametrize(
    ("category", "message"),
    [
        ("Home", "No category was set: unknown category &#39;Home&#39;."),
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


def test_a_row_that_is_not_staged_any_more_is_said(client: TestClient) -> None:
    sent = form(client) | {"category:gone": "Gifts"}
    response = client.post("/import/save", data=sent)
    assert response.status_code == 409
    assert "those rows are not staged any more: load the page again" in text(response.text)


def test_choosing_nothing_saves_nothing(client: TestClient) -> None:
    assert client.post("/import/save", data=form(client)).status_code == 303


def test_commit_gives_the_chosen_categories_then_commits_with_a_copy_from_before(
    client: TestClient, database: Path
) -> None:
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    sent = form(client) | {f"category:{kiosk}": categories.GIFTS}
    response = client.post("/import/commit", data=sent)
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/import?owner=Lukas&committed=1"
    assert committed_files(database) == ["Lukas migros.csv", "Lukas migros.csv"]
    assert category_of(database, kiosk) == (categories.GIFTS, "manual")
    assert [copy.name for copy in (database.parent / "backups").iterdir()] == [
        "scrooge-20261012T093000.db"
    ]
    done = client.get(response.headers["location"])
    assert "1 statement committed: the other pages show them now." in text(done.text)
    assert "Nothing is staged for Lukas." in text(done.text)
    # The rest of the site follows at once.
    shown = client.get(
        "/transactions", params={"owner": "Lukas", "category": categories.GIFTS}
    ).text
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
    response = client.post(
        "/import/commit", data=form(client) | {f"category:{kiosk}": categories.GROUP_HOME}
    )
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
        "scrooge-20261012T093000.db"
    ]


def test_the_waiting_rows_are_asked_about_by_counterparty_and_set_with_one_button(
    client: TestClient, database: Path
) -> None:
    page = client.get("/import", params={"owner": "Lukas"}).text
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    assert "<h2>Need a category</h2>" in page
    assert (
        '<input name="group:0" list="categories" placeholder="Choose" autocomplete="off" '
        'aria-label="Category for &lt;i&gt;Kiosk&lt;/i&gt;" form="draft-form">'
    ) in page
    assert 'type="radio"' not in page
    assert "For these rows only." in page
    # Its fields belong to the form of the whole draft: one button sends every choice.
    assert "/import/group" not in page
    assert page.count(">Set</button>") == 0
    assert page.count('<button form="draft-form">Set categories</button>') == 1
    sent = form(client) | {"ids:0": kiosk, "group:0": categories.GIFTS, "remember:0": "rule"}
    response = client.post("/import/save", data=sent)
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/import?owner=Lukas"
    # no rules file: not remembered
    assert category_of(database, kiosk) == (categories.GIFTS, "manual")
    assert "Need a category" not in client.get("/import", params={"owner": "Lukas"}).text


def test_a_category_to_remember_is_saved_as_a_rule(database: Path, tmp_path: Path) -> None:
    rules = webapp.Rules(tmp_path / "rules.local.yaml", PEOPLE)
    client = TestClient(webapp.create_app(database, rules=rules), base_url=SITE)
    page = client.get("/import", params={"owner": "Lukas"}).text
    assert '<input type="radio" name="remember:0" value="rule" checked form="draft-form">' in page
    assert '<input type="radio" name="remember:0" value="rows" form="draft-form">' in page
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    sent = form(client) | {"ids:0": kiosk, "group:0": " Gifts ", "remember:0": "rule"}
    assert client.post("/import/save", data=sent, follow_redirects=False).status_code == 303
    assert category_of(database, kiosk) == (categories.GIFTS, "rule")
    assert rules.shared.read_text(encoding="utf-8") == (
        'rules:\n  - {match: "(?m)^<i>Kiosk</i>$", category: "Gifts", direction: out}\n'
    )


def test_a_group_left_empty_is_left_waiting(client: TestClient, database: Path) -> None:
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    sent = form(client) | {"ids:0": kiosk, "group:0": "", "remember:0": "rule"}
    assert client.post("/import/save", data=sent).status_code == 303
    assert category_of(database, kiosk) == (None, None)


def test_a_row_chosen_on_its_own_takes_its_choice_over_its_groups(
    database: Path, tmp_path: Path
) -> None:
    rules = webapp.Rules(tmp_path / "rules.local.yaml", PEOPLE)
    client = TestClient(webapp.create_app(database, rules=rules), base_url=SITE)
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    sent = form(client) | {
        "ids:0": kiosk,
        "group:0": categories.GIFTS,
        "remember:0": "rule",
        f"category:{kiosk}": categories.GROCERIES,
    }
    assert client.post("/import/save", data=sent, follow_redirects=False).status_code == 303
    assert category_of(database, kiosk) == (categories.GROCERIES, "manual")
    assert not rules.shared.exists()  # the group had no row left to give its category to


def test_every_choice_of_one_button_is_given_or_none_is(client: TestClient, database: Path) -> None:
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    bakery = row_id(database, "Lukas", "Bakery")
    sent = form(client) | {
        "ids:0": kiosk,
        "group:0": categories.GIFTS,
        f"category:{bakery}": categories.label(categories.GROUP_HOME),  # as the form sends it
    }
    response = client.post("/import/save", data=sent)
    assert response.status_code == 409
    assert "No category was set: unknown category &#39;Home&#39;." in response.text
    assert category_of(database, kiosk) == (None, None)
    assert category_of(database, bakery) == (categories.GROCERIES, "rule")
    # Both at once, then: the group's first choice and the row's change.
    sent[f"category:{bakery}"] = "Gifts"
    assert client.post("/import/save", data=sent).status_code == 303
    assert category_of(database, kiosk) == (categories.GIFTS, "manual")
    assert category_of(database, bakery) == (categories.GIFTS, "manual")


def test_a_group_choice_that_cannot_be_made_is_said(client: TestClient, database: Path) -> None:
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    base = form(client) | {"ids:0": kiosk, "remember:0": "rows"}
    unknown = client.post(
        "/import/save", data=base | {"group:0": categories.label(categories.GROUP_HOME)}
    )
    assert unknown.status_code == 409
    assert "No category was set: unknown category &#39;Home&#39;." in unknown.text
    gone = client.post("/import/save", data=base | {"group:0": categories.GIFTS, "ids:0": "gone"})
    assert gone.status_code == 409
    assert "those rows are not staged any more: load the page again" in text(gone.text)
    nothing = client.post("/import/save", data=form(client) | {"group:0": categories.GIFTS})
    assert nothing.status_code == 409
    assert "those rows are not staged any more" in text(nothing.text)
    assert category_of(database, kiosk) == (None, None)
    nobody = form(client) | {"owner": "Zoe", "ids:0": "x", "group:0": categories.GIFTS}
    assert client.post("/import/save", data=nobody).status_code == 404


def test_a_category_is_added_from_the_import_page_and_can_then_be_chosen(
    database: Path, tmp_path: Path
) -> None:
    rules = webapp.Rules(tmp_path / "rules.local.yaml", PEOPLE)
    client = TestClient(webapp.create_app(database, rules=rules), base_url=SITE)
    client.follow_redirects = False
    page = client.get("/import", params={"owner": "Lukas"}).text
    assert "<h2>Add a category</h2>" in page
    assert '<option value="expenses:Home">Expenses, Home</option>' in page
    assert '<option value="Pets"' not in page
    sent = form(client) | {"name": "Pets", "under": "expenses:Home"}
    response = client.post("/import/category", data=sent)
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/import?owner=Lukas"
    assert (
        '<option value="Pets" label="Home" data-side="expenses">'
        in client.get("/import", params={"owner": "Lukas"}).text
    )
    added = (tmp_path / "categories.yaml").read_text(encoding="utf-8")
    assert added == "categories:\n  expenses:\n    Home:\n    - Pets\n  income: []\n"
    again = client.post("/import/category", data=sent)
    assert again.status_code == 409
    assert "No category was added: there is already a category &#39;Pets&#39;." in again.text


def test_without_a_rules_file_no_category_can_be_added(client: TestClient) -> None:
    page = client.get("/import", params={"owner": "Lukas"}).text
    assert "Add a category" not in page
    sent = form(client) | {"name": "Pets", "under": "income:"}
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


@pytest.mark.parametrize("path", ["/import/save", "/import/commit", "/import/category"])
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
    assert client.get("/import/rule").status_code == 405


def remembering(database: Path, tmp_path: Path) -> tuple[TestClient, webapp.Rules]:
    rules = webapp.Rules(tmp_path / "rules.yaml", PEOPLE)
    app = webapp.create_app(database, clock=lambda: CLICKED, rules=rules)
    return TestClient(app, base_url=SITE, follow_redirects=False), rules


def suggested(page: str) -> str:
    found = re.search(r'<section id="suggested">.*?</section>', page, re.DOTALL)
    return found[0] if found else ""


def test_a_category_changed_by_hand_is_offered_as_a_rule_that_is_added_on_request(
    database: Path, tmp_path: Path
) -> None:
    client, rules = remembering(database, tmp_path)
    bakery = row_id(database, "Lukas", "Bakery")
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")  # waiting: its first choice is no change
    sent = form(client) | {f"category:{bakery}": categories.GIFTS, f"category:{kiosk}": "Gifts"}
    response = client.post("/import/save", data=sent)
    assert response.headers["location"] == f"{SITE}/import?owner=Lukas&suggest={bakery}"
    offered = suggested(client.get(response.headers["location"]).text)
    assert not rules.shared.exists()  # offered, not written
    ((row,),) = tables(offered)
    assert row[:3] == ["Bakery", "paid", "Gifts"]
    assert row[3].startswith("rules.yaml, first")
    assert f'name="id" value="{bakery}"' in offered
    assert f'name="suggest" value="{bakery}"' in offered
    added = client.post("/import/rule", data=form(client, id=bakery, suggest=bakery))
    assert added.status_code == 303
    assert added.headers["location"] == f"{SITE}/import?owner=Lukas&suggest={bakery}&rule=1"
    written = rules.shared.read_text(encoding="utf-8")
    assert written.startswith("rules:\n  - {match: ")
    assert written.endswith('*(?:,.*)?$", category: "Gifts", direction: out}\n')
    assert "Bakery" in written
    after = client.get(added.headers["location"]).text
    assert "Rule added: the next import gives" in after
    assert not suggested(after)  # the rule gives it now: nothing more to offer
    again = client.post("/import/rule", data=form(client, id=bakery, suggest=bakery))
    assert again.status_code == 409
    assert "No rule was added: that rule is not needed any more" in again.text


def test_a_commit_offers_the_rules_of_its_changes_too(database: Path, tmp_path: Path) -> None:
    client, _ = remembering(database, tmp_path)
    bakery = row_id(database, "Lukas", "Bakery")
    kiosk = row_id(database, "Lukas", "<i>Kiosk</i>")
    sent = form(client) | {f"category:{bakery}": categories.GIFTS, f"category:{kiosk}": "Gifts"}
    response = client.post("/import/commit", data=sent)
    assert response.headers["location"] == (
        f"{SITE}/import?owner=Lukas&committed=1&suggest={bakery}"
    )
    assert suggested(client.get(response.headers["location"]).text)


def test_only_rows_named_as_ids_are_offered_rules(database: Path, tmp_path: Path) -> None:
    client, _ = remembering(database, tmp_path)
    bakery = row_id(database, "Lukas", "Bakery")
    sent = form(client) | {f"category:{bakery}": categories.GIFTS}
    assert client.post("/import/save", data=sent).status_code == 303
    for asked in ["", "nothing", bakery.upper(), "1" * 24]:
        page = client.get("/import", params={"owner": "Lukas", "suggest": asked}).text
        assert not suggested(page), asked
    twice = client.get("/import", params={"owner": "Lukas", "suggest": f"x,{bakery},{bakery}"})
    assert suggested(twice.text).count('name="id"') == 1


def test_a_rules_file_that_cannot_be_read_offers_nothing_and_says_why(
    database: Path, tmp_path: Path
) -> None:
    client, rules = remembering(database, tmp_path)
    bakery = row_id(database, "Lukas", "Bakery")
    sent = form(client) | {f"category:{bakery}": categories.GIFTS}
    assert client.post("/import/save", data=sent).status_code == 303
    rules.shared.write_text("rules: [\n", encoding="utf-8")
    page = client.get("/import", params={"owner": "Lukas", "suggest": bakery}).text
    assert not suggested(page)
    assert "No rule can be offered:" in page
    refused = client.post("/import/rule", data=form(client, id=bakery, suggest=bakery))
    assert refused.status_code == 409
    assert "No rule was added:" in refused.text


def test_without_a_rules_file_no_rule_is_offered_or_added(
    client: TestClient, database: Path
) -> None:
    bakery = row_id(database, "Lukas", "Bakery")
    sent = form(client) | {f"category:{bakery}": categories.GIFTS}
    assert client.post("/import/save", data=sent).status_code == 303
    page = client.get("/import", params={"owner": "Lukas", "suggest": bakery}).text
    assert not suggested(page)
    assert client.post("/import/rule", data=form(client, id=bakery)).status_code == 404


def test_a_copy_adds_no_rule(database: Path, tmp_path: Path) -> None:
    rules = webapp.Rules(tmp_path / "rules.yaml", PEOPLE)
    copy = TestClient(webapp.create_app(database, rules=rules, read_only=True), base_url=SITE)
    bakery = row_id(database, "Lukas", "Bakery")
    assert copy.post("/import/rule", data=form(copy, id=bakery)).status_code == 403
    assert not rules.shared.exists()
