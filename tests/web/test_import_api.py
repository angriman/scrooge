"""The import of the app's own pages, through `/api/import`: from the files to what is
committed."""

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from scrooge import categories
from scrooge.web import app as webapp
from scrooge.web import uploads as uploads_
from tests.fakes import FakeMaturities
from tests.storage.helpers import PEOPLE
from tests.web.builders import Site
from tests.web.test_maturity_lookup import BILL, MATURES, undated


def _headers(site: Site) -> dict[str, str]:
    return {"X-Token": site.token}


def _upload(site: Site, *names: str, owner: str = "Lukas") -> dict[str, Any]:
    response = site.client.post(
        "/api/import", data={"owner": owner}, files=site.uploads(*names), headers=_headers(site)
    )
    sent: dict[str, Any] = response.json()
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
    files = sent["imported"]["files"]
    assert [(f["name"], f["staged"], f["problem"]) for f in files] == [
        ("Migros.csv", True, None),
        ("Revolut Savings.tsv", True, None),
        ("notes.txt", False, None),
    ]
    draft = _draft(site)
    assert [f["filename"] for f in draft["files"]] == ["Migros.csv", "Revolut Savings.tsv"]
    assert draft["rows"]
    assert site.count("committed_transactions") == 0


def test_a_statement_and_its_rows_say_where_its_file_is_downloaded_from(site: Site) -> None:
    _upload(site, "Migros.csv")
    draft = _draft(site)
    (file,) = draft["files"]
    address = f"/statements/{file['id']}/original"
    assert file["original"].endswith(address)
    assert {row["original"] for row in draft["rows"]} == {file["original"]}
    assert site.client.get(file["original"]).content == (site.folder / "Migros.csv").read_bytes()
    for kept in (site.database.parent / "originals").iterdir():
        kept.unlink()
    draft = _draft(site)
    assert draft["files"][0]["original"] is None
    assert {row["original"] for row in draft["rows"]} == {None}


def test_a_committed_row_says_where_its_statements_file_is_downloaded_from(site: Site) -> None:
    _upload(site, "Migros.csv")
    draft = _draft(site)
    choices = [
        {"ids": group["ids"], "category": _leaf(draft, bool(group["incoming"])), "rule": False}
        for group in draft["groups"]
    ]
    sent = {"owner": "Lukas", "choices": choices, "commit": True}
    assert site.client.post("/api/import/categories", json=sent, headers=_headers(site)).is_success
    rows = site.client.get("/api/transactions").json()["rows"]
    assert rows
    assert {row["original"] for row in rows} == {draft["files"][0]["original"]}
    for kept in (site.database.parent / "originals").iterdir():
        kept.unlink()
    rows = site.client.get("/api/transactions").json()["rows"]
    assert {row["original"] for row in rows} == {None}


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
    choices = [{"ids": ["not-a-row"], "category": categories.GROCERIES}]
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
    held = stopped["undated"]["held"]
    assert stopped["undated"]["bills"] == [BILL]
    response = site.client.post(
        "/api/import/maturities", json={"held": held}, headers=_headers(site)
    )
    assert response.status_code == 200
    assert response.json()["notice"].startswith(f"{BILL} matures on 2026-12-24")
    assert site.count("statement_files") == 1
    gone = site.client.post("/api/import/maturities", json={"held": held}, headers=_headers(site))
    assert gone.status_code == 409


def test_a_category_is_added_where_rules_are_kept(site: Site) -> None:
    sent = {"name": "Garden", "side": "expenses", "group": ""}
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
        held = _upload(site, "tastytrade.csv")["undated"]["held"]
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


def test_a_folder_of_hundreds_of_files_is_taken_in_one_import(site: Site) -> None:
    folder = [("file", (f"{number}.txt", b"not a statement")) for number in range(300)]
    files = [*site.uploads("Migros.csv"), *folder]
    response = site.client.post(
        "/api/import", data={"owner": "Lukas"}, files=files, headers=_headers(site)
    )
    assert response.status_code == 200
    assert len(response.json()["imported"]["files"]) == 301


def test_more_files_than_one_import_takes_are_refused_and_say_so(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads_, "UPLOADS", 2)
    files = site.uploads("Migros.csv", "Revolut Savings.tsv", "notes.txt")
    response = site.client.post(
        "/api/import", data={"owner": "Lukas"}, files=files, headers=_headers(site)
    )
    assert response.status_code == 400
    assert response.json()["errors"] == [
        "Nothing was imported: Too many files. Maximum number of files is 2."
    ]
    assert site.count("statement_files") == 0


def test_a_zip_sent_is_opened_and_each_of_its_files_staged(site: Site) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("Migros.csv", "Revolut Savings.tsv", "notes.txt"):
            archive.writestr(f"September/{name}", (site.folder / name).read_bytes())
        archive.writestr("September/inner.zip", b"PK")
    files = [("file", ("statements.zip", buffer.getvalue())), ("file", ("bad.zip", b"no zip"))]
    response = site.client.post(
        "/api/import", data={"owner": "Lukas"}, files=files, headers=_headers(site)
    )
    assert response.status_code == 200
    outcomes = [(f["name"], f["staged"]) for f in response.json()["imported"]["files"]]
    assert outcomes == [
        ("bad.zip", False),
        ("statements.zip/Migros.csv", True),
        ("statements.zip/Revolut Savings.tsv", True),
        ("statements.zip/inner.zip", False),
        ("statements.zip/notes.txt", False),
    ]
    assert [f["filename"] for f in _draft(site)["files"]] == [
        "statements.zip/Migros.csv",
        "statements.zip/Revolut Savings.tsv",
    ]
    kept = {path.name for path in (site.database.parent / "originals").iterdir()}
    assert kept == {
        hashlib.sha256((site.folder / name).read_bytes()).hexdigest() + Path(name).suffix
        for name in ("Migros.csv", "Revolut Savings.tsv")
    }


def _changeable(draft: dict[str, Any]) -> dict[str, Any]:
    """A row paid out whose category a rule gave, named by letters."""
    for row in draft["rows"]:
        named = row["counterparty"] or row["description"]
        paid = row["amount"].startswith("-")
        if row["choosable"] and row["category"] and paid and any(c.isalpha() for c in named):
            return dict(row)
    raise AssertionError


def test_a_category_changed_by_hand_is_answered_with_a_rule_to_offer(site: Site) -> None:
    rules = webapp.Rules(site.folder / "rules.yaml", PEOPLE)
    client: TestClient = site.app(imports=site.setup, rules=rules)
    _upload(site, "Migros.csv")
    row = _changeable(_draft(site))
    other = next(
        name
        for group in _draft(site)["choices"]["leaves"]
        if group["heading"] != "Income"
        for name in group["names"]
        if name != row["category"]
    )
    sent = {"owner": "Lukas", "choices": [{"ids": [row["id"]], "category": other, "rule": False}]}
    response = client.post("/api/import/categories", json=sent, headers=_headers(site))
    assert response.status_code == 200
    (offered,) = response.json()["suggestions"]
    assert (offered["id"], offered["direction"], offered["category"]) == (row["id"], "out", other)
    assert (offered["file"], response.json()["unoffered"]) == ("rules.yaml", None)
    before = rules.shared.read_text(encoding="utf-8")
    adding = {"owner": "Lukas", "id": row["id"]}
    added = client.post("/api/import/rules", json=adding, headers=_headers(site))
    assert added.json() == {"rule": offered, "errors": []}
    written = rules.shared.read_text(encoding="utf-8")
    assert written.startswith("rules:\n  - {match: ")
    assert written.endswith(before.removeprefix("rules:\n"))
    again = client.post("/api/import/rules", json=adding, headers=_headers(site))
    assert again.status_code == 409
    assert again.json()["errors"][0].startswith("No rule was added: That rule is not needed")


def test_no_rule_is_offered_for_a_choice_saved_as_a_rule_or_without_rules(site: Site) -> None:
    _upload(site, "Migros.csv")
    row = _changeable(_draft(site))
    choice = {"ids": [row["id"]], "category": categories.GIFTS, "rule": False}
    sent = {"owner": "Lukas", "choices": [choice]}
    response = site.client.post("/api/import/categories", json=sent, headers=_headers(site))
    assert (response.json()["suggestions"], response.json()["unoffered"]) == ([], None)
    adding = {"owner": "Lukas", "id": row["id"]}
    assert (
        site.client.post("/api/import/rules", json=adding, headers=_headers(site)).status_code
        == 404
    )
    rules = webapp.Rules(site.folder / "rules.yaml", PEOPLE)
    client: TestClient = site.app(imports=site.setup, rules=rules)
    remembered = choice | {"category": categories.DENTIST, "rule": True}
    saved = {"owner": "Lukas", "choices": [remembered]}
    response = client.post("/api/import/categories", json=saved, headers=_headers(site))
    assert response.json()["suggestions"] == []


def test_a_rules_file_that_cannot_be_read_is_said_and_adds_nothing(site: Site) -> None:
    rules = webapp.Rules(site.folder / "rules.yaml", PEOPLE)
    client: TestClient = site.app(imports=site.setup, rules=rules)
    _upload(site, "Migros.csv")
    row = _changeable(_draft(site))
    (site.folder / "rules.yaml").write_text("rules: [\n", encoding="utf-8")
    choice = {"ids": [row["id"]], "category": categories.GIFTS, "rule": False}
    sent = {"owner": "Lukas", "choices": [choice]}
    response = client.post("/api/import/categories", json=sent, headers=_headers(site))
    assert response.json()["suggestions"] == []
    assert response.json()["unoffered"].startswith("No rule can be offered:")
    adding = {"owner": "Lukas", "id": row["id"]}
    refused = client.post("/api/import/rules", json=adding, headers=_headers(site))
    assert refused.status_code == 409
    assert refused.json()["errors"][0].startswith("No rule was added:")


def test_a_rule_is_added_only_for_someone_the_config_names_and_never_on_a_copy(
    site: Site,
) -> None:
    rules = webapp.Rules(site.folder / "rules.yaml", PEOPLE)
    client: TestClient = site.app(imports=site.setup, rules=rules)
    for sent in [{"owner": "Zoe", "id": "x"}, ["Lukas"]]:
        refused = client.post("/api/import/rules", json=sent, headers=_headers(site))
        assert refused.status_code == 400
    assert client.post("/api/import/rules", json={"owner": "Lukas"}).status_code == 403
    copy = site.app(imports=site.setup, rules=rules, read_only=True)
    sent = {"owner": "Lukas", "id": "x"}
    assert copy.post("/api/import/rules", json=sent, headers=_headers(site)).status_code == 403
