import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pdfplumber.utils.exceptions import PdfminerException

from scrooge import categories, importing, staging
from scrooge.errors import ConfigError
from scrooge.extractors import ocr, swisscard
from scrooge.router import LABELS
from scrooge.storage import db, originals
from tests.conftest import FIXTURES, INSTRUMENTS_YAML, MATURED, write_rules
from tests.storage.helpers import OWNER, PARTNER, PEOPLE

NOW = datetime(2026, 12, 20, 12, 0, tzinfo=UTC)


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


def test_a_file_named_as_a_card_invoice_that_is_no_pdf_is_refused_and_the_rest_is_read(
    folder: Path, setup: importing.Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = folder / "swisscard-copy.pdf"
    broken.write_bytes(b"not a pdf")

    def read_text(path: Path) -> str:
        if path == broken:
            raise PdfminerException("no /Root object")
        return path.read_text(encoding="utf-8")

    monkeypatch.setattr(swisscard, "read_text", read_text)
    found = importing.read(OWNER, files_of(folder), setup)
    (refused,) = found.refused
    assert refused.name == "swisscard-copy.pdf"
    assert "not a readable PDF" in refused.problem
    assert refused.label == "not read"
    assert len(found.statements) == 6  # the others are read as ever


def test_nothing_that_is_a_statement_stages_nothing(
    conn: sqlite3.Connection, folder: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    notes = [folder / "notes.txt"]
    found = importing.read(OWNER, notes, setup)
    assert found.statements == ()
    done = importing.stage(conn, tmp_path / "data" / "scrooge.db", found, setup, NOW)
    assert done.staged == staging.Staged((), 0, 0)
    assert not (tmp_path / "data" / "originals").exists()


def test_what_is_staged_is_kept_and_read_again_it_is_nothing_new(
    conn: sqlite3.Connection, folder: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    database = tmp_path / "data" / "scrooge.db"
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
    database = tmp_path / "data" / "scrooge.db"
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


def test_two_exports_of_one_account_in_one_import_are_both_refused_and_the_rest_staged(
    conn: sqlite3.Connection, folder: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    original = (folder / "Migros.csv").read_bytes()
    (folder / "Migros copy.csv").write_bytes(original + b"\n")  # another file, the same days
    found = importing.read(OWNER, files_of(folder), setup)
    done = importing.stage(conn, tmp_path / "data" / "scrooge.db", found, setup, NOW)
    files = by_name(done)
    assert "Migros copy.csv" in files["Migros.csv"].problem
    assert "Migros.csv" in files["Migros copy.csv"].problem
    assert not files["Migros.csv"].staged
    assert not files["Migros copy.csv"].staged
    assert files["swisscard.pdf"].staged
    assert files["swisscard.pdf"].problem == ""
    stored = {row[0] for row in conn.execute("SELECT filename FROM statement_files")}
    assert stored >= {"swisscard.pdf", "IBKR.csv"}
    assert not stored & {"Migros.csv", "Migros copy.csv"}


def test_a_file_imported_again_over_one_staged_replaces_it(
    conn: sqlite3.Connection, folder: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    database = tmp_path / "data" / "scrooge.db"
    importing.stage(conn, database, importing.read(OWNER, files_of(folder), setup), setup, NOW)
    migros = folder / "Migros.csv"
    migros.write_bytes(migros.read_bytes() + b"\n")
    found = importing.read(OWNER, files_of(folder), setup)
    done = importing.stage(conn, database, found, setup, NOW)
    assert by_name(done)["Migros.csv"].problem == ""
    assert by_name(done)["Migros.csv"].staged


def test_a_certificate_that_cannot_replace_a_committed_one_is_refused_alone(
    conn: sqlite3.Connection,
    folder: Path,
    setup: importing.Setup,
    pillar2: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = folder / "data" / "scrooge.db"
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
    done = importing.stage(conn, tmp_path / "data" / "scrooge.db", found, setup, NOW)
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


def test_unmatched_expenses_and_income_need_no_review(folder: Path, setup: importing.Setup) -> None:
    (folder / "rules.yaml").write_text("rules: []\n", encoding="utf-8")
    plain = importing.read(OWNER, files_of(folder), setup)
    assert any(t.kind.value in {"expense", "income"} for t in plain.month.uncategorized)
    filled = importing.read(OWNER, files_of(folder), replace(setup, unmatched=categories.UNSORTED))
    left = filled.month.uncategorized
    assert all(t.kind.value not in {"expense", "income"} for t in left)
    assert len(left) < len(plain.month.uncategorized)


def test_an_unmatched_category_that_does_not_exist_is_refused(
    folder: Path, setup: importing.Setup
) -> None:
    with pytest.raises(ConfigError, match="'Nowhere' is no category"):
        importing.read(OWNER, files_of(folder), replace(setup, unmatched="Nowhere"))


def monthly_migros(
    folder: Path,
    names: Mapping[str, date],
    opening: Decimal,
    jumps: Mapping[str, Decimal] | None = None,
) -> list[Path]:
    """Migros statements of consecutive months, one file each, named as given: each month
    pays the rent and gets the salary, and starts from where the month before closed, or that
    plus its jump."""
    balance = opening
    files: dict[date, Path] = {}
    for name, first in sorted(names.items(), key=lambda item: item[1]):
        balance += (jumps or {}).get(name, Decimal(0))
        last = (first + timedelta(days=31)).replace(day=1) - timedelta(days=1)
        lines = []
        for day, text, amount in (
            (first.replace(day=2), "Hans Muster, Beispielweg 8, 5000 Aarau", Decimal(-1000)),
            (first.replace(day=25), "ACME SWITZERLAND GMBH, 0000000001", Decimal(2000)),
        ):
            balance += amount
            lines.append(
                f'"{day:%d.%m.%Y}";"{text}";"";;"{amount:.2f}";"{balance:.2f}";"{day:%d.%m.%Y}"'
            )
        path = folder / name
        path.write_text(
            "\n".join(
                [
                    f'"Estratto conto da:";"{first}"',
                    f'"Estratto conto fino:";"{last}"',
                    ";",
                    '"Contratto:";"EB00000000"',
                    '"Numero di conto / IBAN:";"00.000.000 / CH00 0000 0000 0000 0000 0"',
                    '"Denominazione:";"Conto privato"',
                    f'"Saldo:";"CHF {balance:.2f}"',
                    ";",
                    '"Data";"Testo di registrazione";"Comunicazione";"Numero di riferimento";'
                    '"Importo";"Saldo";"Valuta"',
                    *lines,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        files[first] = path
    return [files[first] for first in sorted(files)]


# Named so that their names sort in another order than their months.
MONTHS = {
    "Apr.csv": date(2026, 4, 1),
    "Feb.csv": date(2026, 2, 1),
    "Jan.csv": date(2026, 1, 1),
    "Mar.csv": date(2026, 3, 1),
}


def stored_months(conn: sqlite3.Connection) -> list[str]:
    """The statements stored, staged or committed, by their first day."""
    return [
        row[0] for row in conn.execute("SELECT filename FROM statement_files ORDER BY period_start")
    ]


def test_statements_of_one_account_are_staged_in_date_order_whatever_their_names(
    conn: sqlite3.Connection, setup: importing.Setup, tmp_path: Path
) -> None:
    drop = tmp_path / "drop"
    drop.mkdir()
    files = monthly_migros(drop, MONTHS, Decimal(500))
    found = importing.read(OWNER, sorted(files, reverse=True), setup)
    assert found.refused == ()
    done = importing.stage(conn, tmp_path / "data" / "scrooge.db", found, setup, NOW)
    assert done.refused == ()
    assert done.staged is not None
    assert done.staged.gaps == ()
    assert [f.name for f in done.files] == sorted(MONTHS)  # shown by name
    assert len(staging.commit(conn, owner=OWNER, now=NOW)) == len(MONTHS)
    assert stored_months(conn) == ["Jan.csv", "Feb.csv", "Mar.csv", "Apr.csv"]


def test_statements_staged_one_by_one_are_staged_in_date_order_whatever_their_names(
    conn: sqlite3.Connection, setup: importing.Setup, tmp_path: Path
) -> None:
    drop = tmp_path / "drop"
    drop.mkdir()
    files = monthly_migros(drop, MONTHS, Decimal(500))
    # Two exports of another account and the same days: the one transaction is refused, and
    # each file is staged on its own.
    revolut = FIXTURES / "revolut" / "chf.tsv"
    twins = [drop / "Revolut.tsv", drop / "Revolut copy.tsv"]
    twins[0].write_bytes(revolut.read_bytes())
    twins[1].write_bytes(revolut.read_bytes() + b"\n")
    found = importing.read(OWNER, [*twins, *files], setup)
    done = importing.stage(conn, tmp_path / "data" / "scrooge.db", found, setup, NOW)
    assert [f.name for f in done.refused] == ["Revolut copy.tsv", "Revolut.tsv"]
    assert all(by_name(done)[name].staged for name in MONTHS)
    assert done.staged is not None
    assert done.staged.gaps == ()
    assert len(staging.commit(conn, owner=OWNER, now=NOW)) == len(MONTHS)
    assert stored_months(conn) == ["Jan.csv", "Feb.csv", "Mar.csv", "Apr.csv"]


def test_of_statements_staged_one_by_one_the_first_that_breaks_the_chain_is_refused(
    conn: sqlite3.Connection, setup: importing.Setup, tmp_path: Path
) -> None:
    drop = tmp_path / "drop"
    drop.mkdir()
    # February does not start where January closed: February is the one refused, whatever
    # the names, and the months around it are staged.
    files = monthly_migros(drop, MONTHS, Decimal(500), {"Feb.csv": Decimal(50)})
    found = importing.read(OWNER, files, setup)
    done = importing.stage(conn, tmp_path / "data" / "scrooge.db", found, setup, NOW)
    assert [f.name for f in done.refused] == ["Feb.csv"]
    assert stored_months(conn) == ["Jan.csv", "Mar.csv", "Apr.csv"]
    assert done.staged is not None
    (gap,) = done.staged.gaps
    assert "2026-01-31" in gap
