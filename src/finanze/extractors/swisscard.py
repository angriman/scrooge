"""Swisscard (Cashback Cards) invoice PDF -> normalized transactions booked on Migros (D3, D9).

`read_text` pulls the PDF's text layer with pdfplumber; `extract` parses that text and is pure.
Only lines shaped `dd.mm.yyyy <merchant> <amount>` between "Nuove transazioni" and "Totale nuove
transazioni" are transactions; detail lines under them (FX rates, flight legs) are skipped. A
misread is caught by the printed totals: each card's "Totale transazioni" and the invoice total.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pdfplumber

from finanze.errors import ExtractionError
from finanze.extractors.common import month_start, parse_decimal, parse_swiss_date
from finanze.models import ControlTotal, Extraction, Kind, Transaction

ACCOUNT = "Migros"
CURRENCY = "CHF"

_AMOUNT = r"(-?\d[\d']*\.\d{2})"
_TRANSACTION = re.compile(rf"(\d{{2}}\.\d{{2}}\.\d{{4}}) (.+?) {_AMOUNT}")
_INVOICE_DATE = re.compile(r"Data ?della ?fattura ?(\d{2}\.\d{2}\.\d{4})")
_CARD = re.compile(r"[\dX ]*X[\dX ]*?(\d{4}) [A-Z]")  # "0000 00XXXX X0001 NAME Cashback ..."
_CARD_TOTAL = re.compile(rf"Totale ?transazioni.* {_AMOUNT}")
_INVOICE_TOTAL = re.compile(rf"Totale ?nuove ?transazioni {_AMOUNT}")
_START = "Nuovetransazioni"
_MISCELLANEOUS = "Varie"


def read_text(path: Path) -> str:
    """Text layer of the PDF, with word spacing kept (the default tolerance glues words)."""
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text(x_tolerance=1.5) or "" for page in pdf.pages)


def extract(content: str, source_file: str) -> Extraction:
    """Parse the text of one Swisscard invoice."""
    invoice = _INVOICE_DATE.search(content)
    if invoice is None:
        msg = "Swisscard: no invoice date (is there a text layer?)"
        raise ExtractionError(msg)
    # D4: every purchase is dated to the invoice, i.e. the month the bill is paid from Migros.
    day = parse_swiss_date(invoice[1], "Swisscard invoice date")
    lines = [line.strip() for line in content.splitlines()]
    compact = [line.replace(" ", "") for line in lines]
    if _START not in compact:
        msg = 'Swisscard: no "Nuove transazioni" section'
        raise ExtractionError(msg)

    transactions: list[Transaction] = []
    section = ""  # last four digits of the current card, or "Varie"
    subtotal = total = Decimal(0)
    for n, line in enumerate(lines[compact.index(_START) + 1 :], start=1):
        if _INVOICE_TOTAL.fullmatch(line):
            _check(total, line, "the invoice total")
            return Extraction(
                month=month_start(day),
                transactions=tuple(transactions),
                controls=(ControlTotal(ACCOUNT, CURRENCY, -total),),
            )
        card = _CARD.match(line)
        if card or line == _MISCELLANEOUS:
            section, subtotal = (card[1] if card else _MISCELLANEOUS), Decimal(0)
        elif _CARD_TOTAL.fullmatch(line):
            _check(subtotal, line, f"the total of card {section}")
        elif match := _TRANSACTION.fullmatch(line):
            if not section:
                msg = f"Swisscard line {n}: transaction before any card heading: {line!r}"
                raise ExtractionError(msg)
            billed = parse_decimal(match[3], "Swisscard amount")
            subtotal += billed
            total += billed
            kind, category = _classify(match[2], billed, section)
            transactions.append(
                Transaction(
                    date=day,
                    account=ACCOUNT,
                    amount=-billed,
                    currency=CURRENCY,
                    description=f"{match[2]} ({match[1]})",
                    kind=kind,
                    source_file=source_file,
                    source_ref=f"{section}:{n}",
                    counterparty=match[2].rpartition(",")[0] or match[2],
                    category=category,
                )
            )
    msg = 'Swisscard: no "Totale nuove transazioni" line'
    raise ExtractionError(msg)


def _check(actual: Decimal, line: str, what: str) -> None:
    printed = parse_decimal(line.rpartition(" ")[2], "Swisscard total")
    if actual != printed:
        msg = f"Swisscard: transactions sum to {actual}, but {what} is {printed}"
        raise ExtractionError(msg)


def _classify(merchant: str, billed: Decimal, section: str) -> tuple[Kind, str]:
    if section == _MISCELLANEOUS and merchant == "CASHBACK":
        return Kind.INCOME, "Cashback"
    if merchant.startswith("REVOLUT**"):  # card top-up of the Revolut account
        return Kind.TRANSFER, "Topup Revolut"
    return (Kind.EXPENSE if billed > 0 else Kind.INCOME), ""  # category from rules.yaml
