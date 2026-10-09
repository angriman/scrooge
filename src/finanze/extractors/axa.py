"""AXA pension fund certificate (second pillar, English) -> the retirement assets it states.

The certificate is a scan: `ocr.read_text` gives its text and `extract` parses that text and
is pure. The one row taken is "Your retirement assets as at <date>", with the mandatory
portion, the extra-mandatory portion and their total; the rows under it are projections. The
two portions must add up to the total, which is what catches a misread digit.
"""

from __future__ import annotations

import re

from finanze.errors import ExtractionError
from finanze.extractors.common import only_match, parse_swiss_amount, parse_swiss_date
from finanze.models import PensionCertificate

SOURCE = "axa"
ACCOUNT = "Pillar 2 AXA"  # one account per pension fund (and person, D14)
CURRENCY = "CHF"
ADDS_UP = True  # the two portions must make the total: the figure is checked

_TITLE = "Pension fund certificate"
_ISSUER = re.compile(r"\bAXA\b")
_ASSETS = re.compile(r"Your retirement assets as at[ \t]+(\S+)(.*)")
_START = re.compile(r"Beginning of insurance[ \t]+(\S+)")


def recognises(content: str) -> bool:
    """Whether the text of a scan is an AXA pension fund certificate."""
    return _TITLE in content and _ISSUER.search(content) is not None


def extract(content: str) -> PensionCertificate:
    """Parse the text of one certificate. Anything that does not read cleanly raises."""
    if not recognises(content):
        msg = f'AXA: no "{_TITLE}" heading'
        raise ExtractionError(msg)
    row = only_match(_ASSETS, content, 'AXA: the row "Your retirement assets as at"')
    as_of = parse_swiss_date(row[1], "AXA retirement assets date")
    figures = row[2].split()
    if len(figures) != 3:
        msg = f"AXA: the retirement assets row has {len(figures)} figures, expected 3: {row[0]!r}"
        raise ExtractionError(msg)
    mandatory, extra, total = (parse_swiss_amount(f, "AXA retirement assets") for f in figures)
    if mandatory + extra != total:
        msg = f"AXA: the portions {mandatory} and {extra} do not add up to the total {total}"
        raise ExtractionError(msg)
    start = only_match(_START, content, 'AXA: "Beginning of insurance"')
    since = parse_swiss_date(start[1], "AXA beginning of insurance")
    if since > as_of:
        msg = f"AXA: the insurance begins on {since}, after the assets' date {as_of}"
        raise ExtractionError(msg)
    return PensionCertificate(SOURCE, as_of, CURRENCY, total, mandatory, since)
