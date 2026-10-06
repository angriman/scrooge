"""Migros Bank account statement (CSV, `;`, Italian headers) -> normalized transactions.

A block of metadata lines precedes the table. The `Saldo:` metadata line is the balance on the
day of the export, not at the end of the period, so the opening and closing balances come from
the running `Saldo` column, which is checked row by row.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta
from decimal import Decimal

from finanze.errors import ExtractionError
from finanze.extractors.common import (
    month_end,
    parse_decimal,
    parse_iso_date,
    parse_swiss_date,
    read_table,
)
from finanze.models import CashBalance, Extraction, Kind, Transaction

_COLUMNS = ("Data", "Testo di registrazione", "Comunicazione", "Importo", "Saldo", "Valuta")
_HEADER = '"Data";'
_CARD_PURCHASE = re.compile(r"Acquisto (.+?) \d{2}\.\d{2}\.\d{4} \d{2}:\d{2} Carta:")
# Transfers the sheet tracks as their own categories (D2), by what the booking text starts with.
_TRANSFERS = (
    ("Acquisto Revolut", Kind.TRANSFER, "Topup Revolut"),
    ("REVOLUT LTD", Kind.TRANSFER, "Topup Revolut"),
    ("Interactive Brokers", Kind.TRANSFER, "Topup IBKR"),
    ("Prelevamento al Bancomat", Kind.TRANSFER, "Prelievi"),
    ("Storno, Prelevamento al Bancomat", Kind.TRANSFER, "Prelievi"),
    ("Storno: Prelevamento al Bancomat", Kind.TRANSFER, "Prelievi"),
    # D3: the card bill is replaced by the individual Swisscard purchases.
    ("Swisscard AECS", Kind.INTERNAL, ""),
)


def extract(content: str, source_file: str) -> Extraction:
    """Parse the text of a Migros Bank statement covering whole calendar months."""
    text = content.removeprefix("\ufeff")
    start = text.find(_HEADER)
    if start < 0:
        msg = 'Migros: no transaction table (expected a header row starting with "Data")'
        raise ExtractionError(msg)
    metadata = _metadata(text[:start])
    first, last = _period(metadata)
    currency = metadata.get("Saldo:", "").partition(" ")[0]
    if not currency.isalpha():
        msg = f"Migros: cannot read the currency from Saldo {metadata.get('Saldo:')!r}"
        raise ExtractionError(msg)
    account = "Migros" if currency == "CHF" else f"Migros {currency}"

    rows = read_table(text[start:], ";", _COLUMNS, "Migros")
    if not rows:
        msg = "Migros statement has no rows, so no balance to reconcile"
        raise ExtractionError(msg)
    transactions: list[Transaction] = []
    # The statement has no opening balance: it is what the first row started from.
    first_row = rows[0]
    opening = parse_decimal(first_row["Saldo"], "Migros Saldo") - parse_decimal(
        first_row["Importo"], "Migros Importo"
    )
    balance = opening
    for n, row in enumerate(rows, start=1):
        amount = parse_decimal(row["Importo"], "Migros Importo")
        after = parse_decimal(row["Saldo"], "Migros Saldo")
        if balance + amount != after:
            msg = f"Migros row {n}: balance {after} does not follow {balance} {amount:+}"
            raise ExtractionError(msg)
        balance = after
        text_ = row["Testo di registrazione"]
        kind, category = _classify(text_, amount)
        note = row["Comunicazione"]
        transactions.append(
            Transaction(
                date=parse_swiss_date(row["Valuta"], "Migros Valuta"),
                account=account,
                amount=amount,
                currency=currency,
                description=f"{text_} | {note}" if note else text_,
                kind=kind,
                source_file=source_file,
                source_ref=str(n),
                counterparty=_counterparty(text_),
                category=category,
                booking_date=_booking_date(row, first, last, n),
            )
        )
    return Extraction(
        month=first,
        end=last,
        transactions=tuple(transactions),
        balances=(CashBalance(account, currency, opening, balance),),
    )


def _metadata(block: str) -> dict[str, str]:
    pairs = (row for row in csv.reader(io.StringIO(block), delimiter=";") if len(row) >= 2)
    return {row[0]: row[1] for row in pairs}


def _period(metadata: dict[str, str]) -> tuple[date, date]:
    first = parse_iso_date(metadata.get("Estratto conto da:", ""), "Migros period start")
    last = parse_iso_date(metadata.get("Estratto conto fino:", ""), "Migros period end")
    if first.day != 1 or last < first:
        msg = f"Migros statement must start on the first day of a month, got {first} to {last}"
        raise ExtractionError(msg)
    if last != month_end(last):
        # Exported up to a day inside a month (often the day of the export): the statement
        # counts up to the month end before it, and a row after that is refused below.
        last = last.replace(day=1) - timedelta(days=1)
    if last < first:
        msg = f"Migros statement covers no whole month ({first} onwards): export a full month"
        raise ExtractionError(msg)
    return first, last


def _booking_date(row: dict[str, str], first: date, last: date, n: int) -> date:
    # D4: the value date. A card purchase made late last month is booked in this one, so when
    # the value date falls outside the period the booking date is used.
    value = parse_swiss_date(row["Valuta"], "Migros Valuta")
    if first <= value <= last:
        return value
    booked = parse_swiss_date(row["Data"], "Migros Data")
    if not first <= booked <= last:
        msg = (
            f"Migros row {n}: {booked} is outside the whole months the statement covers "
            f"({first} to {last}): export it up to a month end"
        )
        raise ExtractionError(msg)
    return booked


def _classify(text: str, amount: Decimal) -> tuple[Kind, str]:
    for prefix, kind, category in _TRANSFERS:
        if text.startswith(prefix):
            # Money coming back (e.g. an IBKR withdrawal) has no fixed category: review.
            outgoing = amount < 0 or prefix.startswith("Storno") or kind is Kind.INTERNAL
            return kind, category if outgoing else ""
    return (Kind.EXPENSE if amount < 0 else Kind.INCOME), ""  # category from rules.yaml


def _counterparty(text: str) -> str:
    purchase = _CARD_PURCHASE.match(text)
    if purchase:
        return purchase[1]
    return text.partition(",")[0]
