"""The statements inside a zip: each one read as if it had been given on its own.

A file is a zip by its name (`.zip`); what is inside is recognised by its content, like any
other file. The entries are read in memory and written under names made here, each in a
folder of its own: the name an entry has in the zip is only shown, never used as a path, so
`../x.csv` cannot land anywhere but there.

A zip that is broken, holds too many files or unpacks to too much is refused as a whole, and
the other files of the import go on. The bytes are counted as they are read, whatever the zip
says of its sizes. An entry that is a zip itself, or encrypted, is refused on its own.
Folders, hidden files and what a Mac adds (`__MACOSX/`) are left out.
"""

from __future__ import annotations

import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath

SUFFIX = ".zip"
ENTRIES = 500  # entries in one zip, folders and files left out included
MB = 1024 * 1024
TOTAL = 200 * MB  # what all the entries of one zip unpack to
ENTRY = 50 * MB  # what one entry unpacks to: no statement is near
_CHUNK = 64 * 1024
_ENCRYPTED = 0x1  # the flag of an encrypted entry
_MAC = "__MACOSX"  # the folder of resource forks a zip made on a Mac holds


@dataclass(frozen=True, slots=True)
class Entry:
    """A file of a zip: where it was written, and its name, `archive.zip/inner.csv`. One
    refused has the zip as its path and `problem` says why."""

    path: Path
    name: str
    problem: str = ""


class _TooBigError(Exception):
    """The zip is refused as a whole; the message says why."""


def is_zip(path: Path) -> bool:
    return path.suffix.lower() == SUFFIX


def hidden(name: str) -> bool:
    """Whether an entry is left out: a folder, or something hidden on its path. `..` is no
    hidden folder: such an entry is shown, and written in its own folder like any other."""
    parts = name.replace("\\", "/").split("/")
    return parts[-1] in {"", ".", ".."} or any(
        part == _MAC or (part.startswith(".") and part not in {".", ".."}) for part in parts
    )


def unpack_all(named: Mapping[Path, str], folder: Path) -> tuple[dict[Path, str], list[Entry]]:
    """`named` (path -> name) with each zip replaced by its files, written under `folder`,
    and the zips and files of zips refused. What `scrooge import` and `scrooge keep` read."""
    files = {path: name for path, name in named.items() if not is_zip(path)}
    taken = set(named.values())
    refused: list[Entry] = []
    zips = sorted((name, path) for path, name in named.items() if is_zip(path))
    for number, (name, path) in enumerate(zips, start=1):
        for entry in unpack(path, name, folder / str(number), taken):
            if entry.problem:
                refused.append(entry)
            else:
                files[entry.path] = entry.name
    return files, refused


def unpack(path: Path, name: str, folder: Path, taken: set[str]) -> list[Entry]:
    """The files of the zip at `path`, called `name`, written under `folder`. Every name
    given is unique and added to `taken`, the names the import has already."""
    try:
        return _unpacked(path, name, folder, taken)
    except _TooBigError as exc:
        return [Entry(path, name, f"{exc}: nothing of it was read")]
    except (zipfile.BadZipFile, EOFError, OSError) as exc:
        return [Entry(path, name, f"not a zip that can be opened ({exc})")]


def _unpacked(path: Path, name: str, folder: Path, taken: set[str]) -> list[Entry]:
    entries: list[Entry] = []
    names: set[str] = set()
    total = 0
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > ENTRIES:
            msg = f"it holds more than {ENTRIES} entries"
            raise _TooBigError(msg)
        for number, info in enumerate(infos, start=1):
            if info.is_dir() or hidden(info.filename):
                continue
            base = PurePath(info.filename.replace("\\", "/")).name
            shown = _unique(f"{name}/{base}", taken | names)
            names.add(shown)
            if info.flag_bits & _ENCRYPTED:
                entries.append(Entry(path, shown, "encrypted: unzip it and import the files"))
                continue
            if is_zip(Path(base)):
                entries.append(Entry(path, shown, "a zip inside a zip is not opened: unzip it"))
                continue
            try:
                content = _read(archive, info, shown, TOTAL - total)
            except NotImplementedError:
                problem = "compressed in a way that cannot be read: unzip it and import the files"
                entries.append(Entry(path, shown, problem))
                continue
            total += len(content)
            place = folder / str(number) / base
            place.parent.mkdir(parents=True)
            place.write_bytes(content)
            entries.append(Entry(place, shown))
    taken.update(names)
    return entries


def _read(archive: zipfile.ZipFile, info: zipfile.ZipInfo, shown: str, left: int) -> bytes:
    """The bytes of an entry, counted as they come: at most `ENTRY`, and `left` of the zip."""
    read = bytearray()
    with archive.open(info) as stream:
        while chunk := stream.read(_CHUNK):
            read += chunk
            if len(read) > ENTRY:
                msg = f"{shown} unpacks to more than {ENTRY // MB} MB"
                raise _TooBigError(msg)
            if len(read) > left:
                msg = f"it unpacks to more than {TOTAL // MB} MB"
                raise _TooBigError(msg)
    return bytes(read)


def _unique(name: str, taken: set[str]) -> str:
    """`name`, or with a number before its extension when another file has it already."""
    if name not in taken:
        return name
    path = PurePath(name)
    number = 2
    while (found := str(path.with_name(f"{path.stem} ({number}){path.suffix}"))) in taken:
        number += 1
    return found
