import shutil
from pathlib import Path

import pytest

from finanze.extractors import swisscard

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
    (tmp_path / "other.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / ".DS_Store").write_bytes(b"\0")
    (tmp_path / "old").mkdir()
    monkeypatch.setattr(swisscard, "read_text", lambda path: path.read_text(encoding="utf-8"))
    return tmp_path


def write_rules(tmp_path: Path) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(RULES, encoding="utf-8")
    return path
