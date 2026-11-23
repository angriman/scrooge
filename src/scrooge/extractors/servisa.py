"""Servisa pension certificate (second pillar, German) -> the retirement assets it states.

The certificate is a scan: `ocr.read_text` gives its text and `extract` parses that text and
is pure. The one row taken is "Austrittsleistung per <date>" (what would be paid out on
leaving that day), with the BVG part and the total; the "Voraussichtliches" and "Projiziertes
Altersguthaben" rows above it are projections. Nothing on the page adds up to the total, so a
misread digit can only be caught where it makes the BVG part larger than the total.

The text is read without German language data: umlauts come out as other letters and the
scan's margin leaves stray characters before a line, so no pattern depends on either.
"""

from __future__ import annotations

import re

from scrooge.errors import ExtractionError
from scrooge.extractors.common import only_match, parse_swiss_amount, parse_swiss_date
from scrooge.extractors.source import CertificateSource
from scrooge.models import PensionCertificate

_NAME = "servisa"
ACCOUNT = "Pillar 2 Servisa"  # one account per pension fund (and person, D14)
CURRENCY = "CHF"
ADDS_UP = False  # nothing on the page adds up to the total: the reader must compare it

_TITLE = re.compile(r"Vorsorgeausweis[ \t]+g\S{1,3}ltig[ \t]+ab\b")  # "gültig", read as "giiltig"
_ISSUER = re.compile(r"\bServisa\b")
_LEAVING = re.compile(r"Austrittsleistung per[ \t]+(\S+)(.*)")
# The row gives the day and then, after a slash, the degree of employment.
_START = re.compile(r"Versicherungsbeginn[ \t]*/[ \t]*Besch\S+[ \t]+(\S+)")


def recognises(content: str) -> bool:
    """Whether the text of a scan is a Servisa pension certificate."""
    return _TITLE.search(content) is not None and _ISSUER.search(content) is not None


def extract(content: str) -> PensionCertificate:
    """Parse the text of one certificate. Anything that does not read cleanly raises."""
    if not recognises(content):
        msg = 'Servisa: no "Vorsorgeausweis gültig ab" heading'
        raise ExtractionError(msg)
    row = only_match(_LEAVING, content, 'Servisa: the row "Austrittsleistung per"')
    as_of = parse_swiss_date(row[1], "Servisa Austrittsleistung date")
    figures = row[2].split()
    if len(figures) != 2:
        msg = (
            f"Servisa: the Austrittsleistung row has {len(figures)} figures, expected 2: {row[0]!r}"
        )
        raise ExtractionError(msg)
    mandatory, total = (parse_swiss_amount(f, "Servisa Austrittsleistung") for f in figures)
    if mandatory > total:
        msg = f"Servisa: the BVG part {mandatory} is more than the total {total}"
        raise ExtractionError(msg)
    start = only_match(_START, content, 'Servisa: "Versicherungsbeginn"')
    since = parse_swiss_date(start[1], "Servisa Versicherungsbeginn")
    if since > as_of:
        msg = f"Servisa: the insurance begins on {since}, after the leaving benefit's date {as_of}"
        raise ExtractionError(msg)
    return PensionCertificate(_NAME, as_of, CURRENCY, total, mandatory, since)


SOURCE = CertificateSource(
    name=_NAME,
    label="Servisa pension certificate",
    account=ACCOUNT,
    adds_up=ADDS_UP,
    recognises=recognises,
    extract=extract,
)
