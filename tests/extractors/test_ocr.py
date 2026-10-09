"""Reading the scanned fixtures with the real Tesseract: nothing here is faked but its absence."""

import shutil
from pathlib import Path

import pytest

from finanze.errors import ExtractionError, OcrUnavailableError
from finanze.extractors import axa, ocr, servisa
from tests.extractors.golden import FIXTURES, assert_certificate_matches_golden

AXA = FIXTURES / "axa" / "certificate.pdf"
SERVISA = FIXTURES / "servisa" / "certificate.pdf"


@pytest.fixture(scope="module")
def axa_text() -> str:
    return ocr.read_text(AXA)


def test_the_axa_scan_reads_as_its_golden_file(axa_text: str) -> None:
    assert axa.recognises(axa_text)
    assert_certificate_matches_golden(axa.extract(axa_text), "axa")


def test_the_servisa_scan_reads_as_its_golden_file() -> None:
    text = ocr.read_text(SERVISA)
    assert servisa.recognises(text)
    assert not axa.recognises(text)
    assert_certificate_matches_golden(servisa.extract(text), "servisa")


def test_a_rerun_reads_the_same_text(axa_text: str) -> None:
    assert ocr.read_text(AXA) == axa_text


def test_every_page_is_read_in_order() -> None:
    text = ocr.read_text(SERVISA)
    assert text.index("Seite 1/2") < text.index("Vorsorgeausweis g") < text.index("Seite 2/2")


def test_without_tesseract_it_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _program: None)
    with pytest.raises(OcrUnavailableError, match=r"certificate\.pdf is a scan .* not installed"):
        ocr.read_text(AXA)


def test_a_failing_tesseract_is_an_error_not_an_empty_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "LANGUAGE", "no-such-language")
    with pytest.raises(ExtractionError, match=r"could not read certificate\.pdf page 1: .+"):
        ocr.read_text(AXA)


def test_a_missing_file_is_not_hidden(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ocr.read_text(tmp_path / "none.pdf")


def written_pdf(*pages: str) -> bytes:
    """A PDF made by hand, each page with the given line of text (none for "")."""
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    kids = " ".join(f"{4 + 2 * n} 0 R" for n in range(len(pages)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
        font,
    ]
    for n, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {5 + 2 * n} 0 R "
            "/Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\n" % (len(objects) + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % start
    return bytes(out)


def test_a_pdf_without_any_text_is_a_scan(tmp_path: Path) -> None:
    assert ocr.is_scan(AXA)
    assert ocr.is_scan(SERVISA)  # two pages
    written = tmp_path / "written.pdf"
    written.write_bytes(written_pdf("", "Pension fund certificate", ""))
    assert not ocr.is_scan(written)  # one page with text is enough
    written.write_bytes(written_pdf("", ""))
    assert ocr.is_scan(written)
    written.write_bytes(written_pdf())
    assert not ocr.is_scan(written)  # no page at all


def test_a_file_that_is_no_pdf_after_all_is_no_scan(tmp_path: Path) -> None:
    broken = tmp_path / "ibkr.pdf"
    broken.write_bytes(b"%PDF")
    assert not ocr.is_scan(broken)
