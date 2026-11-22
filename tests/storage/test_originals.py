import hashlib
from pathlib import Path

import pytest

from scrooge.storage import originals

INVOICE = b"%PDF-1.7 an invoice"
EXPORT = b"Date;Amount\n2026-09-10;-12.00\n"


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def inbox(tmp_path: Path) -> Path:
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "Invoice.PDF").write_bytes(INVOICE)
    (folder / "export.csv").write_bytes(EXPORT)
    (folder / "notes.txt").write_bytes(b"not a statement")
    return folder


def test_the_originals_are_kept_next_to_the_database(tmp_path: Path) -> None:
    assert originals.folder_of(tmp_path / "data" / "scrooge.db") == tmp_path / "data" / "originals"


def test_a_file_the_database_knows_is_kept_under_its_digest(tmp_path: Path, inbox: Path) -> None:
    kept = tmp_path / "originals"
    known = {digest(INVOICE): "2026-09/Invoice.PDF", digest(EXPORT): "export.csv"}
    files = sorted(inbox.iterdir())
    assert originals.keep(kept, files, known) == [inbox / "Invoice.PDF", inbox / "export.csv"]
    assert sorted(path.name for path in kept.iterdir()) == sorted(
        [f"{digest(INVOICE)}.pdf", f"{digest(EXPORT)}.csv"]
    )
    found = originals.find(kept, digest(INVOICE), "2026-09/Invoice.PDF")
    assert found is not None
    assert found.read_bytes() == INVOICE


def test_a_file_already_kept_is_not_kept_again(tmp_path: Path, inbox: Path) -> None:
    kept = tmp_path / "originals"
    known = {digest(EXPORT): "export.csv"}
    assert originals.keep(kept, [inbox / "export.csv"], known) == [inbox / "export.csv"]
    before = (kept / f"{digest(EXPORT)}.csv").stat().st_mtime_ns
    assert originals.keep(kept, [inbox / "export.csv"], known) == []
    assert (kept / f"{digest(EXPORT)}.csv").stat().st_mtime_ns == before
    assert [path.name for path in kept.iterdir()] == [f"{digest(EXPORT)}.csv"]  # no part left


def test_nothing_is_kept_of_files_the_database_does_not_know(tmp_path: Path, inbox: Path) -> None:
    kept = tmp_path / "originals"
    assert originals.keep(kept, sorted(inbox.iterdir()), {}) == []
    assert not kept.exists()
    assert originals.find(kept, digest(EXPORT), "export.csv") is None
