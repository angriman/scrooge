"""Detect which source a statement file comes from and run its extractor (step 2 of the flow)."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from pdfplumber.utils.exceptions import PdfminerException

from scrooge.errors import ExtractionError
from scrooge.extractors import (
    axa,
    finpension,
    ibkr,
    migros,
    morganstanley,
    ocr,
    revolut,
    revolut_savings,
    schwab,
    servisa,
    swisscard,
    tastytrade,
)
from scrooge.extractors.source import CertificateSource, Reads, Source
from scrooge.models import Extraction, PensionCertificate

# Every source of statements, in the order they are asked whether a file is theirs.
SOURCES: tuple[Source, ...] = (
    ibkr.SOURCE,
    tastytrade.SOURCE,
    revolut.SOURCE,
    revolut_savings.SOURCE,
    migros.SOURCE,
    schwab.SOURCE,
    finpension.SOURCE,
    swisscard.SOURCE,
    morganstanley.SOURCE,
)
# Every issuer of the scanned pension certificates.
CERTIFICATES: tuple[CertificateSource, ...] = (axa.SOURCE, servisa.SOURCE)
# What each source's files are called on the import page and in `scrooge import`.
_ALL: tuple[Source | CertificateSource, ...] = (*SOURCES, *CERTIFICATES)
LABELS = {source.name: source.label for source in _ALL}
# A UTF-8 byte-order mark that was read as Latin-1 and saved again (seen on re-saved exports).
_GARBLED_MARK = "\u00ef\u00bb\u00bf"
_TEXT_SUFFIXES = {".csv", ".tsv"}


class Scanned(NamedTuple):
    """A pension certificate read off a scan, and what its issuer's extractor says of it."""

    account: str  # the pension account it states the balance of
    adds_up: bool  # its own figures were checked against each other
    certificate: PensionCertificate


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
    for source in CERTIFICATES:
        if source.recognises(text):
            return Scanned(source.account, source.adds_up, source.extract(text))
    return None


def _pdf_statement(path: Path, name: str) -> tuple[str, Extraction] | None:
    """What the source that recognises the PDF's text extracts; None for any other PDF, and for
    one that is no PDF, unless its name says it is a card invoice: then it is refused."""
    if "swisscard" in path.name.lower():
        try:
            text = swisscard.read_text(path)
        except PdfminerException as exc:
            problem = f"{name} is named as a card invoice but is not a readable PDF"
            raise ExtractionError(problem) from exc
        return _extract(swisscard.SOURCE, text, name)
    if ocr.is_scan(path):  # images of paper, and a file that is no PDF after all, have no text
        return None
    try:
        text = swisscard.read_text(path)
    except PdfminerException:
        return None
    return _first(text, name, "pdf")


def load(path: Path, name: str = "") -> tuple[str, Extraction] | None:
    """Extract one statement file. Returns None for files that are not statements.

    `name` is what the transactions call their source file: the file's own name unless given.
    A card invoice is a statement of the card's own account.

    A .csv/.tsv is always taken for a statement: one that no extractor recognises raises, since
    skipping it would drop a whole account from the month without a word.

    A PDF is a statement when a source that reads PDFs recognises its text, or a card invoice
    when its name says so; any other PDF (the copy of the IBKR statement, a scan) is no
    statement here. `load_certificate` reads the scan of a pension certificate.
    """
    name = name or path.name
    suffix = path.suffix.lower()
    if _named_pdf(path):
        return _pdf_statement(path, name)
    if suffix not in _TEXT_SUFFIXES:
        return None
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        msg = "not UTF-8 text (an old export format?): export the statement again"
        raise ExtractionError(msg) from None
    text = text.removeprefix(_GARBLED_MARK)
    found = _first(text, name, "text")
    if found is not None:
        return found
    msg = "not a statement format this parser knows: export it again or move it out of the folder"
    raise ExtractionError(msg)


def _first(text: str, name: str, reads: Reads) -> tuple[str, Extraction] | None:
    """What the first source read this way that recognises the text extracts from it."""
    for source in SOURCES:
        if source.reads == reads and source.recognises(text):
            return _extract(source, text, name)
    return None


def _extract(source: Source, text: str, name: str) -> tuple[str, Extraction]:
    """What the source extracts from the text, refused when its rows cannot be checked.

    A statement must give its rows something to be checked against: the balances or totals it
    prints, or the accounts it prints nothing for, which the import then names.
    """
    extraction = source.extract(text, name)
    if not (extraction.balances or extraction.controls or extraction.unreconciled):
        msg = f"{source.label}: the extractor gives nothing to reconcile the rows against"
        raise ExtractionError(msg)
    return source.name, extraction
