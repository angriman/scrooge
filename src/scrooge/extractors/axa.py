"""AXA pension fund certificate (second pillar, English) -> the retirement assets it states.

The certificate is a scan: `ocr.read_text` gives its text and `extract` parses that text and
is pure. The one row taken is "Your retirement assets as at <date>", with the mandatory
portion, the extra-mandatory portion and their total; the rows under it are projections. The
two portions must add up to the total, which is what catches a misread digit.

The certificate of 1 January prints the year behind it instead: "Retirement assets as at
<date>" once for the day the year began (not on the first one) and once for the day it ended,
with interest and credits in between. The later row is the certificate's; the earlier one is
what the certificate of the year before stated, and is taken as the balance the year opened
with (decided 2026-10-10): it must read as cleanly.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from scrooge.errors import ExtractionError
from scrooge.extractors.common import only_match, parse_swiss_amount, parse_swiss_date
from scrooge.extractors.source import CertificateSource
from scrooge.models import PensionCertificate

_NAME = "axa"
ACCOUNT = "Pillar 2 AXA"  # one account per pension fund (and person, D14)
CURRENCY = "CHF"
ADDS_UP = True  # the two portions must make the total: the figure is checked

_TITLE = "Pension fund certificate"
_ISSUER = re.compile(r"\bAXA\b")
_ASSETS = re.compile(r"^(?:Your r|R)etirement assets as at[ \t]+(\S+)(.*)", re.MULTILINE)
_START = re.compile(r"Beginning of insurance[ \t]+(\S+)")


def recognises(content: str) -> bool:
    """Whether the text of a scan is an AXA pension fund certificate."""
    return _TITLE in content and _ISSUER.search(content) is not None


def extract(content: str) -> PensionCertificate:
    """Parse the text of one certificate. Anything that does not read cleanly raises."""
    if not recognises(content):
        msg = f'AXA: no "{_TITLE}" heading'
        raise ExtractionError(msg)
    rows = sorted(_assets(found) for found in _ASSETS.finditer(content))
    days = {day for day, _, _ in rows}
    if len(rows) not in (1, 2) or len(days) != len(rows):
        msg = (
            f'AXA: the row "retirement assets as at": found {len(rows)} times on {len(days)} '
            "days, expected once, or once each for the two days a year runs between"
        )
        raise ExtractionError(msg)
    as_of, mandatory, total = rows[-1]
    opened = None if len(rows) == 1 else (rows[0][0], rows[0][2])
    start = only_match(_START, content, 'AXA: "Beginning of insurance"')
    since = parse_swiss_date(start[1], "AXA beginning of insurance")
    if since > as_of:
        msg = f"AXA: the insurance begins on {since}, after the assets' date {as_of}"
        raise ExtractionError(msg)
    return PensionCertificate(_NAME, as_of, CURRENCY, total, mandatory, since, opened)


def _assets(row: re.Match[str]) -> tuple[date, Decimal, Decimal]:
    """The day, the mandatory portion and the total of one row of retirement assets."""
    day = parse_swiss_date(row[1], "AXA retirement assets date")
    figures = row[2].split()
    if len(figures) != 3:
        msg = f"AXA: the retirement assets row has {len(figures)} figures, expected 3: {row[0]!r}"
        raise ExtractionError(msg)
    mandatory, extra, total = (parse_swiss_amount(f, "AXA retirement assets") for f in figures)
    if mandatory + extra != total:
        msg = f"AXA: the portions {mandatory} and {extra} do not add up to the total {total}"
        raise ExtractionError(msg)
    return day, mandatory, total


SOURCE = CertificateSource(
    name=_NAME,
    label="AXA pension certificate",
    account=ACCOUNT,
    adds_up=ADDS_UP,
    recognises=recognises,
    extract=extract,
)
