from pathlib import Path

from finanze import staging
from finanze.storage import db
from tests.conftest import FILES
from tests.web.builders import CLICKED, SITE, Site
from tests.web.test_analysis_cashflow import tables
from tests.web.test_app import text


def test_the_page_asks_whose_statements_and_which_files(site: Site) -> None:
    response = site.client.get("/import", params={"owner": "Anna"})
    assert response.status_code == 200
    assert 'enctype="multipart/form-data"' in response.text
    assert '<input type="file" name="file" multiple required>' in response.text
    assert "<option>Lukas</option>" in response.text
    assert "<option selected>Anna</option>" in response.text
    assert "Nothing is staged for Anna." in text(response.text)
    link = f'<a href="{SITE}/import?owner=Lukas" class="import" aria-current="page">Import</a>'
    assert link in site.client.get("/import").text
    assert site.client.get("/import", params={"owner": "Zoe"}).status_code == 404


def result(page: str) -> list[list[str]]:
    """File, kind and what became of it, for each file of the upload."""
    return tables(page)[0]


def test_every_file_of_an_upload_says_what_it_is_and_what_became_of_it(site: Site) -> None:
    response = site.send([*site.uploads(), *site.uploads("notes.txt")])
    assert response.status_code == 200
    assert result(response.text) == [
        ["IBKR.csv", "IBKR activity statement", "Staged."],
        ["Migros.csv", "Migros Bank account statement", "Staged."],
        ["Revolut CHF.tsv", "Revolut account statement", "Staged."],
        ["Revolut Savings.tsv", "Revolut Savings statement", "Staged."],
        ["notes.txt", "not a statement", "Not recognised: it is not a statement and was left out."],
        ["swisscard.pdf", "Swisscard invoice", "Staged."],
        ["tastytrade.csv", "Tastytrade transactions", "Staged."],
    ]
    assert "Revolut EUR includes rows with no balance to check against" in text(response.text)
    assert site.count("transactions") > 0
    assert site.count("committed_transactions") == 0  # staged: the draft below commits
    assert site.count("statement_files") == len(FILES)
    assert site.kept() == len(FILES)
    assert list(site.folder.glob("**/*.part")) == []


def test_the_draft_of_the_person_is_below_the_upload(site: Site) -> None:
    response = site.send(site.uploads("Migros.csv", "Revolut Savings.tsv"))
    page = response.text
    assert page.index('id="uploaded"') < page.index('id="staged"') < page.index('id="rows"')
    assert "Waiting to be committed, Lukas" in text(page)
    files = tables(page)[1]
    assert [row[:4] for row in files] == [
        ["Migros Bank account statement", "Migros.csv", "2026-09-01 to 2026-09-30", "11"],
        ["Revolut Savings statement", "Revolut Savings.tsv", "2026-09-01 to 2026-09-30", "6"],
    ]
    # The same draft on a plain visit.
    assert tables(site.client.get("/import").text)[0] == files
    assert tables(site.client.get("/import", params={"owner": "Anna"}).text) == []


def test_a_file_that_does_not_reconcile_is_refused_and_the_others_are_staged(site: Site) -> None:
    migros = site.folder / "Migros.csv"
    tampered = migros.read_text(encoding="utf-8").replace("-1288.18", "-1288.19")
    migros.write_text(tampered, encoding="utf-8")
    response = site.send(site.uploads("Migros.csv", "Revolut Savings.tsv"))
    assert response.status_code == 200
    refused, staged = result(response.text)
    assert refused[:2] == ["Migros.csv", "not read"]
    assert refused[2] == "Refused: Migros row 4: balance 4686.42 does not follow 5974.60 -1288.19"
    assert staged == ["Revolut Savings.tsv", "Revolut Savings statement", "Staged."]
    assert 'class="wide error"' in response.text
    assert site.count("statement_files") == 1
    assert site.kept() == 1


def test_a_committed_statement_is_left_as_it_is_and_what_differs_is_said(site: Site) -> None:
    assert site.send(site.uploads("Revolut Savings.tsv")).status_code == 200
    conn = db.connect(site.database)
    staging.commit(conn, owner="Lukas", now=CLICKED)
    conn.close()
    before = site.count("transactions")
    same = site.send(site.uploads("Revolut Savings.tsv"))
    assert result(same.text) == [
        [
            "Revolut Savings.tsv",
            "Revolut Savings statement",
            "Nothing new: all of it is committed already.",
        ]
    ]
    assert "6 rows are booked on days already committed and stored there" in text(same.text)
    assert "Differs" not in same.text

    savings = site.folder / "Revolut Savings.tsv"
    lines = savings.read_text(encoding="utf-8").splitlines(keepends=True)
    without = [line for line in lines if not line.startswith("Sep 29, 2026, 2:09")]  # rows short
    savings.write_text("".join(without), encoding="utf-8")
    short = site.send(site.uploads("Revolut Savings.tsv"))
    assert short.status_code == 200
    assert "is committed and is not in Revolut Savings.tsv." in text(short.text)
    assert "Nothing was changed in what is committed" in text(short.text)
    assert site.count("transactions") == before


def test_an_import_that_cannot_start_stores_nothing_and_says_why(site: Site) -> None:
    (site.folder / "categories.yaml").write_text("categories: 5", encoding="utf-8")
    response = site.send(site.uploads("Migros.csv"))
    assert response.status_code == 409
    assert "Nothing was imported: " in text(response.text)
    assert site.count("statement_files") == 0
    assert "<form" in response.text


def test_the_form_needs_a_person_and_a_file(site: Site) -> None:
    nobody = site.send(site.uploads("Migros.csv"), owner="Zoe")
    assert nobody.status_code == 400
    assert "Zoe is nobody the config names" in text(nobody.text)
    nothing = site.send([])
    assert nothing.status_code == 400
    assert "Choose at least one file" in text(nothing.text)
    assert site.count("transactions") == 0


def test_two_files_of_one_name_are_told_apart(site: Site) -> None:
    first, second = site.uploads("Migros.csv", "Revolut Savings.tsv")
    response = site.send([first, ("file", ("Migros.csv", second[1][1]))])
    assert response.status_code == 200
    assert result(response.text)[0][:2] == ["1/Migros.csv", "Migros Bank account statement"]
    assert result(response.text)[1][:2] == ["2/Migros.csv", "Revolut Savings statement"]


def test_a_form_from_elsewhere_or_without_the_token_is_refused(site: Site) -> None:
    files = site.uploads("Migros.csv")
    stale = site.send(files, token="of-an-earlier-start")  # noqa: S106  # not a password
    assert stale.status_code == 403
    assert "this page was loaded before the app was last started" in text(stale.text)
    assert f'name="token" value="{site.token}"' in stale.text  # sending from here works
    form = {"token": site.token, "owner": "Lukas"}
    elsewhere = {"origin": "https://evil.example"}
    assert site.client.post("/import", data=form, files=files, headers=elsewhere).status_code == 403
    assert site.count("transactions") == 0


def test_a_copy_imports_nothing(site: Site) -> None:
    copy = site.app(imports=site.setup, read_only=True)
    assert "Import</a>" not in copy.get("/months").text
    assert copy.get("/import").status_code == 404
    form = {"token": site.token, "owner": "Lukas"}
    assert copy.post("/import", data=form, files=site.uploads("Migros.csv")).status_code == 404
    assert site.count("transactions") == 0


def test_without_its_setup_the_page_shows_the_draft_but_takes_no_files(site: Site) -> None:
    assert site.send(site.uploads("Migros.csv")).status_code == 200
    plain = site.app()
    page = plain.get("/import", params={"owner": "Lukas"})
    assert page.status_code == 200
    assert "Migros.csv" in text(page.text)
    assert 'type="file"' not in page.text
    form = {"token": site.token, "owner": "Lukas"}
    assert plain.post("/import", data=form, files=site.uploads("Migros.csv")).status_code == 404


def test_a_pension_certificate_is_taken_and_what_was_read_is_shown(
    site: Site, pillar2: Path
) -> None:
    scan = ("file", ("Scan 0001.pdf", (pillar2 / "Scan 0001.pdf").read_bytes()))
    response = site.send([scan])
    assert response.status_code == 200
    page = text(response.text)
    assert "Pillar 2 AXA of Lukas: " in page
    assert "read off Scan 0001.pdf." in page
    assert "statements reconciled" not in page
    assert site.count("balances") > 0
    assert site.kept() == 1
