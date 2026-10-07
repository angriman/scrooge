"""Detect which source a statement file comes from and run its extractor (step 2 of the flow)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from finanze import records
from finanze.errors import ExtractionError
from finanze.extractors import (
    finpension,
    ibkr,
    migros,
    revolut,
    revolut_savings,
    swisscard,
    tastytrade,
)
from finanze.models import Extraction

_Extractor = Callable[[str, str], Extraction]
# (source name, what the text starts with, extractor)
_TEXT_SOURCES: tuple[tuple[str, tuple[str, ...], _Extractor], ...] = (
    ("ibkr", ("Statement,Header,",), ibkr.extract),
    ("tastytrade", ("Date,Type,Sub Type,",), tastytrade.extract),
    ("revolut", ("Type\tProduct\t", "Type,Product,"), revolut.extract),
    ("revolut_savings", ("Date\tDescription\tValue, ",), revolut_savings.extract),
    ("migros", ('"Estratto conto da:"', "Estratto conto da:;"), migros.extract),
    (
        "finpension",
        ("Date\tCategory\tAsset Name\t", "Date;Category;Asset Name;", "Date,Category,Asset Name,"),
        finpension.extract,
    ),
)
# A UTF-8 byte-order mark that was read as Latin-1 and saved again (seen on re-saved exports).
_GARBLED_MARK = "\u00ef\u00bb\u00bf"
_TEXT_SUFFIXES = {".csv", ".tsv"}
# Header of a `--dump` file: older runs saved it next to the statements.
_OWN_DUMP = ",".join(records.FIELDS)


def load(path: Path, name: str = "", *, database: bool = False) -> tuple[str, Extraction] | None:
    """Extract one statement file. Returns None for files that are not statements.

    `name` is what the transactions call their source file: the file's own name unless given.
    For the `database`, a card invoice is a statement of the card's own account; for the
    files to paste, its purchases are booked on the bank account (D3).

    A .csv/.tsv is always taken for a statement: one that no extractor recognises raises, since
    skipping it would drop a whole account from the month without a word.
    """
    name = name or path.name
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        if "swisscard" not in path.name.lower():
            return None  # e.g. the PDF copy of the IBKR statement
        read = swisscard.extract_account if database else swisscard.extract
        return "swisscard", read(swisscard.read_text(path), name)
    if suffix not in _TEXT_SUFFIXES:
        return None
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        msg = "not UTF-8 text (an old export format?): export the statement again"
        raise ExtractionError(msg) from None
    text = text.removeprefix(_GARBLED_MARK)
    for source, starts, extract in _TEXT_SOURCES:
        if text.startswith(starts):
            return source, extract(text, name)
    if text.startswith(_OWN_DUMP):
        return None
    msg = "not a statement format this parser knows: export it again or move it out of the folder"
    raise ExtractionError(msg)
