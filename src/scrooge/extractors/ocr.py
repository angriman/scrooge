"""Text of a scanned PDF, read with Tesseract: offline, and the same text on every run (D9).

Pension certificates arrive as scans: each page is one image and there is no text layer.
Every page is rendered at the resolution it was scanned at and handed to the `tesseract`
program; language, page segmentation and resolution are fixed here so that nothing depends on
the machine's defaults. Nothing is corrected or guessed: what the extractors cannot read in
this text, they refuse.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pdfplumber
from pdfplumber.utils.exceptions import PdfminerException

from scrooge.errors import ExtractionError, OcrUnavailableError

PROGRAM = "tesseract"
# The digits and labels of the German certificate read correctly with the English data too
# (only umlauts come out wrong, and the extractors do not depend on them).
LANGUAGE = "eng"
DPI = 300  # an A4 page scanned at 300 dpi is 2480 x 3508 pixels
_PAGE_AS_ONE_BLOCK = "6"  # keeps a table row on one line, where the default splits columns


def is_scan(path: Path) -> bool:
    """Whether the PDF is images of paper: it has pages and none carries a single character.

    Cheap next to reading it: the pages are only parsed, up to the first one with text. A
    file that is no PDF after all is no scan either.
    """
    try:
        with pdfplumber.open(path) as pdf:
            return bool(pdf.pages) and not any(page.chars for page in pdf.pages)
    except PdfminerException:
        return False


def read_text(path: Path) -> str:
    """OCR text of every page of the PDF, in order, one page after the other."""
    program = shutil.which(PROGRAM)
    if program is None:
        msg = (
            f"{path.name} is a scan and reading it needs Tesseract, which is not installed "
            f"(no `{PROGRAM}` on PATH): install it, e.g. `make ocr` in the project folder "
            "(then run scrooge through ./uv or make), `brew install tesseract` or "
            "`apt install tesseract-ocr`"
        )
        raise OcrUnavailableError(msg)
    with pdfplumber.open(path) as pdf:
        return "\n".join(
            _read_page(program, page, f"{path.name} page {n}")
            for n, page in enumerate(pdf.pages, 1)
        )


def _read_page(program: str, page: pdfplumber.page.Page, what: str) -> str:
    image = io.BytesIO()
    page.to_image(resolution=DPI).original.convert("L").save(image, format="PNG")
    command = [program, "stdin", "stdout", "-l", LANGUAGE, "--psm", _PAGE_AS_ONE_BLOCK]
    # No shell, and the program is the one found on PATH above with fixed arguments.
    done = subprocess.run(  # noqa: S603
        [*command, "--dpi", str(DPI)], input=image.getvalue(), capture_output=True, check=False
    )
    if done.returncode != 0:
        reason = done.stderr.decode("utf-8", errors="replace").strip()
        msg = f"Tesseract could not read {what}: {reason}"
        raise ExtractionError(msg)
    return done.stdout.decode("utf-8")
