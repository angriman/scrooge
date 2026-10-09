from datetime import date
from decimal import Decimal

import pytest

from finanze.errors import ExtractionError
from finanze.extractors import axa, servisa
from finanze.models import PensionCertificate
from tests.extractors.golden import FIXTURES, assert_certificate_matches_golden

# What Tesseract reads off `certificate.pdf`: see tests/make_certificates.py.
TEXT = (FIXTURES / "servisa" / "certificate.txt").read_text(encoding="utf-8")
ROW = "Austrittsleistung per 01.01.2026 2'468.10 3'579.40"
START = "Versicherungsbeginn / Beschaftigungsgrad 01.04.2024 / 80.00%"


def test_matches_golden_file() -> None:
    assert_certificate_matches_golden(servisa.extract(TEXT), "servisa")


def test_the_balance_is_the_total_paid_on_leaving() -> None:
    assert servisa.extract(TEXT) == PensionCertificate(
        source="servisa",
        as_of=date(2026, 1, 1),
        currency="CHF",
        balance=Decimal("3579.40"),
        mandatory=Decimal("2468.10"),
        insured_since=date(2024, 4, 1),
    )


def test_projections_are_not_the_balance() -> None:
    assert "Voraussichtliches Altersguthaben per 31.12.2026 4'321.05 5'432.10" in TEXT
    assert "Projiziertes Altersguthaben mit Zins 93'000.50 94'111.55" in TEXT
    assert servisa.extract(TEXT).balance == Decimal("3579.40")


def test_the_bvg_part_may_be_all_of_it() -> None:
    result = servisa.extract(TEXT.replace("2'468.10 3'579.40", "950.40 950.40"))
    assert (result.mandatory, result.balance) == (Decimal("950.40"), Decimal("950.40"))


@pytest.mark.parametrize("heading", ["g\u00fcltig", "giiltig", "gultig", "giltig"])
def test_however_the_umlauts_were_read(heading: str) -> None:
    text = TEXT.replace("gultig", heading).replace("Beschaftigungsgrad", "Besch\u00e4ftigungsgrad")
    assert servisa.extract(text) == servisa.extract(TEXT)


def test_stray_characters_from_the_margin_before_a_line() -> None:
    assert f"3 {START}" in TEXT  # the fixture has them too
    text = TEXT.replace(f"3 {START}", f"& 4 7 {START}").replace(ROW, f"z 2 {ROW}")
    assert servisa.extract(text) == servisa.extract(TEXT)


def test_recognises_its_own_certificate_only() -> None:
    assert servisa.recognises(TEXT)
    assert not servisa.recognises(TEXT.replace("Servisa", "Servus"))
    # The cover letter alone names the certificate, but is not one.
    assert not servisa.recognises(TEXT.partition("Seite 1/2")[0])
    assert not servisa.recognises("")


def test_neither_issuer_takes_the_other_one_for_its_own() -> None:
    assert not axa.recognises(TEXT)
    assert not servisa.recognises((FIXTURES / "axa" / "certificate.txt").read_text("utf-8"))


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("Vorsorgeausweis gultig ab", "Vorsorgeausweis", "no .Vorsorgeausweis g.ltig ab. heading"),
        ("Austrittsleistung per", "Austrittsleistung", "Austrittsleistung per.: found 0 times"),
        (ROW, f"{ROW}\n{ROW}", "found 2 times"),
        ("per 01.01.2026", "per 01.01.2O26", "cannot read date '01.01.2O26'"),
        (" 3'579.40", "", "has 1 figures, expected 2"),
        ("3'579.40", "3'579.40 :", "has 3 figures, expected 2"),
        ("3'579.40", "3'579.4O", "cannot read amount"),
        ("3'579.40", "3579.40", "cannot read amount '3579.40'"),
        ("2'468.10", "2'468", "cannot read amount"),
        ("2'468.10", "8'468.10", "the BVG part 8468.10 is more than the total 3579.40"),
        ("Versicherungsbeginn /", "Versicherungs beginn /", "Versicherungsbeginn.: found 0 times"),
        (START, f"{START}\n{START}", "Versicherungsbeginn.: found 2 times"),
        ("01.04.2024 /", "01.04.2024/", "cannot read date '01.04.2024/'"),
        ("01.04.2024 /", "01.04.2027 /", "begins on 2027-04-01, after"),
    ],
)
def test_what_does_not_read_cleanly_is_refused(old: str, new: str, message: str) -> None:
    assert old in TEXT
    with pytest.raises(ExtractionError, match=message):
        servisa.extract(TEXT.replace(old, new))
