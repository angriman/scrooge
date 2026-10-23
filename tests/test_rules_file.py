"""The example rules shipped with the project must load and sort by generic words only.

The real rules.yaml, instruments.yaml and categories.yaml are personal and git-ignored.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finanze import categories
from finanze.classification import classify, load_instruments, load_rules
from finanze.models import Kind, Transaction

ROOT = Path(__file__).parents[1]
EXAMPLES = ROOT / "examples"
RULES = load_rules([EXAMPLES / "rules.yaml"])


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
        ("RISTORANTE DA MARIO, LUGANO", "Ristoranti"),
        ("ESEMPIO-RAMEN GMBH, BERN", "Ristoranti"),
        ("MARIAS CAFÉ BISTRO, BERN", "Ristoranti"),
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
    text = (EXAMPLES / "rules.yaml").read_text(encoding="utf-8")
    assert "TWINT" not in text


def test_the_personal_files_are_not_committed() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for name in ("/rules.yaml", "rules.*.yaml", "/instruments.yaml", "/categories.yaml"):
        assert name in ignored


def test_shipped_instruments_file_loads() -> None:
    instruments = load_instruments(EXAMPLES / "instruments.yaml")
    assert instruments["WIDG"].tab == "ETFs"
    assert instruments["WIDG"].price_symbol == "WIDG.DE"
    assert instruments["912797ZZ1"].maturity == date(2026, 12, 24)


def test_shipped_categories_file_loads() -> None:
    assert "Spedizioni" in categories.names(categories.load(EXAMPLES / "categories.yaml"))
