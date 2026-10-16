"""Detect which source a statement file comes from and run its extractor (step 2 of the flow)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from finanze.errors import ExtractionError
from finanze.extractors import (
    axa,
    finpension,
    ibkr,
    migros,
    ocr,
    revolut,
    revolut_savings,
    servisa,
    swisscard,
    tastytrade,
)
from finanze.models import Extraction, PensionCertificate

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


class Scanned(NamedTuple):
    """A pension certificate read off a scan, and what its issuer's extractor says of it."""

    account: str  # the pension account it states the balance of
    adds_up: bool  # its own figures were checked against each other
    certificate: PensionCertificate


# (recognises the text of a scan, extractor, account, whether its figures add up)
_SCANS: tuple[tuple[Callable[[str], bool], Callable[[str], PensionCertificate], str, bool], ...] = (
    (axa.recognises, axa.extract, axa.ACCOUNT, axa.ADDS_UP),
    (servisa.recognises, servisa.extract, servisa.ACCOUNT, servisa.ADDS_UP),
)


def _is_pdf(path: Path) -> bool:
    with path.open("rb") as file:
        return file.read(5) == b"%PDF-"


def _named_pdf(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix == ".pdf" or (not suffix and _is_pdf(path))  # a PDF saved without its extension


def load_certificate(path: Path) -> Scanned | None:
    """Read a scanned pension certificate (second pillar). Returns None for any other file.

    Nothing depends on the file's name. Only a PDF without any text layer is a scan and is
    read with OCR, which takes about a second per page; a PDF with text never is. A scan that
    no issuer's extractor recognises is no statement, like any other PDF; one that is
    recognised and cannot be read cleanly raises.
    """
    if not _named_pdf(path) or not ocr.is_scan(path):
        return None
    text = ocr.read_text(path)
    for recognises, extract, account, adds_up in _SCANS:
        if recognises(text):
            return Scanned(account, adds_up, extract(text))
    return None


def load(path: Path, name: str = "", *, database: bool = False) -> tuple[str, Extraction] | None:
    """Extract one statement file. Returns None for files that are not statements.

    `name` is what the transactions call their source file: the file's own name unless given.
    For the `database`, a card invoice is a statement of the card's own account; for the
    files to paste, its purchases are booked on the bank account (D3).

    A .csv/.tsv is always taken for a statement: one that no extractor recognises raises, since
    skipping it would drop a whole account from the month without a word.

    A pension certificate is no statement of a month and is not read here: every PDF but a
    card invoice is skipped unopened, so a certificate never reaches the files to paste.
    `load_certificate` reads one, for the database.
    """
    name = name or path.name
    suffix = path.suffix.lower()
    if _named_pdf(path):
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
    msg = "not a statement format this parser knows: export it again or move it out of the folder"
    raise ExtractionError(msg)
