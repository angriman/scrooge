"""What every extractor module declares: the one interface the router knows them by.

Each module in `scrooge.extractors`, apart from the helpers (`common`, `ocr`, this one), ends
with `SOURCE = Source(...)` for a statement or `SOURCE = CertificateSource(...)` for a scanned
pension certificate, and `router.SOURCES` lists it. The router reads the file (`reads`), asks
each source in turn whether it `recognises` the text, and runs the first one's `extract`.
`tests/extractors/test_sources.py` refuses a module that does not follow this.

A source recognises a file by its content alone, never by its name. `extract` is a pure
function of the text: no network, no files, no global state, no `print`; it raises an
`ExtractionError` for anything it cannot read, never guesses. What a statement's `extract`
returns has something to reconcile its rows against (`balances`, `controls`, or the accounts
it prints nothing for in `unreconciled`), or the router refuses it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from scrooge.models import Extraction, PensionCertificate

# How the router turns the file into the text a source reads: a CSV/TSV export as UTF-8, or
# the text layer of a PDF (pdfplumber). A scan has no text layer: see `CertificateSource`.
Reads = Literal["text", "pdf"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Source:
    """A kind of statement: an export or a PDF with its transactions and the figures to check."""

    name: str  # the module's name; stored with every statement file (never change it)
    label: str  # what its files are called on the import page and in `scrooge import`
    reads: Reads
    recognises: Callable[[str], bool]  # whether the text is one of its statements
    extract: Callable[[str, str], Extraction]  # (text, source file name) -> what it states


@dataclass(frozen=True, slots=True, kw_only=True)
class CertificateSource:
    """A pension fund's certificate, scanned: read with OCR, it states one balance on one day."""

    name: str  # the module's name; stored with every certificate (never change it)
    label: str
    account: str  # the pension account it states the balance of
    adds_up: bool  # its own figures are checked against each other; else the reader compares
    recognises: Callable[[str], bool]
    extract: Callable[[str], PensionCertificate]


def starts_with(*headers: str) -> Callable[[str], bool]:
    """Recognise an export by how its text begins: its header row (or one of its variants)."""

    def recognises(text: str) -> bool:
        return text.startswith(headers)

    return recognises
