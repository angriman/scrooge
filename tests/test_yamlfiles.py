"""The YAML files the app changes are written whole, one change at a time."""

import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from scrooge import categories, classification, staging, yamlfiles


def test_a_file_keeps_its_mode_and_a_link_stays_a_link(tmp_path: Path) -> None:
    kept = tmp_path / "kept"
    kept.mkdir()
    target = kept / "rules.yaml"
    target.write_text("rules:\n", encoding="utf-8")
    target.chmod(0o640)
    link = tmp_path / "rules.yaml"
    link.symlink_to(target)
    yamlfiles.write(link, "rules: []\n")
    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "rules: []\n"
    assert target.stat().st_mode & 0o777 == 0o640
    fresh = tmp_path / "categories.yaml"
    yamlfiles.write(fresh, "categories: {}\n")
    assert fresh.stat().st_mode & 0o777 == 0o644


def test_only_what_changed_is_put_back_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crlf, same, new = tmp_path / "a.yaml", tmp_path / "b.yaml", tmp_path / "c.yaml"
    crlf.write_bytes(b"rules:\r\n  - {match: a, category: Spesa}\r\n")
    same.write_bytes(b"rules:\n")
    before = {path: yamlfiles.snapshot(path) for path in (crlf, same, new)}
    crlf.write_text("rules:\n", encoding="utf-8")
    new.write_text("rules:\n", encoding="utf-8")
    written: list[Path] = []
    real = yamlfiles.write_bytes

    def counted(path: Path, data: bytes) -> None:
        written.append(path)
        real(path, data)

    monkeypatch.setattr(yamlfiles, "write_bytes", counted)
    assert yamlfiles.put_back(before) == []
    assert crlf.read_bytes() == b"rules:\r\n  - {match: a, category: Spesa}\r\n"
    assert not new.exists()
    assert written == [crlf]  # the file nothing wrote was left alone


def test_a_file_that_cannot_be_put_back_does_not_stop_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = tmp_path / "a.yaml", tmp_path / "b.yaml"
    first.write_text("a\n", encoding="utf-8")
    second.write_text("b\n", encoding="utf-8")
    before = {first: yamlfiles.snapshot(first), second: None}
    first.write_text("changed\n", encoding="utf-8")

    def unwritable(*_: object) -> None:
        raise PermissionError("read-only folder")

    monkeypatch.setattr(yamlfiles, "write_bytes", unwritable)
    assert yamlfiles.put_back(before) == ["a.yaml (read-only folder)"]
    assert not second.exists()


def test_staging_says_what_could_not_be_put_back_after_what_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text("rules:\n", encoding="utf-8")

    def unwritable(*_: object) -> None:
        raise PermissionError("read-only folder")

    def remember() -> None:
        with staging._rules_kept() as before:
            before[path] = yamlfiles.snapshot(path)
            path.write_text("rules: []\n", encoding="utf-8")
            monkeypatch.setattr(yamlfiles, "write_bytes", unwritable)
            raise ValueError("boom")

    with pytest.raises(staging.StagingError, match=r"^boom; and rules\.yaml \(read-only folder\)"):
        remember()


def test_a_file_is_replaced_whole_and_leaves_nothing_beside_it(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text("rules:\n", encoding="utf-8")
    yamlfiles.write(path, "rules:\n  - {match: a, category: Groceries}\n")
    assert path.read_text(encoding="utf-8") == "rules:\n  - {match: a, category: Groceries}\n"
    assert [p.name for p in tmp_path.iterdir()] == ["rules.yaml"]


def test_a_write_that_fails_leaves_the_file_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text("rules:\n", encoding="utf-8")

    def full(_: int) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(os, "fsync", full)
    with pytest.raises(OSError, match="no space"):
        yamlfiles.write(path, "rules: []\n")
    assert path.read_text(encoding="utf-8") == "rules:\n"
    assert [p.name for p in tmp_path.iterdir()] == ["rules.yaml"]


@pytest.mark.parametrize(
    "write",
    [
        lambda folder: classification.append_rule(
            folder / "rules.yaml", "ACME", "in", categories.SALARY
        ),
        lambda folder: categories.add(
            folder / categories.FILE, categories.Extra("Mance", "income")
        ),
    ],
)
def test_the_import_page_waits_for_a_change_in_progress(
    tmp_path: Path, write: Callable[[Path], object]
) -> None:
    done = threading.Event()

    def remember() -> None:
        write(tmp_path)
        done.set()

    with yamlfiles.LOCK:
        writer = threading.Thread(target=remember)
        writer.start()
        assert not done.wait(0.2)
        assert list(tmp_path.iterdir()) == []
    writer.join(5)
    assert done.is_set()
    assert len(list(tmp_path.iterdir())) == 1
