"""The import of the app's own pages, through `/api/import`: from the files to what is
committed."""

from typing import Any

from starlette.testclient import TestClient

from scrooge.web import app as webapp
from tests.fakes import FakeMaturities
from tests.storage.helpers import PEOPLE
from tests.web.builders import Site
from tests.web.test_maturity_lookup import BILL, MATURES, undated


def _headers(site: Site) -> dict[str, str]:
    return {"X-Token": site.token}


def _upload(site: Site, *names: str, owner: str = "Lukas") -> dict[str, object]:
    response = site.client.post(
        "/api/import", data={"owner": owner}, files=site.uploads(*names), headers=_headers(site)
    )
    sent: dict[str, object] = response.json()
    sent["status"] = response.status_code
    return sent


def _draft(site: Site, owner: str = "Lukas") -> dict[str, Any]:
    sent: dict[str, Any] = site.client.get("/api/import", params={"owner": owner}).json()
    return sent


def _leaf(draft: dict[str, Any], incoming: bool) -> str:
    """A category for money coming in, or one for money going out."""
    for group in draft["choices"]["leaves"]:
        if (group["heading"] == "Income") == incoming:
            return str(group["names"][0])
    raise AssertionError


def test_the_draft_is_empty_until_statements_are_sent(site: Site) -> None:
    sent = site.client.get("/api/import").json()
    assert sent["owner"] == sent["owners"][0]
    assert (sent["files"], sent["rows"], sent["groups"], sent["gaps"]) == ([], [], [], [])
    assert sent["waiting"] == 0
    assert (sent["rules"], sent["lookup"]) == (False, False)
    assert site.client.get("/api/import", params={"owner": "Zoe"}).status_code == 400
    copy = site.app(imports=site.setup, read_only=True)
    assert copy.get("/api/import").status_code == 404


def test_every_file_sent_says_what_became_of_it_and_is_staged(site: Site) -> None:
    sent = _upload(site, "Migros.csv", "Revolut Savings.tsv", "notes.txt")
    assert sent["status"] == 200
    files = sent["imported"]["files"]  # type: ignore[index]  # JSON of the API
    assert [(f["name"], f["staged"], f["problem"]) for f in files] == [
        ("Migros.csv", True, None),
        ("Revolut Savings.tsv", True, None),
        ("notes.txt", False, None),
    ]
    draft = _draft(site)
    assert [f["filename"] for f in draft["files"]] == ["Migros.csv", "Revolut Savings.tsv"]
    assert draft["rows"]
    assert site.count("committed_transactions") == 0


def test_an_upload_needs_the_token_a_person_and_a_file(site: Site) -> None:
    files = site.uploads("Migros.csv")
    assert site.client.post("/api/import", data={"owner": "Lukas"}, files=files).status_code == 403
    nobody = site.client.post(
        "/api/import", data={"owner": "Zoe"}, files=files, headers=_headers(site)
    )
    assert nobody.status_code == 400
    empty = site.client.post("/api/import", data={"owner": "Lukas"}, headers=_headers(site))
    assert empty.json()["errors"] == ["Choose at least one file."]
    assert site.count("statement_files") == 0


def _unruled(site: Site) -> None:
    """No rule gives the rows of Migros a category: they wait for one."""
    (site.folder / "rules.yaml").write_text("rules: []\n", encoding="utf-8")


def test_categories_are_given_and_what_is_staged_is_committed(site: Site) -> None:
    _unruled(site)
    _upload(site, "Migros.csv", "Revolut Savings.tsv")
    draft = _draft(site)
    assert draft["groups"]
    assert draft["waiting"] == sum(len(group["ids"]) for group in draft["groups"])
    early = {"owner": "Lukas", "choices": [], "commit": True}
    refused = site.client.post("/api/import/categories", json=early, headers=_headers(site))
    assert refused.status_code == 409
    assert refused.json()["errors"][0].startswith("Not committed:")
    choices = [
        {"ids": group["ids"], "category": _leaf(draft, bool(group["incoming"])), "rule": False}
        for group in draft["groups"]
    ]
    sent = {"owner": "Lukas", "choices": choices, "commit": True}
    response = site.client.post("/api/import/categories", json=sent, headers=_headers(site))
    assert response.status_code == 200
    assert response.json()["committed"] == 2
    assert response.json()["changed"] > 0
    assert site.count("committed_transactions") > 0
    assert _draft(site)["files"] == []


def test_a_choice_for_rows_no_longer_staged_changes_nothing(site: Site) -> None:
    _upload(site, "Migros.csv")
    choices = [{"ids": ["not-a-row"], "category": "Groceries"}]
    sent = {"owner": "Lukas", "choices": choices}
    response = site.client.post("/api/import/categories", json=sent, headers=_headers(site))
    assert response.status_code == 409
    assert "load the page again" in response.json()["errors"][0]
    garbled = {"owner": "Lukas", "choices": "Groceries"}
    assert (
        site.client.post("/api/import/categories", json=garbled, headers=_headers(site)).status_code
        == 409
    )


def test_a_staged_statement_is_removed_and_a_committed_one_taken_back(site: Site) -> None:
    _upload(site, "Revolut Savings.tsv")
    [staged] = _draft(site)["files"]
    path = f"/api/statements/{staged['id']}"
    assert site.client.post(f"{path}/discard").status_code == 403
    removed = site.client.post(f"{path}/discard", headers=_headers(site))
    assert removed.json() == {"owner": "Lukas", "errors": []}
    assert _draft(site)["files"] == []
    assert site.client.post(f"{path}/discard", headers=_headers(site)).status_code == 404
    huge = site.client.post(f"/api/statements/{'9' * 30}/uncommit", headers=_headers(site))
    assert huge.status_code == 404

    _upload(site, "Revolut Savings.tsv")
    [again] = _draft(site)["files"]
    assert (
        site.client.post(
            f"/api/statements/{again['id']}/uncommit", headers=_headers(site)
        ).status_code
        == 409
    )


def test_bills_without_a_maturity_are_looked_up_and_the_same_files_imported(site: Site) -> None:
    source = FakeMaturities({BILL: MATURES})
    undated(site, source)
    stopped = _upload(site, "tastytrade.csv")
    assert stopped["status"] == 409
    held = stopped["undated"]["held"]  # type: ignore[index]  # JSON of the API
    assert stopped["undated"]["bills"] == [BILL]  # type: ignore[index]  # JSON of the API
    response = site.client.post(
        "/api/import/maturities", json={"held": held}, headers=_headers(site)
    )
    assert response.status_code == 200
    assert response.json()["notice"].startswith(f"{BILL} matures on 2026-12-24")
    assert site.count("statement_files") == 1
    gone = site.client.post("/api/import/maturities", json={"held": held}, headers=_headers(site))
    assert gone.status_code == 409


def test_a_category_is_added_where_rules_are_kept(site: Site) -> None:
    sent = {"name": "Garden", "side": "uscite", "group": ""}
    assert (
        site.client.post("/api/import/category", json=sent, headers=_headers(site)).status_code
        == 404
    )
    rules = webapp.Rules(site.folder / "rules.local.yaml", PEOPLE)
    client: TestClient = site.app(imports=site.setup, rules=rules)
    added = client.post("/api/import/category", json=sent, headers=_headers(site))
    assert added.json() == {"name": "Garden", "errors": []}
    again = client.post("/api/import/category", json=sent, headers=_headers(site))
    assert again.status_code == 409
    assert client.get("/api/import").json()["rules"] is True


def test_a_bill_that_cannot_be_looked_up_says_why_and_keeps_the_files(site: Site) -> None:
    for source, status, words in (
        (FakeMaturities(down=True), 502, "TreasuryDirect could not tell"),
        (FakeMaturities(), 409, f"TreasuryDirect knows no bill {BILL}"),
    ):
        undated(site, source)
        held = _upload(site, "tastytrade.csv")["undated"]["held"]  # type: ignore[index]  # JSON of the API
        response = site.client.post(
            "/api/import/maturities", json={"held": held}, headers=_headers(site)
        )
        assert response.status_code == status
        assert response.json()["errors"][0].startswith(words)
        assert response.json()["undated"] == {"bills": [BILL], "held": held}
    assert site.count("statement_files") == 0
    undated(site, None)
    stopped = _upload(site, "tastytrade.csv")
    assert stopped["undated"] == {"bills": [BILL], "held": None}
    nowhere = site.client.post("/api/import/maturities", json={}, headers=_headers(site))
    assert nowhere.status_code == 404
