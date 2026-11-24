"""The settings page's API: the rules and the categories, read and changed."""

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from scrooge import categories, staging
from scrooge.storage import db
from scrooge.web import app as webapp
from tests.storage.helpers import OWNER, PARTNER, PEOPLE, txn
from tests.web.builders import AUGUST_DAYS, Site, close

OWN = "rules:\n  - {match: 'ANNA MEIER', category: Regali}  # birthdays\n"


@pytest.fixture
def site(folder: Path) -> Site:
    """The app with its rules, and Lukas's August committed: two rows of KONSUM, one of an
    added category, one of Anna."""
    made = Site(folder)
    (folder / "rules.lukas.yaml").write_text(OWN, encoding="utf-8")
    conn = db.connect(made.database)
    staging.add_category(conn, folder / categories.FILE, name="Prestiti", side="uscite", group="")
    day = date(2026, 8, 10)
    rows = [
        txn("-45", date=day, source_ref="1"),
        txn("-15", date=day, source_ref="2"),
        txn("-300", date=day, source_ref="3", description="ANNA MEIER", category="Prestiti"),
    ]
    close(conn, OWNER, AUGUST_DAYS, ("migros", rows))
    conn.close()
    made.client = _app(made)
    return made


def _app(site: Site, **settings: Any) -> TestClient:
    rules = webapp.Rules(site.folder / "rules.yaml", PEOPLE)
    return site.app(imports=site.setup, rules=rules, **settings)


def _post(site: Site, path: str, sent: object, client: TestClient | None = None) -> Any:
    headers = {"x-token": site.token}
    return (client or site.client).post(f"/api/settings/{path}", json=sent, headers=headers)


def test_the_rules_are_listed_in_the_order_they_are_tried(site: Site) -> None:
    sent = site.client.get("/api/settings/rules").json()
    assert sent["people"] == [OWNER, PARTNER]
    assert sent["files"] == [
        {"key": "rules.lukas.yaml", "owner": OWNER},
        {"key": "rules.anna.yaml", "owner": PARTNER},
        {"key": "rules.yaml", "owner": None},
    ]
    assert sent["rules"][0] == {
        "file": "rules.lukas.yaml",
        "owner": OWNER,
        "position": 1,
        "match": "ANNA MEIER",
        "category": "Regali",
        "direction": "",
        "account": "",
        "matches": 1,
    }
    assert [(r["match"], r["matches"]) for r in sent["rules"][1:4]] == [
        ("Hans Muster", 0),
        ("ACME SWITZERLAND", 0),
        ("KONSUM|FRISCHMARKT", 2),
    ]
    assert sent["editable"] is True
    assert "Entrate" in [group["heading"] for group in sent["leaves"]]
    assert sent["errors"] == []
    anna = site.client.get("/api/settings/rules", params={"owner": PARTNER}).json()
    assert [f["key"] for f in anna["files"]] == ["rules.anna.yaml", "rules.yaml"]


def test_rules_of_nobody_are_refused(site: Site) -> None:
    for path in ("rules", "rules/test"):
        refused = site.client.get(f"/api/settings/{path}", params={"owner": "Bob", "match": "a"})
        assert refused.status_code == 400
        assert refused.json()["errors"] == ["Bob is nobody the config names."]


def test_without_a_place_for_rules_there_are_none(site: Site) -> None:
    client = site.app()
    sent = client.get("/api/settings/rules").json()
    assert (sent["files"], sent["rules"], sent["editable"]) == ([], [], False)
    listed = client.get("/api/settings/categories").json()
    assert "Prestiti" not in [c["name"] for c in listed["categories"]]
    assert _post(site, "rules/remove", {}, client).status_code == 404


def test_a_broken_rules_file_is_said(site: Site) -> None:
    (site.folder / "rules.anna.yaml").write_text("rules: [\n", encoding="utf-8")
    sent = site.client.get("/api/settings/rules").json()
    assert len(sent["errors"]) == 1
    assert "rules.anna.yaml" in sent["errors"][0]


def test_a_pattern_is_tried_on_the_committed_rows(site: Site) -> None:
    sent = site.client.get("/api/settings/rules/test", params={"match": "anna"}).json()
    assert sent["count"] == 1
    assert sent["rows"][0]["category"] == "Prestiti"
    refused = site.client.get("/api/settings/rules/test", params={"match": "("})
    assert refused.status_code == 400
    assert refused.json()["errors"][0].startswith("'(' is no pattern")


def _rule(site: Site, position: int, **fields: object) -> dict[str, object]:
    rule = site.client.get("/api/settings/rules").json()["rules"][position]
    return {
        "file": rule["file"],
        "position": rule["position"],
        "was_match": rule["match"],
        "was_category": rule["category"],
        **fields,
    }


def test_a_rule_is_edited_moved_and_removed(site: Site) -> None:
    shared = site.folder / "rules.yaml"
    edit = _rule(site, 3, match="KONSUM", category="Spesa", direction="out")
    assert _post(site, "rules/edit", edit).json() == {"errors": []}
    assert '  - {match: "KONSUM", category: "Spesa", direction: out}\n' in shared.read_text(
        encoding="utf-8"
    )
    assert _post(site, "rules/move", _rule(site, 3, by=-1)).json() == {"errors": []}
    assert [r["match"] for r in site.client.get("/api/settings/rules").json()["rules"][1:4]] == [
        "Hans Muster",
        "KONSUM",
        "ACME SWITZERLAND",
    ]
    assert _post(site, "rules/remove", _rule(site, 0)).json() == {"errors": []}
    assert (site.folder / "rules.lukas.yaml").read_text(encoding="utf-8") == "rules:\n"


@pytest.mark.parametrize(
    ("sent", "status", "problem"),
    [
        ({"file": "rules.bob.yaml", "position": 1}, 400, "There is no rules file rules.bob.yaml"),
        ({"file": "rules.yaml", "position": "1"}, 400, "Send the place of the rule"),
        ({"file": "rules.yaml", "position": True}, 400, "Send the place of the rule"),
        ({"file": 1}, 400, "Send 'file' as text."),
        ([1], 400, "Send the change as JSON."),
        (
            {"file": "rules.yaml", "position": 1, "was_match": "x", "was_category": "Spesa"},
            409,
            "Rule 1 of rules.yaml changed since",
        ),
    ],
)
def test_a_change_of_a_rule_that_cannot_be_made_says_why(
    site: Site, sent: object, status: int, problem: str
) -> None:
    answer = _post(site, "rules/remove", sent)
    assert answer.status_code == status
    assert answer.json()["errors"][0].startswith(problem)


def test_a_change_needs_the_token_and_a_writable_database(site: Site) -> None:
    sent = _rule(site, 0)
    assert site.client.post("/api/settings/rules/remove", json=sent).status_code == 403
    copy = _app(site, read_only=True)
    assert copy.get("/api/settings/rules").json()["editable"] is False
    assert _post(site, "rules/remove", sent, copy).status_code == 403
    assert "ANNA MEIER" in (site.folder / "rules.lukas.yaml").read_text(encoding="utf-8")


def test_the_categories_are_listed_with_their_rows(site: Site) -> None:
    sent = site.client.get("/api/settings/categories").json()
    listed = {(c["name"], c["side"]): c for c in sent["categories"]}
    assert listed["Prestiti", "uscite"] == {
        "name": "Prestiti",
        "side": "uscite",
        "parent": "Prestiti",
        "built_in": False,
        "rows": 1,
    }
    assert listed["Spesa", "uscite"]["rows"] == 2
    assert "Prestiti" in sent["groups"]
    assert sent["editable"] is True


def test_categories_are_added_renamed_merged_and_removed(site: Site) -> None:
    added = _post(site, "categories/add", {"name": "Mance", "side": "entrate", "group": ""})
    assert added.json() == {"name": "Mance", "errors": []}
    assert _post(site, "categories/add", {"name": "Mance", "side": "entrate"}).status_code == 409
    renamed = _post(site, "categories/rename", {"name": "Prestiti", "to": "Mutuo"})
    assert renamed.json() == {"rows": 1, "errors": []}
    merged = _post(site, "categories/merge", {"name": "Mutuo", "into": "Regali"})
    assert merged.json() == {"rows": 1, "errors": []}
    assert _post(site, "categories/remove", {"name": "Mance"}).json() == {"errors": []}
    names = [c["name"] for c in site.client.get("/api/settings/categories").json()["categories"]]
    assert {"Prestiti", "Mutuo", "Mance"}.isdisjoint(names)
    refused = _post(site, "categories/remove", {"name": "Spesa"})
    assert refused.status_code == 409
    assert refused.json()["errors"][0].startswith("Spesa is built in")


def test_a_rename_the_database_cannot_take_is_refused(site: Site) -> None:
    conn = sqlite3.connect(site.database)
    conn.execute(
        "INSERT INTO categories (name, parent_id, side, sort_order) SELECT 'Mutuo', id, "
        "'uscite', 999 FROM categories WHERE name = 'Prestiti' AND parent_id IS NULL"
    )
    conn.commit()
    conn.close()
    refused = _post(site, "categories/rename", {"name": "Prestiti", "to": "Mutuo"})
    assert refused.status_code == 409
    assert refused.json()["errors"] == ["The database already has a category of that name."]
    assert "Prestiti" in (site.folder / categories.FILE).read_text(encoding="utf-8")
