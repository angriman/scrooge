"""Migros Bank account statement (CSV, `;`, Italian headers) -> normalized transactions.

A block of metadata lines precedes the table. The `Saldo:` metadata line is the balance on the
day of the export, not at the end of the period, so the opening and closing balances come from
the running `Saldo` column, which is checked row by row.

Older exports have the same table without quotes and without the
`Comunicazione` column, and list some rows of one day out of balance order: the rows are then
put back in the one order in which every balance follows from the row before.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from finanze.errors import ExtractionError
from finanze.extractors.common import (
    month_start,
    parse_decimal,
    parse_iso_date,
    parse_swiss_date,
    read_table,
)
from finanze.models import CashBalance, Extraction, Kind, Transaction

_COLUMNS = ("Data", "Testo di registrazione", "Importo", "Saldo", "Valuta")
_HEADER = re.compile(r'^"?Data"?;', re.MULTILINE)
_Row = tuple[int, dict[str, str]]  # line number in the table, cells
# "Addebito TWINT Surname, First, +41790000000 <reference>", or a shop's name and the reference
_TWINT = re.compile(r"((?:Addebito|Accredito) TWINT) (.*?)(?: \d{16})?$")
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
    """Parse the text of a Migros Bank statement for the period it prints."""
    text = content.removeprefix("\ufeff")
    header = _HEADER.search(text)
    if header is None:
        msg = 'Migros: no transaction table (expected a header row starting with "Data")'
        raise ExtractionError(msg)
    start = header.start()
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
    opening, ordered = _in_balance_order(rows)
    balance = opening
    for n, row in ordered:
        amount = parse_decimal(row["Importo"], "Migros Importo")
        balance = parse_decimal(row["Saldo"], "Migros Saldo")
        text_ = row["Testo di registrazione"]
        kind, category = _classify(text_, amount)
        note = row.get("Comunicazione", "")
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
        month=month_start(first),
        account_id=_iban(metadata),
        start=first,
        end=last,
        transactions=tuple(transactions),
        balances=(CashBalance(account, currency, opening, balance),),
    )


def _in_balance_order(rows: list[dict[str, str]]) -> tuple[Decimal, list[_Row]]:
    """The opening balance, and the rows (with their line numbers) so that each balance follows.

    The statement has no opening balance: it is what the first row started from. Rows are
    normally already in order. Where they are not, each row's balance before it (`Saldo` -
    `Importo`) must be the balance after exactly one other row, which gives the order back.
    """
    numbered = list(enumerate(rows, start=1))
    spans: list[tuple[Decimal, Decimal]] = []  # balance before and after each row
    for row in rows:
        after = parse_decimal(row["Saldo"], "Migros Saldo")
        spans.append((after - parse_decimal(row["Importo"], "Migros Importo"), after))
    broken = next((n for n in range(1, len(rows)) if spans[n][0] != spans[n - 1][1]), None)
    if broken is None:
        return spans[0][0], numbered
    before, after = spans[broken]
    error = ExtractionError(
        f"Migros row {broken + 1}: balance {after} does not follow "
        f"{spans[broken - 1][1]} {after - before:+}"
    )
    reached = {after for _, after in spans}
    starts = {before for before, _ in spans if before not in reached}
    if len(starts) != 1:
        raise error
    waiting: dict[Decimal, list[int]] = defaultdict(list)
    for index, (before, _) in enumerate(spans):
        waiting[before].append(index)
    opening = balance = starts.pop()
    ordered: list[_Row] = []
    while waiting[balance]:
        index = waiting[balance].pop(0)
        ordered.append(numbered[index])
        balance = spans[index][1]
    if len(ordered) != len(rows):
        raise error
    return opening, ordered


def _metadata(block: str) -> dict[str, str]:
    pairs = (row for row in csv.reader(io.StringIO(block), delimiter=";") if len(row) >= 2)
    return {row[0]: row[1] for row in pairs}


def _period(metadata: dict[str, str]) -> tuple[date, date]:
    first = parse_iso_date(metadata.get("Estratto conto da:", ""), "Migros period start")
    last = parse_iso_date(metadata.get("Estratto conto fino:", ""), "Migros period end")
    if last < first:
        msg = f"Migros statement period ends before it starts: {first} to {last}"
        raise ExtractionError(msg)
    return first, last


def _iban(metadata: dict[str, str]) -> str:
    """The IBAN of "Numero di conto / IBAN:" (account number, a slash, IBAN), no spaces."""
    printed = metadata.get("Numero di conto / IBAN:", "")
    return printed.rpartition("/")[2].replace(" ", "")


def _booking_date(row: dict[str, str], first: date, last: date, n: int) -> date:
    # The day the running balance books the row (`Data`), whatever period the file covers, so
    # two overlapping exports give a row the same booking date. `Valuta` stays its `date`.
    booked = parse_swiss_date(row["Data"], "Migros Data")
    if not first <= booked <= last:
        msg = (
            f"Migros row {n}: {booked} is outside the period the statement covers "
            f"({first} to {last})"
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
    twint = _TWINT.match(text)
    if twint and twint[2].startswith(","):  # a person without a surname: ", First, +41..."
        return f"{twint[1]} {twint[2].lstrip(', ')}"
    if twint and "," not in twint[2]:  # a shop
        return f"{twint[1]} {twint[2]}"
    return text.partition(",")[0]
