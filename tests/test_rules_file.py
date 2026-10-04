"""The rules.yaml shipped with the project must load and sort by generic words only.

Rules naming a business or a person stay in the git-ignored rules.local.yaml.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finanze.classification import classify, load_instruments, load_rules
from finanze.models import Kind, Transaction

ROOT = Path(__file__).parents[1]
RULES = load_rules([ROOT / "rules.yaml"])


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
    # Rules for people and named businesses belong in the git-ignored rules.local.yaml.
    text = (ROOT / "rules.yaml").read_text(encoding="utf-8")
    assert "TWINT" not in text
    assert (ROOT / ".gitignore").read_text(encoding="utf-8").count("rules.local.yaml") == 1


def test_shipped_instruments_file_loads() -> None:
    instruments = load_instruments(ROOT / "instruments.yaml")
    assert instruments["SGOV"].tab == "ETFs"
