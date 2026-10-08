"""Query strings into what the services take. Nothing here touches the database."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from finanze import query
from finanze.models import Kind


@dataclass(frozen=True, slots=True)
class Parsed:
    wanted: query.Filter
    errors: tuple[str, ...]  # one sentence per field that could not be read; it is then unset


def transactions_filter(params: Mapping[str, str]) -> Parsed:
    """`from`, `to`, `booking`, `owner`, `account`, `category`, `kind`, `min`, `max`, `text`.

    `from` and `to` are a day, a month or a year, as for `finanze query`. An empty field is
    the same as a missing one.
    """
    errors: list[str] = []

    def text(name: str) -> str | None:
        return params.get(name, "").strip() or None

    def day(name: str, end: int) -> date | None:
        value = text(name)
        try:
            return None if value is None else query.span(value)[end]
        except ValueError:
            errors.append(f"'{value}' is not a day, a month or a year like 2025-05-17 or 2025-05.")
            return None

    def amount(name: str) -> Decimal | None:
        value = text(name)
        try:
            number = None if value is None else Decimal(value.replace(",", "").replace("'", ""))
        except InvalidOperation:
            number = Decimal("NaN")
        if number is not None and not number.is_finite():
            errors.append(f"'{value}' is not an amount like 1234.56.")
            return None
        return None if number is None else abs(number)

    kind = text("kind")
    if kind is not None and kind not in set(Kind):
        errors.append(f"'{kind}' is not a kind of transaction.")
        kind = None
    wanted = query.Filter(
        first=day("from", 0),
        last=day("to", 1),
        booking=text("booking") is not None,
        owner=text("owner"),
        account=text("account"),
        category=text("category"),
        kind=None if kind is None else Kind(kind),
        at_least=amount("min"),
        at_most=amount("max"),
        text=text("text") or "",
    )
    return Parsed(wanted, tuple(errors))
