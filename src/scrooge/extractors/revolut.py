"""Revolut account statement (TSV or CSV) -> normalized transactions.

An export holds one currency or, when every account is downloaded together, all of them: each
currency is an account of its own. Rows of a currency are in completion order and `Balance` is
its running balance after each completed row, so every chain is checked here and its ends
become the opening and closing balance. A row changes the balance by `Amount - Fee`. Pending
and reverted rows never touched the balance. Savings are held under the same sheet account, so
transfers to and from them are internal. The export encodes every accented letter twice
(`CaffÃ¨`): descriptions are read back as they were meant (`Caffè`).
"""

from __future__ import annotations

import re
from decimal import Decimal

from scrooge.categories import FEES, REVOLUT_TOP_UP, WITHDRAWALS
from scrooge.errors import ExtractionError, UnsupportedStatementError
from scrooge.extractors.common import (
    day_span,
    fx_category,
    month_start,
    parse_decimal,
    parse_iso_date,
    read_table,
    undo_double_encoding,
)
from scrooge.extractors.source import Source, starts_with
from scrooge.models import CashBalance, Extraction, Kind, Transaction

_COLUMNS = (
    "Type",
    "Product",
    "Started Date",
    "Completed Date",
    "Description",
    "Amount",
    "Fee",
    "Currency",
    "State",
    "Balance",
)
_NO_CASH_STATES = {"PENDING", "REVERTED"}
_CARD_TOPUP = re.compile(r"(Top-up|(Google|Apple) Pay deposit) by \*", re.IGNORECASE)
_EXCHANGED_TO = re.compile(r"Exchanged? to ([A-Z]{3})")
# "Savings Vault topup" is what the first deposit was called when the savings were opened
# The app's language decides the wording: "Fondi monetari flessibili" is the Italian one.
_OWN_SAVINGS = re.compile(
    r"((To|From) ([A-Z]{3} )?(Flexible Cash Funds|Fondi monetari flessibili)"
    r"|Savings Vault topup)\b"
)


def extract(content: str, source_file: str) -> Extraction:
    """Parse the text of one Revolut statement, whatever period it covers."""
    delimiter = "\t" if "\t" in content.partition("\n")[0] else ","
    rows = [
        (n, {**row, "Description": undo_double_encoding(row["Description"])})
        for n, row in enumerate(read_table(content, delimiter, _COLUMNS, "Revolut"), start=1)
        if _has_cash(row, n)
    ]
    if not rows:
        msg = "Revolut statement has no completed rows"
        raise ExtractionError(msg)
    started = [parse_iso_date(row["Started Date"], "Revolut Started Date") for _, row in rows]
    # The balance chain follows completion, and a payment can start before the statement does:
    # the period is the months in which rows were completed.
    completed = [parse_iso_date(row["Completed Date"], "Revolut Completed Date") for _, row in rows]
    first, last = day_span(completed, "Revolut statement")

    transactions: list[Transaction] = []
    opening: dict[str, Decimal] = {}
    balance: dict[str, Decimal] = {}
    for (n, row), start, end in zip(rows, started, completed, strict=True):
        currency = row["Currency"]
        account = f"Revolut {currency}"
        amount = parse_decimal(row["Amount"], "Revolut Amount")
        fee = parse_decimal(row["Fee"], "Revolut Fee")
        after = parse_decimal(row["Balance"], "Revolut Balance")
        # The statement has no opening balance: it is what a currency's first row started from.
        before = balance.get(currency, opening.setdefault(currency, after - amount + fee))
        if before + amount - fee != after:
            msg = f"Revolut row {n}: balance {after} does not follow {before} {amount:+} -{fee}"
            raise ExtractionError(msg)
        balance[currency] = after
        # `date` is the day the payment started; the balance books it on its completion date,
        # whatever period the file covers, so overlapping exports agree on the booking date.
        day = end
        for ref, value, (kind, category), description in (
            (str(n), amount, _classify(row, amount, n), row["Description"]),
            (f"{n}:fee", -fee, *_fee(row)),
        ):
            if value != 0:
                transactions.append(
                    Transaction(
                        date=start,
                        account=account,
                        amount=value,
                        currency=currency,
                        description=description,
                        kind=kind,
                        source_file=source_file,
                        source_ref=ref,
                        counterparty=row["Description"],
                        category=category,
                        booking_date=day,
                    )
                )
    return Extraction(
        month=month_start(first),
        start=first,
        transactions=tuple(transactions),
        balances=tuple(
            CashBalance(f"Revolut {currency}", currency, opened, balance[currency])
            for currency, opened in opening.items()
        ),
        end=last,
    )


def _has_cash(row: dict[str, str], n: int) -> bool:
    if row["Product"] == "Deposit":  # the savings side of transfers, mirrored in older exports
        return False
    if row["Product"] != "Current":
        msg = f"Revolut row {n}: unsupported product {row['Product']!r}"
        raise UnsupportedStatementError(msg)
    if row["State"] == "COMPLETED":
        return True
    if row["State"] in _NO_CASH_STATES:
        return False
    msg = f"Revolut row {n}: unsupported state {row['State']!r}"
    raise UnsupportedStatementError(msg)


def _type(row: dict[str, str]) -> str:
    return row["Type"].upper().replace(" ", "_")  # "Card Payment" and CARD_PAYMENT both occur


def _classify(row: dict[str, str], amount: Decimal, n: int) -> tuple[Kind, str]:
    kind = _type(row)
    description = row["Description"]
    by_sign = (Kind.EXPENSE if amount < 0 else Kind.INCOME, "")  # category from rules.yaml
    if kind == "TOPUP" or (kind == "DEPOSIT" and _CARD_TOPUP.match(description)):
        return Kind.TRANSFER, REVOLUT_TOP_UP
    if kind == "EXCHANGE":
        match = _EXCHANGED_TO.search(description)
        bought = match[1] if match else ""
        return Kind.FX, fx_category(bought)
    if kind == "ATM":
        return Kind.TRANSFER, WITHDRAWALS
    if kind in {"TRANSFER", "REV_PAYMENT"}:
        if _OWN_SAVINGS.match(description):
            return Kind.INTERNAL, ""  # to/from the savings held under the same sheet account
        return by_sign
    if kind in {"CARD_PAYMENT", "CARD_REFUND", "DEPOSIT", "CHARGE", "FEE"}:
        return by_sign
    msg = f"Revolut row {n}: unsupported type {row['Type']!r}"
    raise UnsupportedStatementError(msg)


def _fee(row: dict[str, str]) -> tuple[tuple[Kind, str], str]:
    if _type(row) in {"CHARGE", "FEE"}:  # the fee is the charge itself (e.g. "Metal plan fee")
        return (Kind.EXPENSE, ""), row["Description"]
    return (Kind.COMMISSION, FEES), f"Fee: {row['Description']}"


SOURCE = Source(
    name="revolut",
    label="Revolut account statement",
    reads="text",
    recognises=starts_with(
        "Type\tProduct\t",
        "Type,Product,",
    ),
    extract=extract,
)
