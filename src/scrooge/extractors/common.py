"""Parsing helpers shared by the extractors. Every failure is an `ExtractionError`."""

from __future__ import annotations

import calendar
import csv
import io
import re
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

from scrooge.errors import ExtractionError
from scrooge.models import Kind, SecurityTrade, Transaction

_NUMBER = re.compile(r"[+-]?(\d+|\d{1,3}([,']\d{3})+)(\.\d+)?")
_SWISS_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_SWISS_AMOUNT = re.compile(r"(\d{1,3}('\d{3})*)\.\d{2}")
# The bytes of one UTF-8 character read back as Latin-1: a lead byte and its continuations.
_TWICE_ENCODED = re.compile(
    "[\u00c2-\u00df][\u0080-\u00bf]"
    "|[\u00e0-\u00ef][\u0080-\u00bf]{2}"
    "|[\u00f0-\u00f4][\u0080-\u00bf]{3}"
)
_CENTURY = 2000  # of a year printed with two digits
_US_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})")


def parse_decimal(text: str, what: str) -> Decimal:
    """Read `1,234.50`, `1'234.50` or `-12.3`.

    `,` and `'` are accepted only as thousands separators: a decimal comma (`12,50`) is
    rejected rather than read as 1250.
    """
    cleaned = text.strip()
    if not _NUMBER.fullmatch(cleaned):
        msg = f"cannot read amount {text!r} ({what})"
        raise ExtractionError(msg)
    return Decimal(cleaned.replace(",", "").replace("'", ""))


def parse_us_amount(text: str, what: str) -> Decimal:
    """Read money as US statements print it: `$1,234.50`, `-$88.75`, `(0.13)` or `$(0.13)`.

    A minus before the dollar sign and parentheses around the number are a negative amount.
    """
    sign, _, bare = text.strip().rpartition("$")
    if sign not in {"", "-"}:
        msg = f"cannot read amount {text!r} ({what})"
        raise ExtractionError(msg)
    negative = bare.startswith("(") and bare.endswith(")")
    value = parse_decimal(bare[1:-1] if negative else bare, what)
    return -value if negative != (sign == "-") else value


def parse_us_date(text: str, what: str) -> date:
    """Read a date as US statements print it: month, day, year (`10/28/2026`, `9/30/26`)."""
    found = _US_DATE.fullmatch(text.strip())
    if found is not None:
        year = int(found[3]) + (_CENTURY if len(found[3]) == len("26") else 0)
        try:
            return date(year, int(found[1]), int(found[2]))
        except ValueError:
            pass  # 02/30/2026
    msg = f"cannot read date {text!r} ({what})"
    raise ExtractionError(msg)


def parse_swiss_amount(text: str, what: str) -> Decimal:
    """Read `1'234.50` as a Swiss document prints money: nothing else, not even `1234.5`.

    For text that was read off a scan, where a looser reading would let a misread figure pass.
    """
    if not _SWISS_AMOUNT.fullmatch(text):
        msg = f"cannot read amount {text!r} ({what})"
        raise ExtractionError(msg)
    return Decimal(text.replace("'", ""))


def undo_double_encoding(text: str) -> str:
    """`Caffè` for `CaffÃ¨`: each character whose UTF-8 bytes were read as Latin-1 and encoded
    again. Text that only looks like it (bytes that are no UTF-8) is left as it is.
    """

    def decoded(match: re.Match[str]) -> str:
        try:
            return match[0].encode("latin-1").decode("utf-8")
        except UnicodeDecodeError:
            return match[0]

    return _TWICE_ENCODED.sub(decoded, text)


def only_match(pattern: re.Pattern[str], content: str, what: str) -> re.Match[str]:
    """The one place `pattern` is found in `content`: none, or more than one, is an error."""
    found = list(pattern.finditer(content))
    if len(found) != 1:
        msg = f"{what}: found {len(found)} times, expected once"
        raise ExtractionError(msg)
    return found[0]


def parse_iso_date(text: str, what: str) -> date:
    """Read the date of `2026-09-30`, `2026-09-30 12:00:00` or `2026-09-30T12:00:00+0200`."""
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        msg = f"cannot read date {text!r} ({what})"
        raise ExtractionError(msg) from None


def parse_swiss_date(text: str, what: str) -> date:
    """Read `30.09.2026`."""
    match = _SWISS_DATE.fullmatch(text.strip())
    try:
        if match is None:
            raise ValueError
        return date(int(match[3]), int(match[2]), int(match[1]))
    except ValueError:
        msg = f"cannot read date {text!r} ({what})"
        raise ExtractionError(msg) from None


def month_start(day: date) -> date:
    return day.replace(day=1)


def month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def months_between(first: date, last: date) -> list[date]:
    """The first day of every calendar month from `first`'s to `last`'s."""
    months = [month_start(first)]
    while months[-1] < month_start(last):
        months.append(month_start(month_end(months[-1]) + timedelta(days=1)))
    return months


def day_span(days: Iterable[date], what: str) -> tuple[date, date]:
    """The first and the last of `days`."""
    found = sorted(days)
    if not found:
        msg = f"{what} has no rows"
        raise ExtractionError(msg)
    return found[0], found[-1]


def read_table(
    content: str, delimiter: str, required: Iterable[str], what: str
) -> list[dict[str, str]]:
    """Read delimited text with a header row; a missing required column is a format error."""
    reader = csv.DictReader(io.StringIO(content.removeprefix("\ufeff")), delimiter=delimiter)
    missing = sorted(set(required) - set(reader.fieldnames or ()))
    if missing:
        msg = f"{what}: missing column(s) {missing}"
        raise ExtractionError(msg)
    return [{key: value or "" for key, value in row.items() if key} for row in reader]


def share_vest(  # noqa: PLR0913  # what differs between the plans: all of it is the caller's
    *,
    day: date,
    account: str,
    currency: str,
    source_file: str,
    source_ref: str,
    ticker: str,
    quantity: Decimal,
    price: Decimal,
    description: str,
    asset_tab: str,
) -> list[Transaction]:
    """Shares that vest: salary in shares at the vest price and, the same day, their purchase.

    No cash moves, and what the shares cost is what they were worth. The purchase has
    `description` and `source_ref` as given, the salary the same followed by what says it is
    salary in shares.
    """
    worth = quantity * price
    salary = Transaction(
        date=day,
        account=account,
        amount=worth,
        currency=currency,
        description=f"{description}: salary in shares",
        kind=Kind.INCOME,
        source_file=source_file,
        source_ref=f"{source_ref}:salary",
        category="Salary",
    )
    purchase = SecurityTrade(
        date=day,
        account=account,
        amount=-worth,
        currency=currency,
        description=description,
        kind=Kind.SECURITY_TRADE,
        source_file=source_file,
        source_ref=source_ref,
        category="Investments",
        ticker=ticker,
        quantity=quantity,
        price=price,
        cost=worth,
        asset_tab=asset_tab,
    )
    return [salary, purchase]


_FX_CATEGORY_CURRENCIES = {"EUR", "USD"}  # Acquisto <ccy> exists only for these
_HOME_CURRENCY = "CHF"  # converting back into it is a move between one's own accounts


def fx_category(bought: str) -> str:
    """The category of a currency exchange, by the currency it buys.

    Back into the home currency it is a move between one's own accounts (`Own transfer`); EUR and
    USD have an `Acquisto` category of their own; any other currency has none.
    """
    if bought == _HOME_CURRENCY:
        return "Own transfer"
    return f"Buy {bought}" if bought in _FX_CATEGORY_CURRENCIES else ""
