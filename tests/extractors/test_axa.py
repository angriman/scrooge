from dataclasses import replace
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
# The certificate of 1 January: the year behind it, from one balance to the next.
YEARLY = (FIXTURES / "axa_yearly" / "certificate.txt").read_text(encoding="utf-8")
OPENING = "Retirement assets as at 01.01.2025 10'000.50 20'000.25 30'000.75"
CLOSING = "Retirement assets as at 01.01.2026 12'345.65 24'680.35 37'026.00"


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


def test_the_yearly_certificate_matches_its_golden_file() -> None:
    assert_certificate_matches_golden(axa.extract(YEARLY), "axa_yearly")


def test_of_the_two_balances_of_a_year_the_later_one_is_the_certificate_s() -> None:
    assert OPENING in YEARLY
    assert CLOSING in YEARLY
    certificate = axa.extract(YEARLY)
    assert (certificate.as_of, certificate.balance) == (date(2026, 1, 1), Decimal("37026.00"))
    assert certificate.opened == (date(2025, 1, 1), Decimal("30000.75"))
    # whichever is printed first
    swapped = YEARLY.replace(OPENING, "#").replace(CLOSING, OPENING).replace("#", CLOSING)
    assert axa.extract(swapped) == certificate


def test_the_first_yearly_certificate_has_no_balance_before_it() -> None:
    first = axa.extract(YEARLY.replace(OPENING + "\n", ""))
    assert first == replace(axa.extract(YEARLY), opened=None)


def test_a_certificate_of_another_day_opens_with_nothing() -> None:
    assert axa.extract(TEXT).opened is None


def test_only_the_row_itself_is_read() -> None:
    inside = "Death lump sum (incl. Retirement assets as at 01.01.2027 1.00 2.00 3.00)"
    assert axa.extract(f"{YEARLY}\n{inside}") == axa.extract(YEARLY)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (CLOSING, f"{CLOSING}\n{CLOSING}", "found 3 times on 2 days"),
        (OPENING, CLOSING, "found 2 times on 1 days"),
        (OPENING, f"{OPENING}\n{OPENING.replace('2025', '2024')}", "found 3 times on 3 days"),
        # The balance the year began with is taken too, and must read as cleanly.
        ("as at 01.01.2025", "as at 01.01.2O25", "cannot read date '01.01.2O25'"),
        ("20'000.25", "§0'000.25", "cannot read amount"),
        ("30'000.75", "30'000.15", "10000.50 and 20000.25 do not add up to the total 30000.15"),
        ("24'680.35", "§4'680.35", "cannot read amount"),
        ("24'680.35", "24'630.35", "12345.65 and 24630.35 do not add up to the total 37026.00"),
    ],
)
def test_a_yearly_certificate_that_does_not_read_cleanly_is_refused(
    old: str, new: str, message: str
) -> None:
    assert old in YEARLY
    with pytest.raises(ExtractionError, match=message):
        axa.extract(YEARLY.replace(old, new))


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
        (ROW, f"{ROW}\n{ROW}", "found 2 times on 1 days"),
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
