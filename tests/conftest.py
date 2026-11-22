import shutil
from datetime import date
from pathlib import Path

import pytest

from scrooge.classification import Instrument
from scrooge.extractors import ocr, swisscard

FIXTURES = Path(__file__).parent / "fixtures"
FILES = {
    "IBKR.csv": "ibkr/activity.csv",
    "Migros.csv": "migros/migros.csv",
    "Revolut CHF.tsv": "revolut/chf.tsv",
    "Revolut Savings.tsv": "revolut_savings/savings.tsv",
    "tastytrade.csv": "tastytrade/transactions.csv",
    "swisscard.pdf": "swisscard/statement.txt",
}
RULES = """rules:
  - {match: 'Hans Muster', category: Affitto}
  - {match: 'ACME SWITZERLAND', category: Stipendio}
  - {match: 'KONSUM|FRISCHMARKT', category: Spesa}
  - {match: 'Metal plan fee', category: Revolut Metal}
  - {match: '.', category: Regali}
"""


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name, source in FILES.items():
        shutil.copy(FIXTURES / source, tmp_path / name)
    (tmp_path / "ibkr.pdf").write_bytes(b"%PDF")
    (tmp_path / "notes.txt").write_text("not a statement", encoding="utf-8")
    (tmp_path / ".DS_Store").write_bytes(b"\0")
    (tmp_path / "old").mkdir()
    monkeypatch.setattr(swisscard, "read_text", lambda path: path.read_text(encoding="utf-8"))
    return tmp_path


def write_rules(tmp_path: Path) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(RULES, encoding="utf-8")
    return path


# Tastytrade's export says neither what WIDG is nor when a bill matures.
INSTRUMENTS = {
    "WIDG": Instrument(tab="ETFs"),
    "912797ZZ1": Instrument(maturity=date(2026, 12, 24)),
}
INSTRUMENTS_YAML = "instruments:\n  WIDG: {tab: ETFs}\n  912797ZZ1: {maturity: 2026-12-24}\n"


# The scanned certificates under names that say nothing, as they arrive, and their stored text.
SCANS = {"Scan 0001.pdf": "axa", "Dokument.pdf": "servisa"}


@pytest.fixture
def pillar2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A folder with the two certificate scans; the stored text stands in for Tesseract."""
    scans = tmp_path / "Pillar 2"
    scans.mkdir()
    for name, source in SCANS.items():
        shutil.copy(FIXTURES / source / "certificate.pdf", scans / name)

    def stored_text(path: Path) -> str:
        return (FIXTURES / SCANS[path.name] / "certificate.txt").read_text(encoding="utf-8")

    monkeypatch.setattr(ocr, "read_text", stored_text)
    return scans
