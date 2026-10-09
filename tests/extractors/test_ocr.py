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
