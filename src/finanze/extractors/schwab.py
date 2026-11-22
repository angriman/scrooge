"""Charles Schwab Equity Award Center transactions (CSV) -> the award account.

One account, `Schwab`, in USD: the shares of the plan's company and the cash of their
dividends. The export lists its rows newest first: a `Deposit` is shares that vested, and the
row after it, with no date, gives that award's details (its id, the vest date and the vest's
fair market value); `Dividend` and `Tax Withholding` are the dividends and the US tax withheld
on them.

A Deposit is booked as Morgan Stanley's Release is: the shares that arrive as salary in shares
(`Stipendio`) at the fair market value of the vest and, the same day, their purchase at that
price. No cash moves, and what they cost is what they were worth.

The period runs from the first row to the day of the export, which Schwab names the file after
(`EquityAwardsCenter_Transactions_20261121220549.csv`, exported on 21 November 2026): an award
account that booked nothing for months is not behind. Under another name it ends on the last
row's day.

The export prints no balance and no holdings: the cash and the shares add up from its first
row, and nothing checks them (`unreconciled`). So it should reach back to the first vest, or
the shares and cash from before it are missing. Any other action is refused until it is known.
"""

from __future__ import annotations

import contextlib
import re
from datetime import date
from decimal import Decimal
from pathlib import PurePath

from finanze.errors import ExtractionError, UnsupportedStatementError
from finanze.extractors.common import (
    day_span,
    month_start,
    parse_decimal,
    parse_us_amount,
    parse_us_date,
    read_table,
)
from finanze.models import Extraction, Kind, SecurityTrade, Transaction

ACCOUNT = "Schwab"
CURRENCY = "USD"
_ASSET_TAB = "Stocks"  # the shares of an equity award are the company's own stock
_COLUMNS = (
    "Date",
    "Action",
    "Symbol",
    "Description",
    "Quantity",
    "FeesAndCommissions",
    "Amount",
    "AwardId",
    "VestDate",
    "VestFairMarketValue",
)
# The moment of the export at the end of the file's name: year, month, day, then the time.
_EXPORTED = re.compile(r"_(\d{4})(\d{2})(\d{2})\d{6}$")
_CASH = {
    "Dividend": (Kind.DIVIDEND, "Dividendi"),
    "Tax Withholding": (Kind.TAX, "Ritenute"),
}


def extract(content: str, source_file: str) -> Extraction:
    """Parse the text of a Schwab Equity Award Center transactions export."""
    rows = list(enumerate(read_table(content, ",", _COLUMNS, "Schwab"), start=1))
    transactions: list[Transaction] = []
    days: list[date] = []
    remaining = iter(rows)
    for n, row in remaining:
        if not row["Date"]:
            msg = f"Schwab row {n}: award details with no Deposit before them"
            raise ExtractionError(msg)
        day = _date(row["Date"], n)
        days.append(day)
        fees = row["FeesAndCommissions"]
        if fees and _money(fees, n) != 0:
            msg = f"Schwab row {n}: fees and commissions ({fees}) are not supported yet"
            raise UnsupportedStatementError(msg)
        action = row["Action"]
        if action == "Deposit":
            details = next(remaining, None)
            if details is None or details[1]["Date"] or not details[1]["VestFairMarketValue"]:
                msg = f"Schwab row {n}: a Deposit without the award details after it"
                raise ExtractionError(msg)
            transactions += _vest(day, row, details[1], n, source_file)
        elif action in _CASH:
            transactions.append(_cash(day, row, n, source_file))
        else:
            msg = f"Schwab row {n}: unsupported action {action!r}"
            raise UnsupportedStatementError(msg)
    first, last = day_span(days, "Schwab export")
    exported = _EXPORTED.search(PurePath(source_file).stem)
    if exported is not None:
        with contextlib.suppress(ValueError):  # not a day after all: the last row's day stands
            last = max(last, date(int(exported[1]), int(exported[2]), int(exported[3])))
    return Extraction(
        month=month_start(first),
        start=first,
        end=last,
        transactions=tuple(transactions),
        unreconciled=(ACCOUNT,),
    )


def _vest(
    day: date, row: dict[str, str], details: dict[str, str], n: int, source_file: str
) -> list[Transaction]:
    cash = row["Amount"] or details["Amount"] or details["FeesAndCommissions"]
    if cash or not details["AwardId"]:
        msg = f"Schwab row {n}: a Deposit with cash, fees or no award id is not supported yet"
        raise UnsupportedStatementError(msg)
    quantity = parse_decimal(row["Quantity"], f"Schwab row {n} Quantity")
    price = _money(details["VestFairMarketValue"], n + 1)
    if quantity <= 0 or price <= 0:
        msg = f"Schwab row {n}: a Deposit of {quantity} shares at {price}"
        raise ExtractionError(msg)
    symbol, worth = row["Symbol"], quantity * price
    vested = _date(details["VestDate"], n + 1)
    what = f"{format(quantity, 'f')} {symbol} @ {format(price, 'f')}"
    award = f"award {details['AwardId']}, vested {vested}"
    salary = Transaction(
        date=day,
        account=ACCOUNT,
        amount=worth,
        currency=CURRENCY,
        description=f"Deposit of {what} ({award}): salary in shares",
        kind=Kind.INCOME,
        source_file=source_file,
        source_ref=f"{n}:salary",
        category="Stipendio",
    )
    purchase = SecurityTrade(
        date=day,
        account=ACCOUNT,
        amount=-worth,
        currency=CURRENCY,
        description=f"Deposit of {what} ({award})",
        kind=Kind.SECURITY_TRADE,
        source_file=source_file,
        source_ref=str(n),
        category="Investimenti",
        ticker=symbol,
        quantity=quantity,
        price=price,
        cost=worth,
        asset_tab=_ASSET_TAB,
    )
    return [salary, purchase]


def _cash(day: date, row: dict[str, str], n: int, source_file: str) -> Transaction:
    kind, category = _CASH[row["Action"]]
    return Transaction(
        date=day,
        account=ACCOUNT,
        amount=_money(row["Amount"], n),
        currency=CURRENCY,
        description=f"{row['Action']} {row['Symbol']}",
        kind=kind,
        source_file=source_file,
        source_ref=str(n),
        counterparty=row["Symbol"],
        category=category,
    )


def _money(text: str, n: int) -> Decimal:
    """`$1,234.50` or `-$88.75`."""
    return parse_us_amount(text, f"Schwab row {n}")


def _date(text: str, n: int) -> date:
    """`10/28/2026`: month, day, year."""
    return parse_us_date(text, f"Schwab row {n}")
