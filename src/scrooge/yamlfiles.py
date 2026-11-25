"""How the YAML files the app changes (the rules files, `categories.yaml`) are written: one
change at a time, and each file whole or not at all.

The import page remembers rules, the settings page edits them, and both run in threads of one
server: whoever writes holds `LOCK` from the moment it reads a file to the moment it is done
with it, so that no change is made on a text another one is replacing, and a change put back
after a failure never takes another one with it. The lock is reentrant: an operation that holds
it may call a writer that takes it again. It is a lock of one process: `scrooge import
--review` run by hand at the same time is not held back.

A file is written next to itself under another name (`.<name>.<random>.tmp`), with the mode of
the file it replaces, flushed to the disk, put in its place in one step, and the folder flushed
too: a crash leaves the old file or the new one, never half of one. A crash before that step
can leave the `.tmp` file behind; nothing reads it, and it can be deleted. A file that is a
link is written where the link points, and stays a link.
"""

from __future__ import annotations

import os
import stat
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path

LOCK = threading.RLock()
_NEW = 0o644  # the mode of a file written for the first time


def write(path: Path, text: str) -> None:
    """Replace `path` with `text`, as UTF-8, at once."""
    write_bytes(path, text.encode("utf-8"))


def write_bytes(path: Path, data: bytes) -> None:
    """Replace `path` with `data` at once."""
    target = path.resolve() if path.is_symlink() else path
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else _NEW
    with LOCK:
        handle, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(data)
                file.flush()
                os.fchmod(file.fileno(), mode)
                os.fsync(file.fileno())
            Path(name).replace(target)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
        folder = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


def snapshot(path: Path) -> bytes | None:
    """What `path` holds, byte for byte; None when there is no such file."""
    return path.read_bytes() if path.exists() else None


def put_back(before: Mapping[Path, bytes | None]) -> list[str]:
    """Put each file of `before` back as it was there (gone, if it was), where it differs now:
    a file nothing wrote is left alone. Every file is tried; returns those that could not be
    put back, as `name (why)`."""
    failed: list[str] = []
    for path, data in before.items():
        try:
            if snapshot(path) == data:
                continue
            if data is None:
                path.unlink()
            else:
                write_bytes(path, data)
        except OSError as exc:
            failed.append(f"{path.name} ({exc})")
    return failed
