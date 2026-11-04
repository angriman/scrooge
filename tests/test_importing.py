import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from finanze import importing, staging
from finanze.extractors import ocr, swisscard
from finanze.router import LABELS
from finanze.storage import db, originals
from tests.conftest import FIXTURES, INSTRUMENTS_YAML, write_rules
from tests.storage.helpers import OWNER, PARTNER, PEOPLE

NOW = datetime(2026, 12, 20, 12, 0, tzinfo=UTC)
MATURED = "  912797YY2: {maturity: 2026-09-16}\n"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


@pytest.fixture
def setup(folder: Path) -> importing.Setup:
    instruments = folder / "instruments.yaml"
    instruments.write_text(INSTRUMENTS_YAML + MATURED, encoding="utf-8")
    return importing.Setup([write_rules(folder)], instruments, PEOPLE, {})


def files_of(folder: Path) -> list[Path]:
    return sorted(path for path in folder.iterdir() if path.is_file() and path.name[0] != ".")


def by_name(found: importing.Read) -> dict[str, importing.File]:
    return {file.name: file for file in found.files}


def test_every_file_is_read_on_its_own_and_named_by_what_it_is(
    folder: Path, setup: importing.Setup
) -> None:
    found = importing.read(OWNER, files_of(folder), setup)
    files = by_name(found)
    assert [file.name for file in found.files] == sorted(files, key=str)
    assert files["Migros.csv"].label == LABELS["migros"]
    assert files["swisscard.pdf"].label == LABELS["swisscard"]
    assert files["IBKR.csv"].recognised
    assert files["notes.txt"].label == "not a statement"
    assert not files["notes.txt"].recognised
    assert {path.name for path in found.skipped} == {
        "ibkr.pdf",
        "instruments.yaml",
        "notes.txt",
        "rules.yaml",
    }
    assert found.refused == ()
    assert len(found.statements) == 6
    assert found.staged is None


def test_a_file_that_does_not_reconcile_is_refused_and_the_others_are_read(
    folder: Path, setup: importing.Setup
) -> None:
    statement = folder / "Migros.csv"
    statement.write_text(
        statement.read_text(encoding="utf-8").replace("-1288.18", "-1288.19"), encoding="utf-8"
    )
    found = importing.read(OWNER, files_of(folder), setup)
    (refused,) = found.refused
    assert refused.name == "Migros.csv"
    assert refused.label == "not read"
    assert not refused.recognised
    assert refused.problem
    assert refused.path not in found.skipped  # a statement that failed is no stray file
    assert "Migros.csv" not in {s.filename for s in found.statements}
    assert len(found.statements) == 5


def test_a_card_invoice_the_bank_contradicts_is_refused_and_the_rest_is_read(
    folder: Path, setup: importing.Setup
) -> None:
    invoice = folder / "swisscard.pdf"
    # The invoice lists a payment on a day the bank statement covers, and the bank made none.
    invoice.write_text(
        invoice.read_text(encoding="utf-8").replace("26.08.2026 IL SUO", "02.09.2026 IL SUO"),
        encoding="utf-8",
    )
    found = importing.read(OWNER, files_of(folder), setup)
    (refused,) = found.refused
    assert refused.name == "swisscard.pdf"
    assert "no such card debit" in refused.problem
    assert refused.label == LABELS["swisscard"]  # it was recognised, and set aside
    assert "swisscard.pdf" not in {s.filename for s in found.statements}
    assert {row.account for row in found.month.transactions} >= {"Migros"}
    assert "Swisscard" not in {row.account for row in found.month.transactions}


def test_nothing_that_is_a_statement_stages_nothing(
    conn: sqlite3.Connection, folder: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    notes = [folder / "notes.txt"]
    found = importing.read(OWNER, notes, setup)
    assert found.statements == ()
    done = importing.stage(conn, tmp_path / "data" / "finanze.db", found, setup, NOW)
    assert done.staged == staging.Staged((), 0, 0)
    assert not (tmp_path / "data" / "originals").exists()


def test_what_is_staged_is_kept_and_read_again_it_is_nothing_new(
    conn: sqlite3.Connection, folder: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    database = tmp_path / "data" / "finanze.db"
    # without the Tastytrade export, which wants the August purchase of its bill before a commit
    files = [path for path in files_of(folder) if path.name != "tastytrade.csv"]
    found = importing.read(OWNER, files, setup)
    done = importing.stage(conn, database, found, setup, NOW)
    migros = by_name(done)["Migros.csv"]
    assert (migros.staged, migros.nothing_new, migros.problem) == (True, False, "")
    assert done.staged is not None
    assert done.staged.transactions > 0
    assert len(list(originals.folder_of(database).iterdir())) == 5

    staging.commit(conn, owner=OWNER, now=NOW)
    again = importing.stage(conn, database, importing.read(OWNER, files, setup), setup, NOW)
    migros = by_name(again)["Migros.csv"]
    assert (migros.staged, migros.nothing_new) == (False, True)
    assert by_name(again)["notes.txt"].staged is False
    assert again.staged is not None
    assert again.staged.transactions == 0


def test_a_statement_of_the_other_persons_account_is_refused_and_the_others_staged(
    conn: sqlite3.Connection, folder: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    database = tmp_path / "data" / "finanze.db"
    mine = importing.read(OWNER, files_of(folder), setup)
    importing.stage(conn, database, mine, setup, NOW)

    hers = importing.read(PARTNER, files_of(folder), setup)
    done = importing.stage(conn, database, hers, setup, NOW)
    files = by_name(done)
    for name in ("IBKR.csv", "Migros.csv", "swisscard.pdf"):  # they say whose account it is
        assert "account" in files[name].problem
        assert "not of Anna's" in files[name].problem
        assert not files[name].staged
    assert files["Revolut CHF.tsv"].staged
    assert files["Revolut CHF.tsv"].problem == ""
    assert [f.name for f in done.refused] == ["IBKR.csv", "Migros.csv", "swisscard.pdf"]
    assert done.staged is not None
    assert done.staged.transactions > 0
    anna = conn.execute(
        "SELECT count(*) FROM statement_files f JOIN owners o ON o.id = f.owner_id "
        "WHERE o.name = 'Anna' AND f.source IN ('ibkr', 'migros', 'swisscard')"
    ).fetchone()[0]
    assert anna == 0


def test_a_certificate_that_cannot_replace_a_committed_one_is_refused_alone(
    conn: sqlite3.Connection,
    folder: Path,
    setup: importing.Setup,
    pillar2: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = folder / "data" / "finanze.db"
    scans = [pillar2 / "Scan 0001.pdf", pillar2 / "Dokument.pdf"]
    importing.stage(conn, database, importing.read(OWNER, scans, setup), setup, NOW)
    staging.commit(conn, owner=OWNER, now=NOW)

    again = folder / "again" / "Scan 0001.pdf"
    again.parent.mkdir()
    again.write_bytes((pillar2 / "Scan 0001.pdf").read_bytes() + b"\n% scanned twice")
    text = (FIXTURES / "axa" / "certificate.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(ocr, "read_text", lambda path: text)  # Tesseract's answer for any scan
    found = importing.read(OWNER, [folder / "Migros.csv", again], setup)
    done = importing.stage(conn, database, found, setup, NOW)
    files = by_name(done)
    assert files["Migros.csv"].staged
    assert "is committed: take it back before" in files["Scan 0001.pdf"].problem
    assert files["Scan 0001.pdf"].label == LABELS["axa"]
    assert [f.name for f in done.refused] == ["Scan 0001.pdf"]


def test_the_rules_as_they_are_now_classify_what_was_read_before(
    folder: Path, setup: importing.Setup
) -> None:
    setup.rules[0].write_text("rules: []\n", encoding="utf-8")
    found = importing.read(OWNER, files_of(folder), setup)
    assert found.month.uncategorized
    write_rules(folder)
    classified = importing.again(found, setup)
    assert not classified.month.uncategorized
    assert classified.files == found.files


def test_a_stock_plan_statement_is_staged_and_committed(
    conn: sqlite3.Connection,
    setup: importing.Setup,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = (FIXTURES / "morganstanley" / "statement.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(swisscard, "read_text", lambda path: text)
    monkeypatch.setattr(ocr, "is_scan", lambda path: False)
    statement = tmp_path / "Quarterly Statement 09_30_2026.pdf"
    statement.write_bytes(b"%PDF-1.7 ...")
    found = importing.read(OWNER, [statement], setup)
    assert by_name(found)[statement.name].label == LABELS["morganstanley"]
    done = importing.stage(conn, tmp_path / "data" / "finanze.db", found, setup, NOW)
    assert by_name(done)[statement.name].staged
    assert len(staging.commit(conn, owner=OWNER, now=NOW)) == 1

    account = conn.execute(
        "SELECT kind, currency FROM accounts WHERE name = 'Morgan Stanley'"
    ).fetchone()
    assert tuple(account) == ("broker_cash", "USD")
    number = conn.execute("SELECT source, account_number FROM statement_files").fetchone()
    assert tuple(number) == ("morganstanley", "MSX00000001")  # whose account it is
    held = conn.execute("SELECT quantity, price FROM positions").fetchall()
    assert [tuple(row) for row in held] == [("4.135", "158.4000")]
    # A move in kind has no price: it never stands in for a close.
    moved = conn.execute(
        "SELECT tr.quantity, tr.price FROM trades tr WHERE tr.quantity LIKE '-%' "
        "ORDER BY tr.quantity"
    ).fetchall()
    assert [tuple(row) for row in moved] == [("-4.000", None), ("-6.000", None)]
