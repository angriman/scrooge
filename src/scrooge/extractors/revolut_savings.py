"""Revolut Savings (Flexible Cash Funds) statement -> interest and the fund's fee (D8).

Daily `Return PAID` / `Interest PAID` rows are the interest and `Service Fee Charged` rows the
commission taken from it; fund BUY/SELL rows mirror transfers already seen on the current
account and are ignored. A purchase of less than one unit that does not reinvest a return is
interest too: the fund buys a few cents now and then that no transfer pays for (decided
2026-11-06). The export prints no balance, so the account is reported as unreconciled.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal

from scrooge.errors import ExtractionError, UnsupportedStatementError
from scrooge.extractors.common import day_span, month_start, parse_decimal, read_table
from scrooge.extractors.source import Source, starts_with
from scrooge.models import Extraction, Kind, Transaction

_DATE = re.compile(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})\b")
_MONTHS = {name: number for number, name in enumerate(calendar.month_abbr) if name}
_BOOKED = (
    (re.compile(r"(Return PAID|Interest PAID)\b"), Kind.INTEREST, "Interessi"),
    (re.compile(r"Service Fee Charged\b"), Kind.COMMISSION, "Commissioni"),
)
# Fund movements, incl. interest already counted when PAID and later moved to the current account
_IGNORED = re.compile(
    r"(BUY|SELL|Interest Reinvested|Interest WITHDRAWN|Return Reinvested|Return WITHDRAWN)\b"
)
_BUY = re.compile(r"BUY\b")
_REINVESTED = re.compile(r"(Interest|Return) Reinvested\b")
# A purchase below this, with no reinvested return behind it, has no transfer behind it either
_TOP_UP = Decimal(1)
# How long after a return is reinvested the fund buys it: up to five days in the exports seen
_REINVESTING = timedelta(days=7)


def extract(content: str, source_file: str) -> Extraction:
    """Parse the text of a Revolut Savings TSV, whatever period it covers."""
    header = content.removeprefix("\ufeff").partition("\n")[0].split("\t")
    values = [column for column in header if column.startswith("Value, ")]
    if not values:
        msg = "Revolut Savings: no 'Value, <currency>' column"
        raise ExtractionError(msg)
    column = values[0]  # the fund's own currency comes first, then the CHF equivalent
    currency = column.removeprefix("Value, ")
    account = f"Revolut {currency}"

    transactions: list[Transaction] = []
    days: list[date] = []
    rows = read_table(content, "\t", ("Date", "Description", column), "Revolut Savings")
    top_ups = _top_ups(rows, column)
    for n, row in enumerate(rows, start=1):
        description = row["Description"]
        day = _date(row["Date"])
        days.append(day)
        if n in top_ups:
            kind, category = Kind.INTEREST, "Interessi"
        elif _IGNORED.match(description):
            continue
        else:
            kind, category = _classify(description, n)
        transactions.append(
            Transaction(
                date=day,
                account=account,
                amount=parse_decimal(row[column], "Revolut Savings value"),
                currency=currency,
                description=description,
                kind=kind,
                source_file=source_file,
                source_ref=str(n),
                category=category,
            )
        )
    first, last = day_span(days, "Revolut Savings statement")
    return Extraction(
        month=month_start(first),
        transactions=tuple(transactions),
        unreconciled=(account,),
        end=last,
        start=first,
    )


def _top_ups(rows: list[dict[str, str]], column: str) -> set[int]:
    """The numbers of the rows that are top-ups: purchases below one unit that no return
    reinvested in the week before pays for. A reinvested return pays for the first purchase of
    as much after it."""
    reinvested: list[tuple[date, Decimal]] = []
    buys: list[tuple[date, int, Decimal]] = []
    for n, row in enumerate(rows, start=1):
        description = row["Description"]
        if _REINVESTED.match(description) or _BUY.match(description):
            day = _date(row["Date"])
            amount = parse_decimal(row[column], "Revolut Savings value")
            if _BUY.match(description):
                buys.append((day, n, amount))
            else:
                reinvested.append((day, abs(amount)))
    found: set[int] = set()
    for day, n, amount in sorted(buys):  # the rows come newest first
        paid = [r for r in reinvested if r[1] == amount and r[0] <= day <= r[0] + _REINVESTING]
        if paid:
            reinvested.remove(paid[0])
        elif amount < _TOP_UP:
            found.add(n)
    return found


def _classify(description: str, n: int) -> tuple[Kind, str]:
    for pattern, kind, category in _BOOKED:
        if pattern.match(description):
            return kind, category
    msg = f"Revolut Savings row {n}: unsupported row {description!r}"
    raise UnsupportedStatementError(msg)


def _date(text: str) -> date:
    """Read the date of `Sep 30, 2026, 2:12:10 AM`."""
    match = _DATE.match(text)
    try:
        if match is None:
            raise ValueError
        return date(int(match[3]), _MONTHS[match[1]], int(match[2]))
    except (KeyError, ValueError):
        msg = f"cannot read date {text!r} (Revolut Savings)"
        raise ExtractionError(msg) from None


SOURCE = Source(
    name="revolut_savings",
    label="Revolut Savings statement",
    reads="text",
    recognises=starts_with("Date\tDescription\tValue, "),
    extract=extract,
)
