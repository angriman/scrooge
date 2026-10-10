"""Draws the scanned pension certificates under `tests/fixtures/` (axa, axa_yearly, servisa).

Run `uv run python -m tests.make_certificates` to make them again. Every name, number and
amount is invented; only the labels and the layout are those of the real certificates, at the
resolution of a scan (A4 at 300 dpi), so that the extractors read both alike. Each PDF holds
one image per page and no text layer. Next to it goes `certificate.txt`, what `ocr.read_text`
makes of it, for the tests that do not need Tesseract.

The typeface is the system's Arial (macOS), so the files are only reproduced on a Mac.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from finanze.extractors import ocr

FIXTURES = Path(__file__).parent / "fixtures"
_FONTS = Path("/System/Library/Fonts/Supplemental")
_PAGE = (2480, 3508)
# Fixed, so that a rerun writes the same bytes.
_MADE = datetime(2026, 10, 10, tzinfo=UTC).timetuple()
# (x, y, text, size, bold); a negative x is the right edge the text ends at.
_Text = tuple[int, int, str, int, bool]

_AXA: tuple[_Text, ...] = (
    (200, 530, "Scheme 1 cred.13%", 36, False),
    (200, 620, "Pension fund certificate", 76, True),
    (200, 715, "Valid with effect from 01.03.2026", 36, False),
    (1140, 715, "Contract no. 9/99999", 36, False),
    (-2260, 715, "XYZ", 36, False),
    (200, 805, "AXA Foundation", 36, False),
    (200, 849, "for Occupational", 36, False),
    (200, 893, "Benefits", 36, False),
    (200, 937, "Winterthur", 36, False),
    (1140, 805, "Acme Switzerland GmbH", 36, False),
    (1140, 849, "Musterplatz 1", 36, False),
    (1140, 893, "8000 Zürich", 36, False),
    (200, 1025, "Your personal details", 36, True),
    (-2260, 1025, "CHF", 36, False),
    (200, 1071, "Last name / first name", 36, False),
    (615, 1071, "Muster Hans", 36, True),
    (200, 1115, "Date of birth", 36, False),
    (615, 1115, "02.02.1990", 36, False),
    (200, 1159, "Gender", 36, False),
    (615, 1159, "Male", 36, False),
    (200, 1203, "Insurance number", 36, False),
    (615, 1203, "756.0000.0000.00", 36, False),
    (200, 1291, "Marital status", 36, False),
    (615, 1291, "single", 36, False),
    (1140, 1071, "Beginning of insurance", 36, False),
    (-2260, 1071, "01.09.2021", 36, False),
    (1140, 1115, "Retirement", 36, False),
    (-2260, 1115, "01.03.2055", 36, False),
    (1140, 1159, "Annual salary", 36, False),
    (-2260, 1159, "84'000.00", 36, False),
    (1140, 1203, "Pensionable salary", 36, False),
    (-2260, 1203, "84'000.00", 36, False),
    (-1540, 1380, "Mandatory", 36, True),
    (-1540, 1424, "portion", 36, True),
    (-2010, 1380, "Extra-mandatory", 36, True),
    (-2010, 1424, "portion", 36, True),
    (-2260, 1424, "Total", 36, True),
    (200, 1424, "Retirement assets", 36, True),
    (200, 1468, "Your retirement assets as at 01.03.2026", 36, False),
    (-1540, 1468, "12'345.65", 36, False),
    (-2010, 1468, "23'456.35", 36, False),
    (-2260, 1468, "35'802.00", 36, False),
    (200, 1540, "The current interest rate for retirement assets corresponds to 1.25%.", 36, False),
    (-1540, 1630, "Mandatory", 36, True),
    (-1540, 1674, "portion", 36, True),
    (-2010, 1630, "Extra-mandatory", 36, True),
    (-2010, 1674, "portion", 36, True),
    (-2260, 1674, "Total", 36, True),
    (200, 1674, "Projected benefits on retirement", 36, True),
    (200, 1718, "(provisional figures projected with 2% interest)", 36, False),
    (200, 1762, "Annual retirement pension at 01.03.2055", 36, False),
    (-1540, 1762, "9'100.00", 36, False),
    (-2010, 1762, "14'300.00", 36, False),
    (-2260, 1762, "23'400.00", 36, False),
    (200, 1806, "or retirement capital", 36, False),
    (-1540, 1806, "175'000.00", 36, False),
    (-2010, 1806, "275'000.00", 36, False),
    (-2260, 1806, "450'000.00", 36, False),
    (200, 1930, "Death benefits", 36, True),
    (
        200,
        1974,
        "Death lump sum (incl. retirement assets) ** if no partner's pension is due",
        36,
        False,
    ),
    (-2260, 1974, "40'000.00", 36, False),
    (200, 3250, "AXA Life Ltd", 30, False),
    (200, 3290, "page 1/1", 30, False),
)
_AXA_RULES = (700, 790, 1015, 1370)
_AXA_ASSETS = 1468  # the line of the one row of retirement assets
_LINE = 44
# The certificate of 1 January prints the year behind it instead: label and three figures.
_AXA_YEAR = (
    ("Retirement assets as at 01.01.2025", "10'000.50", "20'000.25", "30'000.75"),
    ("Interest (1.25%) for 2025", "125.00", "250.00", "375.00"),
    ("Retirement credit for 2025", "2'220.15", "4'430.10", "6'650.25"),
    ("Retirement assets as at 01.01.2026", "12'345.65", "24'680.35", "37'026.00"),
)


def _axa_yearly() -> tuple[_Text, ...]:
    """The page of `_AXA` as it is issued on 1 January, with the rows of `_AXA_YEAR`."""
    below = _LINE * (len(_AXA_YEAR) - 1)
    kept: list[_Text] = [
        (x, y + below if y > _AXA_ASSETS else y, text.replace("01.03.2026", "01.01.2026"), size, b)
        for x, y, text, size, b in _AXA
        if y != _AXA_ASSETS and text != "Retirement assets"
    ]
    year: list[_Text] = [
        (200, _AXA_ASSETS - _LINE, "Development of retirement assets in 2025", 36, True)
    ]
    for line, (label, *figures) in enumerate(_AXA_YEAR):
        y = _AXA_ASSETS + _LINE * line
        year.append((200, y, label, 36, False))
        year.extend(
            (edge, y, figure, 36, False)
            for edge, figure in zip((-1540, -2010, -2260), figures, strict=True)
        )
    return (*kept, *year)


_SERVISA_LETTER: tuple[_Text, ...] = (
    (340, 300, "Servisa", 70, True),
    (340, 760, "Persönlich", 40, False),
    (340, 810, "Anna Muster", 40, False),
    (340, 860, "Musterstrasse 1", 40, False),
    (340, 910, "8000 Zürich", 40, False),
    (340, 1300, "Ihr neuer Vorsorgeausweis der Pensionskasse", 44, True),
    (340, 1450, "Guten Tag Frau Muster", 40, False),
    (340, 1550, "Sie erhalten beiliegend Ihren neuen Vorsorgeausweis mit den aktuellen", 40, False),
    (340, 1600, "Informationen. Das Dokument ersetzt den bisherigen Vorsorgeausweis.", 40, False),
    (340, 1750, "Freundliche Grüsse", 40, False),
    (340, 1800, "Servisa Stiftungen", 40, False),
    (340, 3300, "Seite 1/2", 30, False),
)
_SERVISA: tuple[_Text, ...] = (
    (340, 300, "Servisa", 70, True),
    (340, 760, "Persönlich", 40, False),
    (340, 810, "Anna Muster", 40, False),
    (340, 860, "Musterstrasse 1", 40, False),
    (340, 910, "8000 Zürich", 40, False),
    (1100, 700, "Es betreut Sie", 40, False),
    (1100, 750, "Servisa Stiftungen", 40, False),
    (340, 1120, "Personalvorsorge-Vertrag Nr.", 40, False),
    (1100, 1120, "000000.00 MUSTER PARTNER AG", 40, False),
    (340, 1220, "Police Nr.", 40, False),
    (1100, 1220, "1", 40, False),
    (780, 1445, "Vorsorgeausweis gültig ab 01.01.2026", 46, True),
    (360, 1560, "1. Personaldaten", 40, True),
    (410, 1610, "Name und Vorname", 40, False),
    (1450, 1610, "Muster Anna", 40, False),
    (410, 1660, "Geburtsdatum / Geschlecht", 40, False),
    (1450, 1660, "03.03.1995 / weiblich", 40, False),
    (410, 1710, "Zivilstand", 40, False),
    (1450, 1710, "ledig", 40, False),
    (410, 1760, "Versicherungsbeginn / Beschäftigungsgrad", 40, False),
    (1450, 1760, "01.04.2024 / 80.00%", 40, False),
    (410, 1810, "Erreichen Terminalter am", 40, False),
    (1450, 1810, "01.04.2060", 40, False),
    (-1840, 1895, "BVG-Anteil", 40, True),
    (-1800, 1945, "in CHF", 40, True),
    (-2180, 1895, "Total in CHF", 40, True),
    (-2180, 1945, "(inkl. BVG-Anteil)", 40, True),
    (360, 2000, "2. Gehaltsdaten", 40, True),
    (410, 2050, "Gemeldetes Jahresgehalt", 40, False),
    (-2130, 2050, "52'000.00", 40, False),
    (410, 2100, "Versichertes Jahresgehalt", 40, False),
    (-1855, 2100, "25'540.00", 40, False),
    (-2130, 2100, "25'540.00", 40, False),
    (360, 2190, "3. Altersguthaben", 40, True),
    (410, 2240, "Voraussichtliches Altersguthaben per 31.12.2026", 40, False),
    (-1855, 2240, "4'321.05", 40, False),
    (-2130, 2240, "5'432.10", 40, False),
    (410, 2290, "Projiziertes Altersguthaben ohne Zins", 40, False),
    (-1855, 2290, "71'000.00", 40, False),
    (-2130, 2290, "72'111.05", 40, False),
    (410, 2340, "Projiziertes Altersguthaben mit Zins", 40, False),
    (-1855, 2340, "93'000.50", 40, False),
    (-2130, 2340, "94'111.55", 40, False),
    (410, 2420, "Austrittsleistung per 01.01.2026", 40, False),
    (-1855, 2420, "2'468.10", 40, False),
    (-2130, 2420, "3'579.40", 40, False),
    (410, 2470, "davon Guthaben aus Einkauf vorzeitige Pensionierung", 40, False),
    (-2130, 2470, "0.00", 40, False),
    (340, 3300, "Seite 2/2", 30, False),
)
_SERVISA_RULES = (1410, 1530, 1870, 2170)
_MARGIN = "ABCDE/FGHI/ 0000000/0000000/0000/0000"  # printed up the left edge of the page


def _font(size: int, bold: bool) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_FONTS / ("Arial Bold.ttf" if bold else "Arial.ttf"), size)


def _page(texts: tuple[_Text, ...], rules: tuple[int, ...], edges: tuple[int, int]) -> Image.Image:
    page = Image.new("1", _PAGE, 1)  # black and white keeps the files small
    draw = ImageDraw.Draw(page)
    for x, y, text, size, bold in texts:
        font = _font(size, bold)
        left = x if x >= 0 else -x - round(draw.textlength(text, font=font))
        draw.text((left, y), text, font=font, fill=0)
    for y in rules:
        draw.line((edges[0], y, edges[1], y), fill=0, width=2)
    return page


def _margin(page: Image.Image) -> Image.Image:
    font = _font(24, True)
    strip = Image.new("1", (round(font.getlength(_MARGIN)) + 4, 34), 1)
    ImageDraw.Draw(strip).text((2, 2), _MARGIN, font=font, fill=0)
    page.paste(strip.rotate(90, expand=True), (150, 1700))
    return page


def _save(pages: list[Image.Image], folder: str) -> None:
    path = FIXTURES / folder / "certificate.pdf"
    path.parent.mkdir(exist_ok=True)
    pages[0].save(
        path,
        "PDF",
        resolution=ocr.DPI,
        save_all=True,
        append_images=pages[1:],
        creationDate=_MADE,
        modDate=_MADE,
    )
    path.with_suffix(".txt").write_text(ocr.read_text(path), encoding="utf-8")


def main() -> None:
    _save([_page(_AXA, _AXA_RULES, (200, 2270))], "axa")
    _save([_page(_axa_yearly(), _AXA_RULES, (200, 2270))], "axa_yearly")
    letter = _page(_SERVISA_LETTER, (), (0, 0))
    _save([letter, _margin(_page(_SERVISA, _SERVISA_RULES, (340, 2190)))], "servisa")


if __name__ == "__main__":
    main()
