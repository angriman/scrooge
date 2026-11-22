"""The starter files `scrooge init` copies must load, and the rules sort by generic words only.

The real rules.yaml, instruments.yaml and categories.yaml are personal and git-ignored.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from scrooge import categories
from scrooge.classification import classify, load_instruments, load_rules
from scrooge.models import Kind, Transaction

ROOT = Path(__file__).parents[1]
STARTER = ROOT / "src" / "scrooge" / "starter"
RULES = load_rules([STARTER / "rules.yaml"])


def _category(description: str) -> str:
    txn = Transaction(
        date=date(2026, 9, 1),
        account="Migros",
        amount=Decimal(-1),
        currency="CHF",
        description=description,
        kind=Kind.EXPENSE,
        source_file="f",
        source_ref="1",
    )
    return classify([txn], RULES)[0].category


@pytest.mark.parametrize(
    ("description", "category"),
    [
        ("Rossi Carburanti LNG Metano e GPL", "Carburante"),
        ("Parking Esempio Blava", "Parcheggi e pedaggi"),
        ("RISTORANTE DA MARIO, LUGANO", "Ristoranti e bar"),
        ("ESEMPIO-RAMEN GMBH, BERN", "Ristoranti e bar"),
        ("MARIAS CAFÉ BISTRO, BERN", "Ristoranti e bar"),
        ("Supermarket Esempio", "Spesa"),
        ("Elektrizitätswerk der Stadt Bern", "Utenze"),
        ("Muster Kranken-Versicherung AG, Beispielstrasse 21", "Assicurazione sanitaria"),
        ("BAHNHOF APOTHEKE, BERN", "Medico/Farmacia"),
        ("Il Barbiere Di Esempio", "Barbiere"),
        ("LIBRERIA ESEMPIO, MILANO", "Libri"),
        ("Esempio Hotels", "Alloggi"),
        ("MUSEO CIVICO, MILANO", "Attività/Svago"),
        ("Metal plan fee", "Revolut Metal"),
        ("Spese per traffico pagamenti", "Commissioni"),
        ("MUSTER STIFTUNG, BERN", "Donazioni"),
        ("CONSOLATO ONORARIO, BERNA", "Documenti e burocrazia"),
        ("ESEMPIO CHOCOLATIER, BERN", ""),  # a name alone says nothing: the user decides
        ("Accredito TWINT Rossi, Maria, +41790000000", ""),
    ],
)
def test_shipped_rules(description: str, category: str) -> None:
    assert _category(description) == category


def test_shipped_rules_name_no_people() -> None:
    # Rules for people and named businesses belong in the user's own, git-ignored rules.yaml.
    text = (STARTER / "rules.yaml").read_text(encoding="utf-8")
    assert "TWINT" not in text


def test_the_personal_files_are_not_committed() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for name in ("/rules.yaml", "rules.*.yaml", "/instruments.yaml", "/categories.yaml"):
        assert name in ignored


def test_shipped_instruments_file_loads_empty_and_its_example_loads_too(tmp_path: Path) -> None:
    assert load_instruments(STARTER / "instruments.yaml") == {}
    text = (STARTER / "instruments.yaml").read_text(encoding="utf-8")
    example = text.partition("# For example:\n#\n")[2].partition("\ninstruments: {}")[0]
    uncommented = tmp_path / "instruments.yaml"
    uncommented.write_text("".join(f"{line[4:]}\n" for line in example.splitlines()))
    instruments = load_instruments(uncommented)
    assert instruments["WIDG"].tab == "ETFs"
    assert instruments["WIDG"].price_symbol == "WIDG.DE"
    assert instruments["912797ZZ1"].maturity == date(2026, 12, 24)


def test_shipped_categories_file_adds_nothing() -> None:
    assert categories.load(STARTER / "categories.yaml") == ()
