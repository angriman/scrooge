"""Parsing helpers shared by the extractors. Every failure is an `ExtractionError`."""

from __future__ import annotations

import calendar
import csv
import io
import re
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

from finanze.errors import ExtractionError

_NUMBER = re.compile(r"[+-]?(\d+|\d{1,3}([,']\d{3})+)(\.\d+)?")
_SWISS_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


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


def single_month(days: Iterable[date], what: str) -> date:
    """First day of the one calendar month all `days` fall in."""
    months = sorted({month_start(day) for day in days})
    if len(months) != 1:
        found = ", ".join(f"{m:%Y-%m}" for m in months) or "no rows"
        msg = f"{what} must cover exactly one calendar month, got {found}"
        raise ExtractionError(msg)
    return months[0]


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
