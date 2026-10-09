from datetime import date
from decimal import Decimal

import pytest

from finanze.errors import ExtractionError
from finanze.extractors import axa
from finanze.models import PensionCertificate
from tests.extractors.golden import FIXTURES, assert_certificate_matches_golden

# What Tesseract reads off `certificate.pdf`: see tests/make_certificates.py.
TEXT = (FIXTURES / "axa" / "certificate.txt").read_text(encoding="utf-8")
ROW = "Your retirement assets as at 01.03.2026 12'345.65 23'456.35 35'802.00"
START = "Beginning of insurance 01.09.2021"


def test_matches_golden_file() -> None:
    assert_certificate_matches_golden(axa.extract(TEXT), "axa")


def test_the_balance_is_the_total_on_the_day_the_row_names() -> None:
    assert axa.extract(TEXT) == PensionCertificate(
        source="axa",
        as_of=date(2026, 3, 1),
        currency="CHF",
        balance=Decimal("35802.00"),
        mandatory=Decimal("12345.65"),
        insured_since=date(2021, 9, 1),
    )


def test_projections_are_not_the_balance() -> None:
    assert "450'000.00" in TEXT  # the projected retirement capital
    assert axa.extract(TEXT).balance == Decimal("35802.00")


def test_a_second_page_changes_nothing() -> None:
    second = "Pension fund certificate\nValid with effect from 01.03.2026\nAXA Life Ltd\n"
    assert axa.extract(TEXT + "\n" + second) == axa.extract(TEXT)


def test_recognises_its_own_certificate_only() -> None:
    assert axa.recognises(TEXT)
    assert not axa.recognises(TEXT.replace("AXA", "AXB"))
    assert not axa.recognises("")


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("Pension fund certificate", "Pension fund", "no .Pension fund certificate. heading"),
        ("Your retirement assets as at", "Your retirement assets at", "found 0 times"),
        (ROW, f"{ROW}\n{ROW}", "found 2 times"),
        ("as at 01.03.2026", "as at O1.03.2026", "cannot read date 'O1.03.2026'"),
        ("as at 01.03.2026", "as at 31.02.2026", "cannot read date '31.02.2026'"),
        (" 23'456.35", "", "has 2 figures, expected 3"),
        ("35'802.00", "35'802.00 *", "has 4 figures, expected 3"),
        ("23'456.35", "23'456.3S", "cannot read amount"),
        ("23'456.35", "23456.35", "cannot read amount '23456.35'"),
        ("23'456.35", "23,456.35", "cannot read amount"),
        ("35'802.00", "35'802", "cannot read amount .35'802. "),
        # One digit misread: the portions no longer add up.
        ("12'345.65", "12'845.65", "12845.65 and 23456.35 do not add up to the total 35802.00"),
        ("35'802.00", "36'802.00", "do not add up"),
        ("Beginning of insurance", "Beginning insurance", "Beginning of insurance.: found 0 times"),
        (START, f"{START}\n{START}", "Beginning of insurance.: found 2 times"),
        ("insurance 01.09.2021", "insurance 01.09.202", "cannot read date '01.09.202'"),
        ("insurance 01.09.2021", "insurance 01.09.2027", "begins on 2027-09-01, after"),
    ],
)
def test_what_does_not_read_cleanly_is_refused(old: str, new: str, message: str) -> None:
    assert old in TEXT
    with pytest.raises(ExtractionError, match=message):
        axa.extract(TEXT.replace(old, new))
