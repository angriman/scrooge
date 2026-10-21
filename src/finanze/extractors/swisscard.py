"""Swisscard (Cashback Cards) invoice PDF -> normalized transactions booked on Migros (D3, D9).

`read_text` pulls the PDF's text layer with pdfplumber; `extract` parses that text and is pure.
Only lines shaped `dd.mm.yyyy <merchant> <amount>` between "Nuove transazioni" and "Totale nuove
transazioni" are transactions; detail lines under them (FX rates, flight legs) are skipped. A
misread is caught by the printed totals: each card's "Totale transazioni" and the invoice total.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from finanze.errors import ExtractionError
from finanze.extractors.common import month_start, parse_decimal, parse_swiss_date
from finanze.models import CashBalance, ControlTotal, Extraction, Kind, Transaction

ACCOUNT = "Migros"  # the bank account that pays the card, where D3 books the purchases
CARD_ACCOUNT = "Swisscard"
CURRENCY = "CHF"

_AMOUNT = r"(-?\d[\d']*\.\d{2})"
_TRANSACTION = re.compile(rf"(\d{{2}}\.\d{{2}}\.\d{{4}}) (.+?) {_AMOUNT}")
_INVOICE_DATE = re.compile(r"Data ?della ?fattura ?(\d{2}\.\d{2}\.\d{4})")
_ACCOUNT_NUMBER = re.compile(r"Numero ?di ?conto ?(\d+)")
_BILLING = re.compile(
    r"Periodo ?di ?fatturazione ?(\d{2}\.\d{2}\.)(\d{2}) ?[\u2013-] ?(\d{2}\.\d{2}\.)(\d{2})"
)
_CARD = re.compile(r"[\dX ]*X[\dX ]*?(\d{4}) [A-Z]")  # "0000 00XXXX X0001 NAME Cashback ..."
_CARD_TOTAL = re.compile(rf"Totale ?transazioni.* {_AMOUNT}")
_INVOICE_TOTAL = re.compile(rf"Totale ?nuove ?transazioni {_AMOUNT}")
_START = "Nuovetransazioni"
# Under "Saldo ultima fattura / I Suoi pagamenti / Totale nuove transazioni / Nuovo saldo".
_FIGURES = re.compile(" ".join([rf"CHF {_AMOUNT}"] * 4))
_MISCELLANEOUS = "Varie"


def read_text(path: Path) -> str:
    """Text layer of the PDF, with word spacing kept (the default tolerance glues words)."""
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text(x_tolerance=1.5) or "" for page in pdf.pages)


@dataclass(frozen=True, slots=True)
class _Line:
    """One dated line of the invoice: a purchase, a refund, the cashback or a payment."""

    ref: str
    day: date
    text: str
    amount: Decimal  # as printed: what is owed is positive


@dataclass(frozen=True, slots=True)
class _Invoice:
    day: date
    payments: tuple[_Line, ...]
    purchases: tuple[_Line, ...]
    total: Decimal  # "Totale nuove transazioni"
    figures: tuple[Decimal, ...]  # previous balance, payments, new transactions, new balance
    account_id: str  # the number of the card account, "" when not printed
    first: date  # first day the invoice covers
    last: date  # last day it covers: not before the invoice day, on which its rows are booked


def extract(content: str, source_file: str) -> Extraction:
    """Parse the text of one Swisscard invoice, booked on the bank account that pays it (D3)."""
    invoice = _read(content)
    # D4: every purchase is booked on the invoice, i.e. the month the bill is paid from Migros.
    transactions = tuple(
        _purchase(line, ACCOUNT, invoice.day, source_file) for line in invoice.purchases
    )
    return Extraction(
        month=month_start(invoice.first),
        start=invoice.first,
        end=invoice.last,
        account_id=invoice.account_id,
        transactions=transactions,
        controls=(ControlTotal(ACCOUNT, CURRENCY, -invoice.total),),
    )


def extract_account(content: str, source_file: str) -> Extraction:
    """Parse the text of one Swisscard invoice as a statement of the card's own account.

    For the database: the card owes what the invoice says, purchases keep their own dates, and
    the payments received are the card's side of the transfers from the bank. The invoice
    prints its balances, so it reconciles like a bank statement and invoices chain.
    """
    invoice = _read(content)
    if len(invoice.figures) != 4:
        msg = "Swisscard: cannot read the balances of the invoice"
        raise ExtractionError(msg)
    previous, paid, new, balance = invoice.figures
    received = sum((line.amount for line in invoice.payments), Decimal(0))
    if received != paid or new != invoice.total or previous - paid + new != balance:
        msg = (
            f"Swisscard: the invoice says {previous} - {paid} + {new} = {balance}, "
            f"but its lines give payments {received} and transactions {invoice.total}"
        )
        raise ExtractionError(msg)
    payments = tuple(
        Transaction(
            date=line.day,
            account=CARD_ACCOUNT,
            amount=line.amount,
            currency=CURRENCY,
            description=line.text,
            kind=Kind.INTERNAL,  # the other side of the bank's card debit
            source_file=source_file,
            source_ref=line.ref,
            booking_date=invoice.day,
        )
        for line in invoice.payments
    )
    purchases = tuple(
        _purchase(line, CARD_ACCOUNT, invoice.day, source_file) for line in invoice.purchases
    )
    return Extraction(
        month=month_start(invoice.first),
        start=invoice.first,
        end=invoice.last,
        account_id=invoice.account_id,
        transactions=(*payments, *purchases),
        balances=(CashBalance(CARD_ACCOUNT, CURRENCY, -previous, -balance),),
    )


def _purchase(line: _Line, account: str, booked: date, source_file: str) -> Transaction:
    section = line.ref.partition(":")[0]
    kind, category = _classify(line.text, line.amount, section)
    return Transaction(
        date=line.day,
        account=account,
        amount=-line.amount,
        currency=CURRENCY,
        description=f"{line.text} ({line.day:%d.%m.%Y})",
        kind=kind,
        source_file=source_file,
        source_ref=line.ref,
        counterparty=line.text.rpartition(",")[0] or line.text,
        category=category,
        booking_date=booked,
    )


def _read(content: str) -> _Invoice:
    dated = _INVOICE_DATE.search(content)
    if dated is None:
        msg = "Swisscard: no invoice date (is there a text layer?)"
        raise ExtractionError(msg)
    day = parse_swiss_date(dated[1], "Swisscard invoice date")
    lines = [line.strip() for line in content.splitlines()]
    compact = [line.replace(" ", "") for line in lines]
    if _START not in compact:
        msg = 'Swisscard: no "Nuove transazioni" section'
        raise ExtractionError(msg)
    start = compact.index(_START)
    figures = _FIGURES.search(content)
    payments = tuple(
        _line(f"payment:{n}", match)
        for n, line in enumerate(lines[:start], start=1)
        if (match := _TRANSACTION.fullmatch(line))
    )

    purchases: list[_Line] = []
    section = ""  # last four digits of the current card, or "Varie"
    subtotal = total = Decimal(0)
    for n, line in enumerate(lines[start + 1 :], start=1):
        if _INVOICE_TOTAL.fullmatch(line):
            _check(total, line, "the invoice total")
            found = figures.groups() if figures else ()
            amounts = tuple(parse_decimal(f, "Swisscard balance") for f in found)
            first, last = _covered(content, day, (*payments, *purchases))
            number = _ACCOUNT_NUMBER.search(content)
            return _Invoice(
                day,
                payments,
                tuple(purchases),
                total,
                amounts,
                number[1] if number else "",
                first,
                last,
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
            bought = _line(f"{section}:{n}", match)
            subtotal += bought.amount
            total += bought.amount
            purchases.append(bought)
    msg = 'Swisscard: no "Totale nuove transazioni" line'
    raise ExtractionError(msg)


def _covered(content: str, day: date, lines: tuple[_Line, ...]) -> tuple[date, date]:
    """The billing period the invoice prints, else its earliest row to the invoice day."""
    printed = _BILLING.search(content)
    if printed is None:
        return min((line.day for line in lines), default=day), day
    first = parse_swiss_date(f"{printed[1]}20{printed[2]}", "Swisscard billing period")
    last = parse_swiss_date(f"{printed[3]}20{printed[4]}", "Swisscard billing period")
    return first, max(last, day)


def _line(ref: str, match: re.Match[str]) -> _Line:
    day = parse_swiss_date(match[1], "Swisscard transaction date")
    return _Line(ref, day, match[2], parse_decimal(match[3], "Swisscard amount"))


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
