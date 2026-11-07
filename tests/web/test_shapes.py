"""The geometry of the holdings page's shapes."""

from datetime import date
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from finanze.web import shapes
from finanze.web.shapes import Mark, Piece, Reach, Rect

D = Decimal


def test_the_map_of_the_paper_lays_its_rows_as_the_paper_does() -> None:
    # Bruls, Huizing and van Wijk's example: 6, 6, 4, 3, 2, 2, 1 in a box of 6 by 4.
    rects = shapes.squarify([D(n) for n in (6, 6, 4, 3, 2, 2, 1)], D(6), D(4))
    assert rects[:2] == [Rect(D(0), D(0), D(3), D(2)), Rect(D(0), D(2), D(3), D(2))]
    assert rects[2] == Rect(D(3), D(0), D("1.7"), D("2.3"))
    assert sum(r.width * r.height for r in rects) == 24  # the box, filled


def test_what_is_not_above_zero_has_no_place_on_the_map() -> None:
    nothing = Rect(D(0), D(0), D(0), D(0))
    assert shapes.squarify([], D(10), D(10)) == []
    assert shapes.squarify([D(0), D(-3)], D(10), D(10)) == [nothing, nothing]
    [whole, none] = shapes.squarify([D(5), D(0)], D(10), D(4))
    assert (whole, none) == (Rect(D(0), D(0), D(10), D(4)), nothing)


@given(st.lists(st.integers(1, 10_000), min_size=1, max_size=30))
def test_the_map_is_filled_by_rectangles_that_do_not_overlap(values: list[int]) -> None:
    ordered = [D(v) for v in sorted(values, reverse=True)]
    width, height = D(1000), D(500)
    rects = shapes.squarify(ordered, width, height)
    total = sum(ordered)
    slack = D("0.2")  # each side is to a tenth
    for rect, value in zip(rects, ordered, strict=True):
        assert min(rect.x, rect.y) >= 0
        assert rect.x + rect.width <= width + slack
        assert rect.y + rect.height <= height + slack
        exact = value * width * height / total
        assert abs(rect.width * rect.height - exact) <= (rect.width + rect.height) * slack + slack
    for n, a in enumerate(rects):
        for b in rects[n + 1 :]:
            across = min(a.x + a.width, b.x + b.width) - max(a.x, b.x)
            down = min(a.y + a.height, b.y + b.height) - max(a.y, b.y)
            assert across <= slack or down <= slack


def test_a_bar_is_cut_in_shares_largest_first() -> None:
    cut = shapes.pieces(
        {"CHF": D(100), "USD": D(300), "EUR": D(0), "GBP": D(-5)}, {"CHF": 2, "USD": 1}
    )
    assert cut == [Piece("USD", D(300), D(75), D(0), 1), Piece("CHF", D(100), D(25), D(75), 2)]
    thirds = shapes.pieces({"a": D(1), "b": D(1), "c": D(1)}, {"a": 1, "b": 2, "c": 3})
    assert [p.share for p in thirds] == [D("33.34"), D("33.33"), D("33.33")]
    assert shapes.pieces({"a": D(0)}, {"a": 1}) == []


def test_a_gain_goes_right_of_the_middle_and_a_loss_left() -> None:
    assert shapes.reaches([D(20), D(-10), None, D(0)]) == [
        Reach(D(50), D(50)),
        Reach(D(25), D(25)),
        None,
        Reach(D(50), D(0)),
    ]
    assert shapes.reaches([D(0), None]) == [Reach(D(50), D(0)), None]


def test_bills_stand_on_the_line_of_days_to_their_maturity() -> None:
    on = date(2026, 11, 7)
    maturities = [date(2026, 11, 1), date(2026, 12, 7), None, date(2027, 1, 6)]
    values = [D(50), D(100), D(70), D(25)]
    marks, last = shapes.line_of_days(maturities, values, on, D(600), D(80))
    assert marks == [Mark(D(0), D(40)), Mark(D(300), D(80)), None, Mark(D(600), D(20))]
    assert last == date(2027, 1, 6)
    assert shapes.line_of_days([None], [D(1)], on, D(600), D(80)) == ([None], None)
    # all of them matured, or worth nothing: at the start, flat
    assert shapes.line_of_days([date(2026, 1, 1)], [D(0)], on, D(600), D(80)) == (
        [Mark(D(0), D(0))],
        on,
    )


def test_what_is_written_on_the_line_of_days_is_never_too_near() -> None:
    on = date(2026, 11, 7)
    maturities = [date.fromordinal(on.toordinal() + 10 * n) for n in range(1, 7)]
    marks, _ = shapes.line_of_days([*maturities, None], [D(1)] * 7, on, D(60), D(10))
    # at 10, 20, ... 60; a day only 25 after "Today" and the last day written, an amount
    # only 25 after the last amount written
    assert [(m.x, m.dated, m.priced) for m in shapes.spaced(marks, D(25)) if m] == [
        (D(10), False, True),
        (D(20), False, False),
        (D(30), True, False),
        (D(40), False, True),
        (D(50), False, False),
        (D(60), True, False),
    ]


def test_the_axis_of_months_has_a_tick_each_and_names_a_month_only_where_there_is_room() -> None:
    first, last = date(2025, 11, 1), date(2026, 3, 31)  # 151 days
    ticks = shapes.months_axis(first, last, D(1000))
    assert [(t.label, t.year) for t in ticks] == [
        ("Nov", False),
        ("Dec", False),
        ("2026", True),
        ("Feb", False),
        ("Mar", False),
    ]
    assert [t.x for t in ticks] == [D("0.0"), D("198.7"), D("404.0"), D("609.3"), D("794.7")]
    narrow = shapes.months_axis(first, last, D(100))  # 20 units a month: no room for a name
    assert [t.label for t in narrow] == ["", "", "2026", "", ""]


def test_a_run_of_days_is_a_band_of_its_width_and_a_day_alone_is_still_seen() -> None:
    first, last = date(2026, 1, 1), date(2026, 1, 31)  # 31 days
    found = shapes.bands(
        [(date(2026, 1, 1), date(2026, 1, 10)), (date(2026, 1, 21), date(2026, 1, 21))],
        first,
        last,
        D(31),
    )
    assert found == [shapes.Band(D("0.0"), D("10.0")), shapes.Band(D("20.0"), D("3"))]
