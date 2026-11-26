import calendar
from datetime import date
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from scrooge import analysis, categories, staging
from scrooge.analysis import Converted, Rows, Span
from scrooge.cashflow import Counted, Grouping
from scrooge.models import Kind
from scrooge.storage import db
from tests.storage.helpers import CLOSED_AT, OWNER, PARTNER, certificate, certified, close, txn

days = st.dates(min_value=date(1990, 1, 1), max_value=date(2100, 12, 31))


def last_day(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def test_a_range_ending_on_the_last_day_of_february_compares_with_the_whole_earlier_february() -> (
    None
):
    assert analysis.year_before(date(2025, 2, 28)) == date(2024, 2, 29)


def test_the_last_day_of_a_leap_february_maps_to_the_last_day_of_the_earlier_one() -> None:
    assert analysis.year_before(date(2024, 2, 29)) == date(2023, 2, 28)


def test_the_last_day_of_february_before_a_leap_year_is_the_last_of_the_leap_one() -> None:
    assert analysis.year_before(date(2021, 2, 28)) == date(2020, 2, 29)


def test_a_day_of_february_that_is_not_the_last_stays_the_same_day() -> None:
    assert analysis.year_before(date(2024, 2, 28)) == date(2023, 2, 28)
    assert analysis.year_before(date(2025, 2, 27)) == date(2024, 2, 27)


def test_ordinary_days_are_the_same_day_a_year_earlier() -> None:
    assert analysis.year_before(date(2025, 3, 1)) == date(2024, 3, 1)
    assert analysis.year_before(date(2026, 8, 31)) == date(2025, 8, 31)
    assert analysis.year_before(date(2024, 12, 31)) == date(2023, 12, 31)


@given(days)
def test_the_year_before_is_in_the_same_month_a_year_earlier(day: date) -> None:
    before = analysis.year_before(day)
    assert (before.year, before.month) == (day.year - 1, day.month)


@given(days)
def test_the_last_day_of_a_month_maps_to_the_last_day_of_that_month_a_year_earlier(
    day: date,
) -> None:
    end = last_day(day)
    assert analysis.year_before(end) == last_day(end.replace(day=1, year=end.year - 1))


@given(days)
def test_a_day_that_is_not_the_last_of_its_month_keeps_its_number(day: date) -> None:
    if day != last_day(day):
        assert analysis.year_before(day).day == day.day


def test_add_months_moves_forward_and_back_to_the_first_of_a_month() -> None:
    assert analysis.add_months(date(2026, 8, 17), 1) == date(2026, 9, 1)
    assert analysis.add_months(date(2026, 8, 17), 5) == date(2027, 1, 1)
    assert analysis.add_months(date(2026, 1, 31), -1) == date(2025, 12, 1)
    assert analysis.add_months(date(2026, 8, 17), -12) == date(2025, 8, 1)
    assert analysis.add_months(date(2026, 8, 17), 0) == date(2026, 8, 1)


@given(days, st.integers(min_value=-600, max_value=600))
def test_adding_months_and_taking_them_back_gives_the_first_of_the_month(day: date, n: int) -> None:
    moved = analysis.add_months(day, n)
    assert moved.day == 1
    assert analysis.add_months(moved, -n) == day.replace(day=1)


def test_periods_name_each_period_from_the_one_of_the_start_to_the_one_of_the_end() -> None:
    first, last = date(2025, 11, 20), date(2026, 2, 3)
    assert analysis.periods(first, last, Grouping.MONTH) == [
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
    ]
    assert analysis.periods(first, last, Grouping.QUARTER) == ["2025-Q4", "2026-Q1"]
    assert analysis.periods(first, last, Grouping.YEAR) == ["2025", "2026"]


def test_periods_of_weeks_follow_the_iso_year() -> None:
    assert analysis.periods(date(2024, 12, 29), date(2025, 1, 6), Grouping.WEEK) == [
        "2024-W52",
        "2025-W01",
        "2025-W02",
    ]


def test_a_single_day_is_one_period() -> None:
    assert analysis.periods(date(2026, 5, 5), date(2026, 5, 5), Grouping.MONTH) == ["2026-05"]


SPAN = Span(date(2025, 1, 1), date(2026, 8, 31), through=date(2026, 8, 31), known=date(2025, 1, 1))


def test_the_window_of_a_period_is_the_twelve_months_that_end_with_it() -> None:
    assert analysis.window(SPAN, (date(2026, 6, 1), date(2026, 6, 30))) == (
        date(2025, 7, 1),
        date(2026, 6, 30),
    )


def test_the_window_of_a_period_ending_with_february_starts_the_first_of_march() -> None:
    assert analysis.window(SPAN, (date(2025, 2, 1), date(2025, 2, 28))) is None
    wide = Span(date(2020, 1, 1), date(2026, 8, 31), date(2026, 8, 31), date(2020, 1, 1))
    assert analysis.window(wide, (date(2025, 2, 1), date(2025, 2, 28))) == (
        date(2024, 3, 1),
        date(2025, 2, 28),
    )


def test_the_window_of_a_period_beyond_the_last_day_committed_ends_there() -> None:
    assert analysis.window(SPAN, (date(2026, 8, 1), date(2026, 9, 30))) == (
        date(2025, 9, 1),
        date(2026, 8, 31),
    )


def test_there_is_no_window_before_every_month_is_known() -> None:
    assert analysis.window(SPAN, (date(2025, 6, 1), date(2025, 6, 30))) is None


def test_there_is_no_window_for_a_period_after_the_last_day_committed() -> None:
    assert analysis.window(SPAN, (date(2026, 9, 1), date(2026, 9, 30))) is None


@given(days)
def test_the_window_of_a_whole_month_starts_on_the_first_of_a_month(day: date) -> None:
    end = last_day(day)
    span = Span(date(1980, 1, 1), end, end, date(1980, 1, 1))
    window = analysis.window(span, (end.replace(day=1), end))
    assert window == (analysis.add_months(end, -11), end)


def converted(day: date, amount: str = "10") -> Converted:
    row = Counted(
        day,
        "Lukas",
        "Migros",
        Kind.EXPENSE,
        "CHF",
        "expenses",
        categories.GROCERIES,
        categories.GROCERIES,
        0,
        "",
        None,
        "1",
    )
    return Converted(row, Decimal(amount))


ROWS = (converted(date(2026, 3, 5)), converted(date(2026, 4, 1)), converted(date(2026, 4, 30)))
FOUND = Rows(ROWS, tuple(each.day for each in ROWS), date(2026, 6, 15), date(2026, 3, 1))


def test_between_includes_both_ends() -> None:
    assert [each.day for each in FOUND.between(date(2026, 4, 1), date(2026, 4, 30))] == [
        date(2026, 4, 1),
        date(2026, 4, 30),
    ]
    assert FOUND.between(date(2026, 5, 1), date(2026, 5, 31)) == ()


def test_a_span_without_ends_runs_from_the_first_row_to_the_last_day_committed() -> None:
    assert FOUND.span(None, None) == Span(
        date(2026, 3, 5), date(2026, 6, 15), date(2026, 6, 15), date(2026, 3, 1)
    )


def test_a_span_keeps_the_ends_asked_for() -> None:
    span = FOUND.span(date(2026, 4, 1), date(2026, 4, 30))
    assert span is not None
    assert (span.start, span.end) == (date(2026, 4, 1), date(2026, 4, 30))


def test_a_span_of_one_day_is_a_span() -> None:
    assert FOUND.span(date(2026, 4, 1), date(2026, 4, 1)) is not None


def test_a_range_that_ends_before_it_starts_has_no_span() -> None:
    assert FOUND.span(date(2026, 4, 2), date(2026, 4, 1)) is None


def test_nothing_committed_means_no_span() -> None:
    assert Rows((), (), None, None).span(None, None) is None


def test_a_share_is_a_part_of_the_total_not_rounded() -> None:
    # Rounded where it is shown, once: 48.49% is 48%, never 48.5% and then 49%.
    assert analysis.share(Decimal("48.49"), Decimal(100)) == Decimal("0.4849")
    assert analysis.share(Decimal(1), Decimal(3)) == Decimal(1) / Decimal(3)
    assert analysis.share(Decimal(30), Decimal(120)) == Decimal("0.25")


def test_a_share_of_nothing_is_unknown() -> None:
    assert analysis.share(Decimal(5), Decimal(0)) is None


def test_a_share_of_a_negative_total_is_unknown() -> None:
    assert analysis.share(Decimal(5), Decimal(-10)) is None


def test_nothing_out_of_a_total_is_a_share_of_zero() -> None:
    assert analysis.share(Decimal(0), Decimal(10)) == 0


def test_a_negative_part_is_a_negative_share() -> None:
    assert analysis.share(Decimal(-5), Decimal(10)) == Decimal("-0.5")


def test_the_range_a_year_before_has_both_ends_a_year_earlier() -> None:
    span = Span(date(2026, 3, 1), date(2026, 5, 31), date(2026, 6, 15), date(2025, 3, 1))
    assert span.year_before() == (date(2025, 3, 1), date(2025, 5, 31))


def test_the_range_a_year_before_is_unknown_when_it_starts_before_the_known_months() -> None:
    span = Span(date(2026, 3, 1), date(2026, 5, 31), date(2026, 6, 15), date(2025, 4, 1))
    assert span.year_before() is None


def test_the_range_a_year_before_may_start_on_the_first_known_day() -> None:
    known = Span(date(2026, 3, 1), date(2026, 3, 31), date(2026, 6, 15), date(2025, 3, 1))
    assert known.year_before() == (date(2025, 3, 1), date(2025, 3, 31))
    late = Span(date(2026, 3, 1), date(2026, 3, 31), date(2026, 6, 15), date(2025, 3, 2))
    assert late.year_before() is None


def test_the_rows_within_a_period_are_those_of_its_days() -> None:
    span = Span(date(2026, 3, 1), date(2026, 6, 15), date(2026, 6, 15), date(2026, 3, 1))
    april = FOUND.within(span, (date(2026, 4, 1), date(2026, 4, 30)))
    assert [each.day for each in april] == [date(2026, 4, 1), date(2026, 4, 30)]


def test_the_rows_within_a_period_cut_by_the_range_stop_at_its_ends() -> None:
    span = Span(date(2026, 4, 2), date(2026, 4, 29), date(2026, 6, 15), date(2026, 3, 1))
    assert FOUND.within(span, (date(2026, 4, 1), date(2026, 4, 30))) == ()
    wide = Span(date(2026, 3, 20), date(2026, 4, 15), date(2026, 6, 15), date(2026, 3, 1))
    inside = FOUND.within(wide, (date(2026, 3, 1), date(2026, 4, 30)))
    assert [each.day for each in inside] == [date(2026, 4, 1)]


def test_by_period_lists_each_period_with_its_whole_days_and_the_rows_in_the_range() -> None:
    span = Span(date(2026, 3, 10), date(2026, 4, 15), date(2026, 6, 15), date(2026, 3, 1))
    found = [
        (name, days, [each.day for each in inside])
        for name, days, inside in FOUND.by_period(span, Grouping.MONTH)
    ]
    assert found == [
        ("2026-03", (date(2026, 3, 1), date(2026, 3, 31)), []),  # the 5th is before the range
        ("2026-04", (date(2026, 4, 1), date(2026, 4, 30)), [date(2026, 4, 1)]),
    ]


def test_by_period_of_a_range_inside_one_period_is_that_period_alone() -> None:
    span = Span(date(2026, 4, 1), date(2026, 4, 1), date(2026, 6, 15), date(2026, 3, 1))
    [(name, days, inside)] = list(FOUND.by_period(span, Grouping.YEAR))
    assert (name, days) == ("2026", (date(2026, 1, 1), date(2026, 12, 31)))
    assert [each.day for each in inside] == [date(2026, 4, 1)]


def test_since_is_the_first_day_of_the_earliest_committed_statement_month() -> None:
    conn = db.connect(":memory:")
    db.migrate(conn, CLOSED_AT)
    assert analysis.since(conn, None) is None
    assert analysis.since(conn, OWNER) is None

    # A pension certificate alone does not start a statement range.
    staging.stage(conn, certified(certificate(date(2024, 1, 1), "9000"), owner=OWNER), CLOSED_AT)
    staging.commit(conn, owner=OWNER, now=CLOSED_AT)
    assert analysis.since(conn, OWNER) is None
    assert analysis.since(conn, None) is None

    # Mid-month statements anchor to the first of their month; household takes the earliest.
    close(
        conn,
        OWNER,
        (date(2026, 7, 6), date(2026, 8, 31)),
        date(2026, 8, 31),
        ("migros", [txn("-50", date=date(2026, 7, 10), source_ref="1")]),
    )
    close(
        conn,
        PARTNER,
        (date(2026, 8, 4), date(2026, 8, 31)),
        date(2026, 8, 31),
        ("migros", [txn("-40", date=date(2026, 8, 10), source_ref="1")]),
    )
    assert analysis.since(conn, OWNER) == date(2026, 7, 1)
    assert analysis.since(conn, PARTNER) == date(2026, 8, 1)
    assert analysis.since(conn, None) == date(2026, 7, 1)
    conn.close()
