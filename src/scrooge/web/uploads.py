"""The files of an import, from the browser to what is staged: for the server's import page
and for the API of the app's.

An import that stops on bills without a maturity keeps its files for a while (`hold`), in
memory like the sessions, so that once the bills are looked up the same files are imported
again without being chosen again.
"""

from __future__ import annotations

import secrets
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from starlette.datastructures import State

from scrooge import importing, staging
from scrooge.classification import UnknownInstrumentError
from scrooge.errors import ConfigError, ExtractionError
from scrooge.storage import db, store
from scrooge.storage.money import PrecisionError
from scrooge.validation import ReconciliationError

UPLOADS = 200  # files taken by one import: a year of monthly statements of every source
HELD = timedelta(hours=1)  # how long the files of an import stopped on bills are kept
# What stops an import: nothing is stored, and the page says why.
REFUSED = (
    ExtractionError,
    ReconciliationError,
    ConfigError,
    UnknownInstrumentError,
    staging.StagingError,
    PrecisionError,
    store.UnknownCategoryError,
    store.AccountMismatchError,
)


@dataclass(frozen=True, slots=True)
class Held:
    """The files of an import that stopped on bills without a maturity."""

    owner: str
    uploads: list[tuple[str, bytes]]
    bills: tuple[str, ...]
    until: datetime


def name(sent: str) -> str:
    """The name an upload is kept under: the last part of the one the browser sent, or
    `unnamed` when nothing usable is left (`..` would be the folder itself)."""
    last = Path(sent).name
    return last if last not in {"", ".", ".."} else "unnamed"


def saved(folder: Path, uploads: Sequence[tuple[str, bytes]]) -> list[Path]:
    """Write each upload under the name it came with. Files that share a name each go in a
    numbered folder of their own, which is how the import tells them apart."""
    names = [named for named, _ in uploads]
    files: list[Path] = []
    for number, (named, content) in enumerate(uploads, start=1):
        path = folder / str(number) / named if names.count(named) > 1 else folder / named
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        files.append(path)
    return files


def stage(state: State, owner: str, uploads: Sequence[tuple[str, bytes]]) -> importing.Read:
    """Read `uploads` as statements of `owner` and stage them; it blocks, so it is run in a
    thread. Raises what `REFUSED` lists, or `UnknownMaturityError`, when nothing is stored."""
    with tempfile.TemporaryDirectory() as folder:
        files = saved(Path(folder), uploads)
        found = importing.read(owner, files, state.imports)
        conn = db.connect(state.database)
        try:
            return importing.stage(conn, state.database, found, state.imports, state.clock())
        finally:
            conn.close()


def hold(state: State, held: Held) -> str:
    """Keep `held` for the button that looks its bills up; what has waited too long goes."""
    now = state.clock()
    for key in [key for key, kept in state.held.items() if kept.until <= now]:
        del state.held[key]
    key = secrets.token_urlsafe(16)
    state.held[key] = held
    return key
