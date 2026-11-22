"""finpension pillar 3a transactions report -> normalized transactions.

One report covers one portfolio from its first deposit on, so the statement usually spans many
months. The report does not say which portfolio it is: finpension numbers the downloads
(`transaction_report-2.tsv`), and that number, when the file name ends in one, names the
account. `Balance` is the running cash: every row is checked against it. Buys and sells are
trades in fund units, identified by ISIN, at the price the report gives; `Cash Flow` carries
up to six decimals and is kept as it is.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import PurePath

from finanze.errors import ExtractionError, UnsupportedStatementError
from finanze.extractors.common import (
    month_start,
    parse_decimal,
    parse_iso_date,
    read_table,
)
from finanze.models import CashBalance, Extraction, Kind, SecurityTrade, Transaction

ACCOUNT = "Finpension 3a"
CURRENCY = "CHF"
ASSET_TAB = "Funds"

_COLUMNS = (
    "Date",
    "Category",
    "Asset Name",
    "ISIN",
    "Number of Shares",
    "Currency Rate",
    "Asset Price in CHF",
    "Cash Flow",
    "Balance",
)
_DELIMITERS = ("\t", ";", ",")
_ROUNDING = Decimal("0.000001")
_NUMBERED = re.compile(r"[-_ ](\d+)$")
_TRADES = {"Buy": 1, "Sell": -1}  # the sign `Number of Shares` must have
_CASH = {
    # Money in from the bank. A transfer, so it is not counted as invested twice: what counts
    # is the purchase of fund units that follows.
    "Deposit": (Kind.TRANSFER, "Investimenti"),
    # Money moved in from another pillar 3a provider: a transfer like a deposit.
    "Transfer": (Kind.TRANSFER, "Investimenti"),
    "Dividend": (Kind.DIVIDEND, "Dividendi"),
    "Interests": (Kind.INTEREST, "Interessi"),
    "Flat-rate administrative fee": (Kind.COMMISSION, "Commissioni"),
}


def extract(content: str, source_file: str) -> Extraction:
    """Parse the text of a finpension transactions report, whatever period it covers."""
    text = content.removeprefix("﻿")
    header = text.partition("\n")[0]
    delimiter = next((d for d in _DELIMITERS if d in header), _DELIMITERS[0])
    rows = read_table(text, delimiter, _COLUMNS, "finpension")
    if not rows:
        msg = "finpension report has no rows, so no balance to reconcile"
        raise ExtractionError(msg)

    numbered = _NUMBERED.search(PurePath(source_file).stem)
    account = f"{ACCOUNT} {numbered[1]}" if numbered else ACCOUNT
    days = [parse_iso_date(row["Date"], "finpension Date") for row in rows]
    if days != sorted(days):
        msg = "finpension report is not in date order"
        raise ExtractionError(msg)
    opening = _number(rows[0], "Balance") - _number(rows[0], "Cash Flow")
    balance = opening
    transactions: list[Transaction] = []
    for n, (row, day) in enumerate(zip(rows, days, strict=True), start=1):
        amount = _number(row, "Cash Flow")
        after = _number(row, "Balance")
        if balance + amount != after:
            msg = f"finpension row {n}: balance {after} does not follow {balance} {amount:+}"
            raise ExtractionError(msg)
        balance = after
        if _number(row, "Currency Rate") != 1:
            msg = f"finpension row {n}: unsupported currency rate {row['Currency Rate']!r}"
            raise UnsupportedStatementError(msg)
        what, fund = row["Category"], row["Asset Name"]
        if what not in _TRADES and what not in _CASH:
            msg = f"finpension row {n}: unsupported category {what!r}"
            raise UnsupportedStatementError(msg)
        if what == "Transfer" and (row["ISIN"] or row["Number of Shares"] or amount <= 0):
            # Fund units moved, or money moved out, would leave holdings or net worth silently.
            msg = f"finpension row {n}: only a transfer of cash in is supported"
            raise UnsupportedStatementError(msg)
        kind, category = _CASH.get(what, (Kind.SECURITY_TRADE, "Investimenti"))
        cash = Transaction(
            date=day,
            account=account,
            amount=amount,
            currency=CURRENCY,
            description=f"{what}: {fund}" if fund else what,
            kind=kind,
            source_file=source_file,
            source_ref=str(n),
            counterparty=row["ISIN"],
            category=category,
        )
        transactions.append(_trade(cash, row, n) if what in _TRADES else cash)
    return Extraction(
        month=month_start(days[0]),
        start=days[0],
        transactions=tuple(transactions),
        balances=(CashBalance(account, CURRENCY, opening, balance),),
        end=days[-1],
    )


def _number(row: dict[str, str], column: str) -> Decimal:
    return parse_decimal(row[column], f"finpension {column}")


def _trade(cash: Transaction, row: dict[str, str], n: int) -> SecurityTrade:
    """A buy or sell of fund units. The cash must be exactly units x price."""
    quantity = _number(row, "Number of Shares")
    price = _number(row, "Asset Price in CHF")
    if not row["ISIN"]:
        msg = f"finpension row {n}: a {row['Category']} without an ISIN"
        raise ExtractionError(msg)
    if quantity * _TRADES[row["Category"]] <= 0:
        msg = f"finpension row {n}: {quantity} units is the wrong sign for a {row['Category']}"
        raise ExtractionError(msg)
    if abs(quantity * price + cash.amount) >= _ROUNDING:  # the cash is rounded to 6 decimals
        msg = f"finpension row {n}: {quantity} x {price} is not the cash flow {cash.amount}"
        raise ExtractionError(msg)
    return SecurityTrade(
        date=cash.date,
        account=cash.account,
        amount=cash.amount,
        currency=cash.currency,
        description=f"{cash.description} ({quantity} @ {price})",
        kind=cash.kind,
        source_file=cash.source_file,
        source_ref=cash.source_ref,
        counterparty=cash.counterparty,
        category=cash.category,
        ticker=row["ISIN"],
        quantity=quantity,
        price=price,
        cost=-cash.amount,
        asset_tab=ASSET_TAB,
    )
