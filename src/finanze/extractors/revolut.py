"""Revolut account statement (TSV or CSV, one file per currency) -> normalized transactions.

Rows are in completion order and `Balance` is the running balance after each completed row, so
the whole chain is checked here and its ends become the opening and closing balance. A row
changes the balance by `Amount - Fee`. Pending and reverted rows never touched the balance.
Savings are held under the same sheet account, so transfers to and from them are internal.
"""

from __future__ import annotations

import re
from decimal import Decimal

from finanze.errors import ExtractionError, UnsupportedStatementError
from finanze.extractors.common import parse_decimal, parse_iso_date, read_table, whole_months
from finanze.models import CashBalance, Extraction, Kind, Transaction

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
_OWN_SAVINGS = re.compile(r"(To|From) ([A-Z]{3} )?Flexible Cash Funds\b")
_FX_CATEGORY_CURRENCIES = {"EUR", "USD"}  # Acquisto <ccy> exists only for these


def extract(content: str, source_file: str) -> Extraction:
    """Parse the text of one Revolut statement, whatever period it covers."""
    delimiter = "\t" if "\t" in content.partition("\n")[0] else ","
    rows = [
        (n, row)
        for n, row in enumerate(read_table(content, delimiter, _COLUMNS, "Revolut"), start=1)
        if _has_cash(row, n)
    ]
    if not rows:
        msg = "Revolut statement has no completed rows"
        raise ExtractionError(msg)
    currencies = {row["Currency"] for _, row in rows}
    if len(currencies) != 1:
        msg = f"Revolut statement mixes currencies {sorted(currencies)}"
        raise ExtractionError(msg)
    currency = currencies.pop()
    account = f"Revolut {currency}"
    started = [parse_iso_date(row["Started Date"], "Revolut Started Date") for _, row in rows]
    # The balance chain follows completion, and a payment can start before the statement does:
    # the period is the months in which rows were completed.
    completed = [parse_iso_date(row["Completed Date"], "Revolut Completed Date") for _, row in rows]
    first, last = whole_months(completed, "Revolut statement")

    transactions: list[Transaction] = []
    # The statement has no opening balance: it is what the first row started from.
    _, first_row = rows[0]
    opening = (
        parse_decimal(first_row["Balance"], "Revolut Balance")
        - parse_decimal(first_row["Amount"], "Revolut Amount")
        + parse_decimal(first_row["Fee"], "Revolut Fee")
    )
    balance = opening
    for (n, row), start, end in zip(rows, started, completed, strict=True):
        amount = parse_decimal(row["Amount"], "Revolut Amount")
        fee = parse_decimal(row["Fee"], "Revolut Fee")
        after = parse_decimal(row["Balance"], "Revolut Balance")
        if balance + amount - fee != after:
            msg = f"Revolut row {n}: balance {after} does not follow {balance} {amount:+} -{fee}"
            raise ExtractionError(msg)
        balance = after
        # D4: the transaction (start) date, unless the payment started before the statement:
        # it is part of this statement's balance, so it is booked on its completion date.
        day = start if first <= start <= last else end
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
        month=first,
        transactions=tuple(transactions),
        balances=(CashBalance(account, currency, opening, balance),),
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
        return Kind.TRANSFER, "Topup Revolut"
    if kind == "EXCHANGE":
        match = _EXCHANGED_TO.search(description)
        bought = match[1] if match else ""
        return Kind.FX, f"Acquisto {bought}" if bought in _FX_CATEGORY_CURRENCIES else ""
    if kind == "ATM":
        return Kind.TRANSFER, "Prelievi"
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
    return (Kind.COMMISSION, "Commissioni"), f"Fee: {row['Description']}"
