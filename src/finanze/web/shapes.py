"""Where the holdings page draws its shapes: a map of rectangles, bars cut in shares, bars
either side of zero, and bills along the days to their maturity.

Only geometry: every amount arrives worked out, and the shapes are in the units of the SVG
they are drawn in, to a tenth.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from finanze import portfolio

_TENTH = Decimal("0.1")


@dataclass(frozen=True, slots=True)
class Rect:
    x: Decimal
    y: Decimal
    width: Decimal
    height: Decimal


@dataclass(frozen=True, slots=True)
class Piece:
    """One part of a bar cut in shares."""

    name: str
    value: Decimal
    share: Decimal  # of the bar, in percent; they add up to 100
    x: Decimal  # where it starts, in hundredths of the bar
    slot: int  # its colour


@dataclass(frozen=True, slots=True)
class Reach:
    """A bar from zero, which is in the middle: to the right above it, to the left below."""

    x: Decimal  # where it starts, in hundredths of the box
    width: Decimal


@dataclass(frozen=True, slots=True)
class Mark:
    """A bill on the line of days: where it stands and how tall it is."""

    x: Decimal  # its middle, in the units of the line
    height: Decimal
    dated: bool = True  # its day is written under it: no other is too near, nor "Today"
    priced: bool = True  # its amount is written over it: no other is too near


def _tenth(number: Decimal) -> Decimal:
    return number.quantize(_TENTH)


def _worst(areas: Sequence[Decimal], side: Decimal) -> Decimal:
    """How far from square the worst of `areas` is, laid in a row along `side`."""
    total = sum(areas, Decimal(0))
    return max(
        side * side * max(areas) / (total * total), total * total / (side * side * min(areas))
    )


def squarify(values: Sequence[Decimal], width: Decimal, height: Decimal) -> list[Rect]:
    """`values`, largest first, as rectangles that fill `width` by `height`, each of an area
    in proportion to its value and as near a square as the order allows (Bruls, Huizing and
    van Wijk). What is not above zero has no area."""
    total = sum((value for value in values if value > 0), Decimal(0))
    rects = [Rect(Decimal(0), Decimal(0), Decimal(0), Decimal(0))] * len(values)
    if total <= 0:
        return rects
    areas = [value * width * height / total for value in values]
    left = [n for n, area in enumerate(areas) if area > 0]
    x, y, w, h = Decimal(0), Decimal(0), width, height
    while left:
        side = min(w, h)
        row = [left.pop(0)]
        while left and _worst([areas[n] for n in [*row, left[0]]], side) <= _worst(
            [areas[n] for n in row], side
        ):
            row.append(left.pop(0))
        laid = sum((areas[n] for n in row), Decimal(0))
        if w >= h:  # a column along the left
            thick = laid / h
            at = y
            for n in row:
                long = areas[n] / thick
                rects[n] = Rect(_tenth(x), _tenth(at), _tenth(thick), _tenth(long))
                at += long
            x, w = x + thick, w - thick
        else:  # a row along the top
            thick = laid / w
            at = x
            for n in row:
                long = areas[n] / thick
                rects[n] = Rect(_tenth(at), _tenth(y), _tenth(long), _tenth(thick))
                at += long
            y, h = y + thick, h - thick
    return rects


def pieces(
    values: Mapping[str, Decimal], slots: Mapping[str, int], last: str | None = None
) -> list[Piece]:
    """`values` above zero side by side in one bar, largest first (`last` after them all),
    each as wide as its share."""
    above = sorted(
        ((n, v) for n, v in values.items() if v > 0), key=lambda item: (item[0] == last, -item[1])
    )
    total = sum((value for _, value in above), Decimal(0))
    if not above:
        return []
    shares = portfolio.shared_out([value * 100 / total for _, value in above])
    cut = []
    start = Decimal(0)
    for (name, value), share in zip(above, shares, strict=True):
        cut.append(Piece(name, value, share, start, slots[name]))
        start += share
    return cut


def reaches(values: Sequence[Decimal | None]) -> list[Reach | None]:
    """Each of `values` a bar from the middle, the largest from it to an edge; none for a
    value not known."""
    most = max((abs(value) for value in values if value is not None), default=Decimal(0))
    found: list[Reach | None] = []
    for value in values:
        if value is None:
            found.append(None)
            continue
        width = _tenth(abs(value) * 50 / most) if most else Decimal(0)
        found.append(Reach(Decimal(50) if value >= 0 else 50 - width, width))
    return found


def line_of_days(
    maturities: Sequence[date | None],
    values: Sequence[Decimal],
    on: date,
    width: Decimal,
    height: Decimal,
) -> tuple[list[Mark | None], date | None]:
    """Each bill where it matures on a line from `on` (at 0) to the last maturity (at
    `width`), as tall as its value is of the largest's (`height`); a bill that has matured
    stands at 0, and one without a maturity has no place. The last day, None when no bill
    has a maturity."""
    days = [None if day is None else max((day - on).days, 0) for day in maturities]
    last = max((d for d in days if d is not None), default=None)
    most = max(values, default=Decimal(0))
    marks: list[Mark | None] = []
    for day, value in zip(days, values, strict=True):
        if day is None:
            marks.append(None)
            continue
        x = width * day / last if last else Decimal(0)
        tall = height * max(value, Decimal(0)) / most if most > 0 else Decimal(0)
        marks.append(Mark(_tenth(x), _tenth(tall)))
    end = None if last is None else date.fromordinal(on.toordinal() + last)
    return marks, end


def spaced(marks: Sequence[Mark | None], apart: Decimal) -> list[Mark | None]:
    """`marks` with a day written only `apart` from the last day written and from the start,
    where "Today" is, and an amount only `apart` from the last amount written."""
    found = list(marks)
    dated, priced = Decimal(0), -apart  # where the last day and amount are written
    for n, mark in sorted(enumerate(marks), key=lambda item: item[1].x if item[1] else 0):
        if mark is None:
            continue
        found[n] = replace(mark, dated=mark.x - dated >= apart, priced=mark.x - priced >= apart)
        dated = mark.x if mark.x - dated >= apart else dated
        priced = mark.x if mark.x - priced >= apart else priced
    return found
