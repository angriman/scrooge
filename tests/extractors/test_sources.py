"""Every extractor follows the one interface of `scrooge.extractors.source` and is registered."""

import importlib
import pkgutil
from datetime import date
from pathlib import Path

import pytest

from scrooge import extractors, router
from scrooge.errors import ExtractionError
from scrooge.extractors.source import CertificateSource, Source, starts_with
from scrooge.models import Extraction
from tests.extractors.golden import FIXTURES

TESTS = Path(__file__).parent

HELPERS = {"common", "ocr", "source"}  # modules of the package that read no statement
MODULES = sorted(
    found.name for found in pkgutil.iter_modules(extractors.__path__) if found.name not in HELPERS
)
REGISTERED: tuple[Source | CertificateSource, ...] = (*router.SOURCES, *router.CERTIFICATES)


def _sample_texts(name: str) -> list[str]:
    """The redacted fixtures of a source, as the router hands them over: text, no PDF."""
    files = sorted((FIXTURES / name).iterdir())
    return [
        file.read_text(encoding="utf-8-sig")
        for file in files
        if file.name != "expected.json" and file.suffix != ".pdf"
    ]


def _read_alike(one: Source | CertificateSource, other: Source | CertificateSource) -> bool:
    """Whether the router could offer the same text to both: the only ones that may compete."""
    if isinstance(one, Source) and isinstance(other, Source):
        return one.reads == other.reads
    return isinstance(one, CertificateSource) and isinstance(other, CertificateSource)


@pytest.mark.parametrize("name", MODULES)
def test_each_extractor_declares_its_source_under_its_own_name(name: str) -> None:
    source = getattr(importlib.import_module(f"scrooge.extractors.{name}"), "SOURCE", None)
    assert isinstance(source, Source | CertificateSource), f"{name}.py declares no SOURCE"
    assert source.name == name
    assert source.label
    assert REGISTERED.count(source) == 1, f"{name}.SOURCE is not in router.SOURCES once"


def test_the_router_knows_no_source_without_a_module() -> None:
    assert sorted(source.name for source in REGISTERED) == MODULES
    assert len(set(router.LABELS.values())) == len(router.LABELS)


@pytest.mark.parametrize("source", REGISTERED, ids=lambda source: source.name)
def test_each_source_recognises_its_fixtures_and_no_one_else_does(
    source: Source | CertificateSource,
) -> None:
    texts = _sample_texts(source.name)
    assert texts, f"tests/fixtures/{source.name}/ has no redacted statement"
    rivals = [other for other in REGISTERED if _read_alike(other, source)]
    for text in texts:
        assert [other.name for other in rivals if other.recognises(text)] == [source.name]


@pytest.mark.parametrize("source", REGISTERED, ids=lambda source: source.name)
def test_each_source_has_a_golden_test(source: Source | CertificateSource) -> None:
    assert (FIXTURES / source.name / "expected.json").is_file()
    assert (TESTS / f"test_{source.name}.py").is_file()


def test_a_statement_with_nothing_to_reconcile_against_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def extract(_text: str, _source_file: str) -> Extraction:
        return Extraction(month=date(2026, 9, 1), transactions=())

    bare = Source(
        name="bare",
        label="Bare export",
        reads="text",
        recognises=starts_with("Bare,"),
        extract=extract,
    )
    monkeypatch.setattr(router, "SOURCES", (bare,))
    statement = tmp_path / "bare.csv"
    statement.write_text("Bare,Header\n", encoding="utf-8")
    with pytest.raises(ExtractionError, match="Bare export: the extractor gives nothing"):
        router.load(statement)
