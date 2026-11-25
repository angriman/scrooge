"""Statements in a zip: each file of it read as if it had been given on its own."""

import hashlib
import io
import sqlite3
import zipfile
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from scrooge import archives, importing
from scrooge.storage import db, originals
from tests.conftest import INSTRUMENTS_YAML, MATURED, write_rules
from tests.storage.helpers import OWNER, PEOPLE
from tests.test_importing import NOW, by_name, files_of


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


def zipped(path: Path, entries: Mapping[str, bytes]) -> Path:
    """A zip at `path` of `entries`, by the names they have in it."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


def statements(folder: Path) -> dict[str, bytes]:
    """The statements of the fixtures but Tastytrade's, which wants an August purchase."""
    return {
        path.name: path.read_bytes()
        for path in files_of(folder)
        if path.suffix in {".csv", ".tsv", ".pdf"} and path.name != "tastytrade.csv"
    }


def test_a_zip_of_statements_is_staged_like_its_files_and_their_originals_kept(
    conn: sqlite3.Connection, folder: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    inside = statements(folder)
    archive = zipped(tmp_path / "aside.zip", inside)
    database = tmp_path / "data" / "scrooge.db"
    found = importing.read(OWNER, [archive], setup, tmp_path / "unpacked")
    alone = importing.read(OWNER, [folder / name for name in inside], setup)
    assert [(f.name, f.label) for f in found.files] == [
        (f"aside.zip/{f.name}", f.label) for f in alone.files
    ]
    done = importing.stage(conn, database, found, setup, NOW)
    assert by_name(done)["aside.zip/Migros.csv"].staged
    assert found.refused == ()
    # Each statement under its own digest, never the zip.
    kept = {path.name for path in originals.folder_of(database).iterdir()}
    digests = {
        hashlib.sha256(content).hexdigest() + Path(name).suffix
        for name, content in inside.items()
        if name != "ibkr.pdf"  # no statement
    }
    assert kept == digests
    stored = conn.execute("SELECT filename FROM statement_files ORDER BY filename").fetchall()
    assert [row[0] for row in stored] == sorted(
        f"aside.zip/{name}" for name in inside if name != "ibkr.pdf"
    )


def test_without_a_folder_for_its_files_a_zip_is_no_statement(
    folder: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    archive = zipped(tmp_path / "aside.zip", statements(folder))
    found = importing.read(OWNER, [archive], setup)
    assert [(f.name, f.label) for f in found.files] == [("aside.zip", "not a statement")]


def test_names_in_the_zip_are_only_shown_and_nothing_lands_outside_its_folder(
    folder: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    migros = (folder / "Migros.csv").read_bytes()
    entries = {
        "../escape.txt": b"one",
        "/etc/absolute.txt": b"two",
        "..\\..\\windows.txt": b"three",
        "a/../../../deep/Migros.csv": migros,
        "..": b"four",
    }
    archive = zipped(tmp_path / "aside.zip", entries)
    unpacked = tmp_path / "deep" / "unpacked"
    unpacked.mkdir(parents=True)
    found = importing.read(OWNER, [archive], setup, unpacked)
    assert {f.name: f.label for f in found.files} == {
        "aside.zip/escape.txt": "not a statement",
        "aside.zip/absolute.txt": "not a statement",
        "aside.zip/windows.txt": "not a statement",
        "aside.zip/Migros.csv": "Migros Bank account statement",
    }
    assert all(f.path.is_relative_to(unpacked) for f in found.files)
    outside = [path for path in tmp_path.rglob("*") if not path.is_relative_to(unpacked)]
    assert sorted(path.name for path in outside if path.is_file()) == sorted(
        path.name for path in [*files_of(folder), folder / ".DS_Store"]
    )


def test_folders_hidden_files_and_what_a_mac_adds_are_left_out(
    setup: importing.Setup, tmp_path: Path
) -> None:
    entries = {
        "statements/": b"",
        "statements/notes.txt": b"not a statement",
        "statements/.DS_Store": b"\0",
        "__MACOSX/statements/._notes.txt": b"\0",
        ".git/config": b"",
        "statements/.hidden/notes.txt": b"",
    }
    archive = zipped(tmp_path / "aside.zip", entries)
    found = importing.read(OWNER, [archive], setup, tmp_path / "unpacked")
    assert [f.name for f in found.files] == ["aside.zip/notes.txt"]


def test_a_zip_in_a_zip_is_refused_and_the_rest_read(
    folder: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    inner = zipped(tmp_path / "inner.zip", {"notes.txt": b"not a statement"}).read_bytes()
    entries = {"inner.ZIP": inner, "Migros.csv": (folder / "Migros.csv").read_bytes()}
    archive = zipped(tmp_path / "aside.zip", entries)
    found = importing.read(OWNER, [archive], setup, tmp_path / "unpacked")
    (refused,) = found.refused
    assert (refused.name, refused.path) == ("aside.zip/inner.ZIP", archive)
    assert refused.problem == "a zip inside a zip is not opened: unzip it"
    assert by_name(found)["aside.zip/Migros.csv"].recognised


def _flagged(archive: Path, offset: int, value: int) -> None:
    """Set a byte of the first entry the zip's directory lists: its flags (8) or method (10)."""
    content = bytearray(archive.read_bytes())
    content[content.index(b"PK\x01\x02") + offset] = value
    archive.write_bytes(bytes(content))


def test_an_encrypted_entry_is_refused_on_its_own(setup: importing.Setup, tmp_path: Path) -> None:
    archive = zipped(tmp_path / "aside.zip", {"secret.csv": b"x", "notes.txt": b"n"})
    _flagged(archive, 8, archives._ENCRYPTED)
    found = importing.read(OWNER, [archive], setup, tmp_path / "unpacked")
    assert [(f.name, f.problem) for f in found.files] == [
        ("aside.zip/notes.txt", ""),
        ("aside.zip/secret.csv", "encrypted: unzip it and import the files"),
    ]


def test_an_entry_compressed_in_an_unknown_way_is_refused_on_its_own(
    setup: importing.Setup, tmp_path: Path
) -> None:
    archive = zipped(tmp_path / "aside.zip", {"odd.csv": b"x", "notes.txt": b"n"})
    _flagged(archive, 10, 99)
    found = importing.read(OWNER, [archive], setup, tmp_path / "unpacked")
    assert by_name(found)["aside.zip/odd.csv"].problem.startswith("compressed in a way")
    assert not by_name(found)["aside.zip/notes.txt"].problem


def test_a_broken_zip_is_refused_and_stops_no_other_file(
    folder: Path, setup: importing.Setup, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip")
    cut = zipped(tmp_path / "cut.zip", statements(folder))
    cut.write_bytes(cut.read_bytes()[:-30])
    found = importing.read(OWNER, [broken, cut, folder / "Migros.csv"], setup, tmp_path / "out")
    refused = {f.name: f.problem for f in found.refused}
    assert set(refused) == {"broken.zip", "cut.zip"}
    assert refused["broken.zip"].startswith("not a zip that can be opened")
    assert by_name(found)["Migros.csv"].recognised


def test_a_zip_bomb_is_refused_as_a_whole_while_it_is_read(
    setup: importing.Setup, tmp_path: Path
) -> None:
    bomb = zipped(tmp_path / "bomb.zip", {"a.txt": b"n", "zeros.csv": bytes(archives.ENTRY + 1)})
    assert bomb.stat().st_size < archives.MB  # a few bytes on disk
    found = importing.read(OWNER, [bomb], setup, tmp_path / "unpacked")
    assert [(f.name, f.problem) for f in found.files] == [
        ("bomb.zip", "bomb.zip/zeros.csv unpacks to more than 50 MB: nothing of it was read")
    ]


def test_a_zip_that_unpacks_to_too_much_altogether_is_refused(
    setup: importing.Setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(archives, "TOTAL", 1500)
    archive = zipped(tmp_path / "many.zip", {"a.txt": bytes(1000), "b.txt": bytes(1000)})
    found = importing.read(OWNER, [archive], setup, tmp_path / "unpacked")
    assert [f.problem for f in found.files] == [
        "it unpacks to more than 0 MB: nothing of it was read"
    ]


def test_a_zip_of_too_many_entries_is_refused(
    setup: importing.Setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(archives, "ENTRIES", 2)
    archive = zipped(tmp_path / "many.zip", {"a.txt": b"a", "b.txt": b"b", "c/": b""})
    found = importing.read(OWNER, [archive], setup, tmp_path / "unpacked")
    assert [f.problem for f in found.files] == [
        "it holds more than 2 entries: nothing of it was read"
    ]


def test_files_of_one_name_are_told_apart_by_a_number(
    setup: importing.Setup, tmp_path: Path
) -> None:
    for side in ("a", "b"):
        (tmp_path / side).mkdir()
    entries = {
        "2025/notes.txt": b"one",
        "2026/notes.txt": b"two",
        "notes (2).txt": b"three",
        "2027/notes.txt": b"five",
    }
    first = zipped(tmp_path / "a" / "aside.zip", entries)
    second = zipped(tmp_path / "b" / "aside.zip", {"notes.txt": b"four"})
    found = importing.read(OWNER, [first, second], setup, tmp_path / "unpacked")
    assert {f.name: f.path.read_bytes() for f in found.files} == {
        "a/aside.zip/notes.txt": b"one",
        "a/aside.zip/notes (2).txt": b"two",
        "a/aside.zip/notes (2) (2).txt": b"three",
        "a/aside.zip/notes (3).txt": b"five",
        "b/aside.zip/notes.txt": b"four",
    }


def test_an_entry_is_hidden_when_anything_on_its_path_is() -> None:
    assert archives.hidden("a/.b/c.csv")
    assert archives.hidden("__MACOSX/c.csv")
    assert archives.hidden("folder/")
    assert not archives.hidden("folder/c.csv")
    assert archives.is_zip(Path("A.ZIP"))
    assert not archives.is_zip(Path("a.zip.csv"))


def test_the_zip_is_read_in_memory_whatever_its_header_says(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", b"0123456789")
    content = bytearray(buffer.getvalue())
    # The size the directory states, made smaller than what the entry holds.
    at = content.index(b"PK\x01\x02") + 24
    content[at : at + 4] = (3).to_bytes(4, "little")
    lying = tmp_path / "lying.zip"
    lying.write_bytes(bytes(content))
    (entry,) = archives.unpack(lying, "lying.zip", tmp_path / "out", set())
    assert entry.problem.startswith("not a zip that can be opened")
