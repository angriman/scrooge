"""The rules.yaml shipped with the project must load and sort well-known merchants."""

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
        ("ALPENBAHN EASYGO, BERN (05.08.2026)", "Trasporti locali"),
        ("ALPENBAHN AG, BERN (04.08.2026)", "Treni"),
        ("Trenovia", "Treni"),
        ("VELOSHARE AG, BERN", "Trasporti locali"),
        ("FRISCHMARKT ZENTRUM, ZÜRICH", "Spesa"),
        ("Acquisto KONSUM-1234 ZUERICH 30.08.2026 12:01 Carta: 0000", "Spesa"),
        ("FRISCHMARKT RESTAURANT, ZÜRICH", "Ristoranti"),
        ("FOOD RUN, HTTPS://HELP.", "Ristoranti"),
        ("HOSHI-RAMEN GMBH, ZÜRICH", "Ristoranti"),
        ("MARIAS CAFÉ BISTRO, ZÜRICH", "Ristoranti"),
        ("BROTKORB, ZÜRICH", "Spesa"),
        ("Listenly", "Abbonamenti digitali"),
        ("RIDEPASS ONE, AMSTERDAM", "Abbonamenti digitali"),
        ("Metal plan fee", "Revolut Metal"),
        ("Telco Mobile SA, Rue de l'Exemple 4, 1000 Lausanne", "SIM"),
        ("MUSTERKASSE Kranken-Versicherung AG, Beispielstrasse 21", "Assicurazione sanitaria"),
        ("Elektrizitätswerk der Stadt Bern", "Utenze"),
        ("Tutorly", "Lezioni di lingua"),
        ("BUCHWELT LESELAND AG 10, ZÜRICH", "Libri"),
        ("Rossi Carburanti LNG Metano e GPL", "Carburante"),
        ("Spese per traffico pagamenti", "Commissioni"),
        ("TECHNIKUM - WARENWELT ONLINE, ZUERICH", "Elettronica"),
        ("MOBILHAUS, ECUBLENS VD", "Arredamento e casa"),
        ("Warenpost", "Acquisti vari"),
        ("FlyBlue", "Voli"),
        ("AEROSUR, MADRID", "Voli"),
        ("Parking Esempio Blava", "Parcheggi e pedaggi"),
        ("Sportivo Sport", "Sport"),
        ("SCHWEIZ. MUSTER-STI, ZÜRICH", "Donazioni"),
        ("CONSOLATO ONORARIO, ZURIGO", "Documenti e burocrazia"),
        ("Stadt Bern, Stadtpolizei, 3001 Bern", "Documenti e burocrazia"),
        ("ESEMPIO CHOCOLATIER, BERN", ""),  # no obvious category: the user decides
        ("Accredito TWINT Rossi, Maria, +41790000000", ""),
    ],
)
def test_shipped_rules(description: str, category: str) -> None:
    assert _category(description) == category


def test_shipped_rules_name_no_people() -> None:
    # Personal rules belong in the git-ignored rules.local.yaml.
    text = (ROOT / "rules.yaml").read_text(encoding="utf-8")
    assert "TWINT" not in text
    assert (ROOT / ".gitignore").read_text(encoding="utf-8").count("rules.local.yaml") == 1


def test_shipped_instruments_file_loads() -> None:
    instruments = load_instruments(ROOT / "instruments.yaml")
    assert instruments["SGOV"].tab == "ETFs"
