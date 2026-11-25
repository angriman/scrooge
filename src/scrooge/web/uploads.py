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

from scrooge import classification, importing, marketdata, staging
from scrooge.classification import UnknownInstrumentError
from scrooge.errors import ConfigError, ExtractionError
from scrooge.prices import PriceError
from scrooge.storage import db, store
from scrooge.storage.money import PrecisionError
from scrooge.validation import ReconciliationError
from scrooge.web import wire

# Files taken by one import: a folder of a few years of monthly statements of every source.
UPLOADS = 500
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
    with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as unpacked:
        files = saved(Path(folder), uploads)
        found = importing.read(owner, files, state.imports, Path(unpacked))
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


class LookupRefusedError(Exception):
    """Looking up the bills of a held import failed; `held` is None when the files are gone."""

    def __init__(self, status: int, message: str, held: Held | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.held = held


def look_up(state: State, key: str) -> tuple[Held, str]:
    """Look up when the bills of `key` mature, write them into `instruments.yaml`, and drop
    `key`; runs in a thread. Returns `(held, notice)`."""
    held: Held | None = state.held.get(key)
    if held is None or held.until <= state.clock():
        raise LookupRefusedError(
            409, "The files of that import are no longer kept: choose them again."
        )
    try:
        looked = marketdata.maturities(state.maturities, held.bills)
    except PriceError as exc:
        message = (
            f"TreasuryDirect could not tell: {exc}. Try again later, or add "
            "the maturity to instruments.yaml."
        )
        raise LookupRefusedError(502, message, held) from None
    if looked.unknown:
        unknown = ", ".join(looked.unknown)
        message = f"TreasuryDirect knows no bill {unknown}: add its maturity to instruments.yaml."
        raise LookupRefusedError(409, message, held)
    try:
        classification.add_maturities(state.imports.instruments, looked.found)
    except ConfigError as exc:
        raise LookupRefusedError(409, wire.sentence(exc), held) from None
    state.held.pop(key, None)
    days = "; ".join(f"{bill} matures on {day}" for bill, day in looked.found.items())
    return held, f"{days} (TreasuryDirect), written in instruments.yaml."
