"""The statement files themselves, kept next to the database that was filled from them.

`statement_files` says which file every row came from, by its SHA-256. Here are the bytes: one
file per digest in a folder beside the database, named by the digest (and the extension the
file came with), so that the same file kept twice is one file and a name says nothing about
anyone. Nothing is ever changed or removed here: a file a later export replaced stays.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path

FOLDER = "originals"


def folder_of(database: Path) -> Path:
    """Where the originals of `database` are kept."""
    return database.parent / FOLDER


def find(folder: Path, sha256: str, filename: str) -> Path | None:
    """The original of the statement with this digest; None when it was never kept here."""
    path = _place(folder, sha256, filename)
    return path if path.is_file() else None


def keep(folder: Path, paths: Iterable[Path], known: Mapping[str, str]) -> list[Path]:
    """Copy into `folder` those of `paths` the database was filled from; returns them.

    `known` maps the digest of every stored statement to its name. A file with another digest
    is no statement of the database and is left alone, as is one that is kept already. The
    folder is created with the first file kept.
    """
    kept: list[Path] = []
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest not in known:
            continue
        place = _place(folder, digest, known[digest])
        if place.is_file():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        part = place.with_name(f".{place.name}.part")  # never half a file under its name
        shutil.copyfile(path, part)
        os.replace(part, place)
        kept.append(path)
    return kept


def _place(folder: Path, sha256: str, filename: str) -> Path:
    return folder / f"{sha256}{Path(filename).suffix.lower()}"
