"""Morgan Stanley (StockPlan Connect) quarterly statement PDF -> the share plan's account.

The router reads the PDF's text layer; `extract` parses that text and is pure. One account,
`Morgan Stanley`: the shares of the plan's one company and a few cents of cash, in USD.

A Release is a vest: the shares that arrive, booked as salary (`Stipendio`) at the price
printed with them and, the same day, as their purchase at that price. No cash moves, and what
they cost is what they were worth. A Share Withdrawal moves shares out in kind (to IBKR): no
cash, no price. A Stock Split prints its ratio (`20 for 1`) and is no transaction: the shares
are multiplied once, by the split stored with the prices (`splits`), so here it only makes the
rows lead to the closing shares: what is booked on its day or after is counted in the new ones.
The cash rows are the dividends on what is left, the tax withheld on them and their payment out.

The statement prints the shares and the cash it opened and closed with, and both must follow
from the rows. A withdrawal on the last day can still be counted in the closing shares (the
move settles after it): the shares held at the end are then the printed ones less it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import NamedTuple

from scrooge.errors import ExtractionError, UnsupportedStatementError
from scrooge.extractors.common import month_start, parse_us_amount, parse_us_date, share_vest
from scrooge.extractors.source import Source
from scrooge.models import (
    CashBalance,
    Extraction,
    Holding,
    Kind,
    PrintedValue,
    SecurityTrade,
    Transaction,
)

ACCOUNT = "Morgan Stanley"
CURRENCY = "USD"
_ASSET_TAB = "Stocks"
# What the statement calls the plan's shares -> the ticker the other sources use.
_TICKERS = {"ALPHABET INC CL C": "GOOG"}
_ISSUER = "Morgan Stanley Smith Barney"
_PLAN = "www.stockplanconnect.com"

_US_DATE = r"\d{1,2}/\d{1,2}/\d{2}"
_COVERED = re.compile(rf"^\(as of ({_US_DATE})\) \(as of ({_US_DATE})\)$", re.MULTILINE)
_ACCOUNT_NUMBER = re.compile(r"^Account Number: (\S+)", re.MULTILINE)
_ACTIVITY = "SHARE PURCHASE AND HOLDINGS"
_ROW = re.compile(rf"({_US_DATE}) (.+)")
_RELEASE = re.compile(r"Release (\S+) (\S+)")
_WITHDRAWAL = re.compile(r"Share Withdrawal (\S+)")
_SPLIT = re.compile(r"Stock Split\b.*")
_RATIO = re.compile(r"Stock Split\b.*?\b(\d+) for (\d+)\b.*")


class _Split(NamedTuple):
    """So many new shares (`new`) for so many old ones (`old`), from `day`."""

    day: date
    new: int
    old: int


class _CashRow(NamedTuple):
    """A row that moves cash only: its amount is the last figure the pattern captures."""

    pattern: re.Pattern[str]
    kind: Kind
    category: str
    description: str


_CASH_ROWS = (
    # Gross, then net of fees: the tax withheld is a row of its own.
    _CashRow(re.compile(r"Dividend Credit \S+ (\S+)"), Kind.DIVIDEND, "Dividendi", "Dividend"),
    _CashRow(re.compile(r"Withholding Tax (\S+)"), Kind.TAX, "Ritenute", "Withholding tax"),
    _CashRow(
        re.compile(r"Proceeds Disbursement (\S+)"),
        Kind.TRANSFER,
        "Giroconto",
        "Proceeds disbursement",
    ),
    _CashRow(
        re.compile(r"Cancel Proceeds Disb (\S+)"),
        Kind.TRANSFER,
        "Giroconto",
        "Proceeds disbursement cancelled",
    ),
)


def recognises(text: str) -> bool:
    """Whether the text of a PDF is a statement of a Morgan Stanley stock plan account."""
    return _ISSUER in text and _PLAN in text and _ACTIVITY in text


@dataclass(frozen=True, slots=True)
class _Summary:
    """The opening and closing figures of page 1. An account opened during the period prints
    only the closing ones; it opened with nothing."""

    shares: tuple[Decimal, Decimal]
    price: Decimal  # the close of the last day
    share_value: Decimal
    cash: tuple[Decimal, Decimal]  # settled cash plus what is not settled yet
    total: Decimal


def extract(content: str, source_file: str) -> Extraction:
    """Parse the text of one quarterly statement."""
    first, last = _covered(content)
    ticker = _ticker(content)
    summary = _summary(content)
    transactions: list[Transaction] = []
    splits: list[_Split] = []
    for n, (day, activity) in enumerate(_rows(content), start=1):
        if not first <= day <= last:
            msg = f"Morgan Stanley row {n}: {day} is outside the period {first} to {last}"
            raise ExtractionError(msg)
        if _SPLIT.fullmatch(activity):
            splits.append(_split(day, activity))
            continue
        transactions += _transactions(day, activity, ticker, f"{n}", source_file)
    held = _held(summary, transactions, splits, last)
    # The statement's own value, unless shares in transit are taken out of it.
    worth = summary.share_value if held == summary.shares[1] else held * summary.price
    return Extraction(
        month=month_start(first),
        start=first,
        end=last,
        account_id=_account_number(content),
        transactions=tuple(transactions),
        balances=(CashBalance(ACCOUNT, CURRENCY, *summary.cash),),
        holdings=(
            Holding(
                ACCOUNT,
                ticker,
                CURRENCY,
                held,
                summary.price,
                worth.quantize(Decimal("0.01")),
                asset_tab=_ASSET_TAB,
            ),
        ),
        values=(
            PrintedValue("Stock", CURRENCY, summary.share_value),
            PrintedValue("Cash", CURRENCY, summary.cash[1]),
            PrintedValue("Total", CURRENCY, summary.total),
        ),
    )


def _transactions(
    day: date, activity: str, ticker: str, ref: str, source_file: str
) -> list[Transaction]:
    if match := _RELEASE.fullmatch(activity):
        return _release(day, match, ticker, ref, source_file)
    if match := _WITHDRAWAL.fullmatch(activity):
        quantity = _amount(match[1], "Share Withdrawal quantity")
        return [
            SecurityTrade(
                date=day,
                account=ACCOUNT,
                amount=Decimal(0),
                currency=CURRENCY,
                description=f"Share withdrawal: {format(-quantity, 'f')} {ticker}",
                kind=Kind.TRANSFER,
                source_file=source_file,
                source_ref=f"Share Withdrawal:{ref}",
                category="Giroconto",
                ticker=ticker,
                quantity=quantity,
                price=None,  # moved in kind: the statement prints no price
                cost=Decimal(0),
                asset_tab=_ASSET_TAB,
            )
        ]
    for row in _CASH_ROWS:
        if match := row.pattern.fullmatch(activity):
            return [_cash(day, match[1], row, ref, source_file)]
    msg = f"Morgan Stanley: unsupported activity {activity!r} on {day}"
    raise UnsupportedStatementError(msg)


def _release(
    day: date, match: re.Match[str], ticker: str, ref: str, source_file: str
) -> list[Transaction]:
    quantity = _amount(match[1], "Release quantity")
    price = _amount(match[2], "Release price")
    what = f"{format(quantity, 'f')} {ticker} @ {format(price, 'f')}"
    return share_vest(
        day=day,
        account=ACCOUNT,
        currency=CURRENCY,
        source_file=source_file,
        source_ref=f"Release:{ref}",
        ticker=ticker,
        quantity=quantity,
        price=price,
        description=f"Release of {what}",
        asset_tab=_ASSET_TAB,
    )


def _cash(day: date, text: str, row: _CashRow, ref: str, source_file: str) -> Transaction:
    return Transaction(
        date=day,
        account=ACCOUNT,
        amount=_amount(text, row.description),
        currency=CURRENCY,
        description=row.description,
        kind=row.kind,
        source_file=source_file,
        source_ref=f"{row.description}:{ref}",
        category=row.category,
    )


def _split(day: date, activity: str) -> _Split:
    match = _RATIO.fullmatch(activity)
    if match is None or not int(match[1]) or not int(match[2]):
        msg = f"Morgan Stanley: cannot read the ratio of {activity!r} on {day}"
        raise ExtractionError(msg)
    return _Split(day, int(match[1]), int(match[2]))


def _held(
    summary: _Summary, transactions: list[Transaction], splits: list[_Split], last: date
) -> Decimal:
    """The shares held at the end of the period, checked against the printed ones.

    A split multiplies what was held overnight, so what is booked on its day is already in
    the new shares (as `storage.holdings` has it).
    """
    trades = [t for t in transactions if isinstance(t, SecurityTrade)]
    held = summary.shares[0]
    for _day, _, item in sorted(
        [(s.day, 0, s) for s in splits] + [(t.date, 1, t) for t in trades],
        key=lambda event: event[:2],
    ):
        if isinstance(item, _Split):
            held = held * item.new / item.old
        else:
            held += item.quantity
    if held == summary.shares[1]:
        return held
    # Shares withdrawn on the last day can still be in the closing figure: in transit.
    pending = -sum((t.quantity for t in trades if t.quantity < 0 and t.date == last), Decimal(0))
    if pending and held + pending == summary.shares[1]:
        return held
    msg = (
        f"Morgan Stanley: the statement opens with {summary.shares[0]} shares and closes with "
        f"{summary.shares[1]}, but its rows leave {held}"
    )
    raise ExtractionError(msg)


def _rows(content: str) -> list[tuple[date, str]]:
    """The dated rows of the activity section, in the order printed."""
    _, found, activity = content.partition(_ACTIVITY)
    if not found:
        msg = f'Morgan Stanley: no "{_ACTIVITY}" section'
        raise ExtractionError(msg)
    return [
        (_us_date(match[1], "Morgan Stanley row"), match[2])
        for line in activity.splitlines()
        if (match := _ROW.fullmatch(line.strip()))
    ]


def _summary(content: str) -> _Summary:
    shares = _pair(content, "Number of Shares")
    prices = _pair(content, "Share Price")
    values = _pair(content, "Share Value")
    settled = _pair(content, "Cash Value")
    unsettled = _pair(content, "Net Unsettled Cash")
    totals = _pair(content, "Total Account Value")
    cash = (settled[0] + unsettled[0], settled[1] + unsettled[1])
    return _Summary(shares, prices[1], values[1], cash, totals[1])


def _pair(content: str, label: str) -> tuple[Decimal, Decimal]:
    """The opening and the closing figure of a line of the summary."""
    lines = re.findall(rf"^{label} (.+)$", content, re.MULTILINE)
    if len(lines) != 1:
        msg = f"Morgan Stanley: found {len(lines)} lines {label!r}, expected one"
        raise ExtractionError(msg)
    figures = [_amount(text, label) for text in lines[0].split()]
    if len(figures) == 1:
        return Decimal(0), figures[0]
    if len(figures) == 2:  # opening and closing
        return figures[0], figures[1]
    msg = f"Morgan Stanley: cannot read {label!r}: {lines[0]!r}"
    raise ExtractionError(msg)


def _covered(content: str) -> tuple[date, date]:
    found = _COVERED.findall(content)
    if len(found) != 1:
        msg = "Morgan Stanley: no period (is there a text layer?)"
        raise ExtractionError(msg)
    first, last = found[0]
    return _us_date(first, "opening day"), _us_date(last, "closing day")


def _ticker(content: str) -> str:
    match = re.search(r"^Issuer Description: (.+)$", content, re.MULTILINE)
    if match is None:
        msg = "Morgan Stanley: no issuer description"
        raise ExtractionError(msg)
    for description, ticker in _TICKERS.items():
        if match[1].startswith(f"{description} "):
            return ticker
    msg = f"Morgan Stanley: unknown plan shares {match[1]!r}"
    raise UnsupportedStatementError(msg)


def _account_number(content: str) -> str:
    numbers: set[str] = set(_ACCOUNT_NUMBER.findall(content))
    if len(numbers) != 1:
        msg = f"Morgan Stanley: expected one account number, found {sorted(numbers)}"
        raise ExtractionError(msg)
    return numbers.pop()


def _amount(text: str, what: str) -> Decimal:
    """Read `$1,234.50`, `(0.13)` or `$(0.13)`: parentheses are a negative amount."""
    return parse_us_amount(text, f"Morgan Stanley {what}")


def _us_date(text: str, what: str) -> date:
    """Read `9/30/26`: month, day, two-digit year."""
    return parse_us_date(text, what)


SOURCE = Source(
    name="morganstanley",
    label="Morgan Stanley stock plan statement",
    reads="pdf",
    recognises=recognises,
    extract=extract,
)
