"""How the YAML files the app changes (the rules files, `categories.yaml`) are written: one
change at a time, and each file whole or not at all.

The import page remembers rules, the settings page edits them, and both run in threads of one
server: whoever writes holds `LOCK` from the moment it reads a file to the moment it is done
with it, so that no change is made on a text another one is replacing, and a change put back
after a failure never takes another one with it. The lock is reentrant: an operation that holds
it may call a writer that takes it again. It is a lock of one process: `scrooge import
--review` run by hand at the same time is not held back.

A file is written next to itself under another name, flushed to the disk, then put in its
place in one step: a crash leaves the old file or the new one, never half of one.
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

LOCK = threading.RLock()


def write(path: Path, text: str) -> None:
    """Replace `path` with `text` at once."""
    with LOCK:
        handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                file.write(text)
                file.flush()
                os.fsync(file.fileno())
            Path(name).replace(path)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
