"""The YAML files the app changes are written whole, one change at a time."""

import os
import threading
from pathlib import Path

import pytest

from scrooge import categories, classification, yamlfiles


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
            folder / categories.FILE, categories.Extra("Mance", "entrate")
        ),
    ],
)
def test_the_import_page_waits_for_a_change_in_progress(tmp_path: Path, write: object) -> None:
    done = threading.Event()

    def remember() -> None:
        write(tmp_path)  # type: ignore[operator]  # one of the writers above
        done.set()

    with yamlfiles.LOCK:
        writer = threading.Thread(target=remember)
        writer.start()
        assert not done.wait(0.2)
        assert list(tmp_path.iterdir()) == []
    writer.join(5)
    assert done.is_set()
    assert len(list(tmp_path.iterdir())) == 1
