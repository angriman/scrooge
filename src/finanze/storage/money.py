"""Money and decimals as the database stores them (D16).

Amounts are integers in millionths of the currency unit, so SQL sums are exact. Quantities,
prices and rates are text decimals. Nothing here rounds: a value that does not fit is refused.
"""

from __future__ import annotations

from decimal import Decimal

_MICRO = Decimal(1_000_000)


class PrecisionError(ValueError):
    """An amount has more than six decimals: storing it would silently change it."""


def to_micro(amount: Decimal) -> int:
    """`Decimal("12.5")` -> `12500000`."""
    _require_decimal(amount)
    scaled = amount * _MICRO
    if scaled != scaled.to_integral_value():
        msg = f"{amount} has more than 6 decimals"
        raise PrecisionError(msg)
    return int(scaled)


def from_micro(micro: int) -> Decimal:
    """`12500000` -> `Decimal("12.5")`."""
    return Decimal(micro).scaleb(-6).normalize() + Decimal(0)  # + 0: no exponent form, no -0


def to_text(value: Decimal) -> str:
    """A quantity, price or rate as plain digits (`0.00001`, never `1E-5`)."""
    _require_decimal(value)
    return format(value, "f")


def from_text(text: str) -> Decimal:
    return Decimal(text)


def _require_decimal(value: object) -> None:
    # Runtime guard: SQLite would store a float given for a TEXT decimal as its text.
    if not isinstance(value, Decimal):
        msg = f"expected Decimal, got {type(value).__name__}"
        raise TypeError(msg)
