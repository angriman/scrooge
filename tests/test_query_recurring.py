"""Rows that come back at a steady pace, and what rows add up to by category."""

from datetime import date
from decimal import Decimal

from scrooge import query
from scrooge.models import Kind
from scrooge.query import CategorySum, Row

D = Decimal


def _row(  # a row is many fields; the tests vary these
    n: int,
    on: date,
    amount: str,
    counterparty: str = "Landlord AG",
    *,
    kind: Kind = Kind.EXPENSE,
    category: str | None = "Affitto",
    parent: str | None = "Casa",
    currency: str = "CHF",
) -> Row:
    return Row(
        str(n),
        "Anna",
        "Migros",
        on,
        on,
        D(amount),
        currency,
        counterparty,
        counterparty,
        kind,
        category,
        parent,
        None,
    )


def test_a_payment_each_month_of_about_the_same_amount_is_monthly() -> None:
    rent = [_row(n, date(2026, month, 1), "-1850") for n, month in enumerate(range(5, 9), 1)]
    # A rise of a tenth is the same payment; a one-off twice the size is not.
    rent.append(_row(10, date(2026, 9, 2), "-2035"))
    rent.append(_row(11, date(2026, 9, 15), "-4000"))
    found = query.recurring(rent)
    assert found == dict.fromkeys(["1", "2", "3", "4", "10"], "monthly")


def test_twice_a_year_apart_is_yearly_and_two_months_apart_is_nothing() -> None:
    insurance = [
        _row(1, date(2025, 3, 1), "-600", "Insurer"),
        _row(2, date(2026, 3, 3), "-610", "Insurer"),
    ]
    gym = [_row(3, date(2026, 1, 5), "-80", "Gym"), _row(4, date(2026, 3, 5), "-80", "Gym")]
    assert query.recurring([*insurance, *gym]) == {"1": "yearly", "2": "yearly"}


def test_only_money_in_and_out_comes_back() -> None:
    salary = [_row(n, date(2026, m, 25), "5000", "Employer") for n, m in ((1, 6), (2, 7), (3, 8))]
    moves = [
        _row(n, date(2026, m, 1), "-100", "Savings", kind=Kind.INTERNAL, category=None, parent=None)
        for n, m in ((4, 6), (5, 7), (6, 8))
    ]
    # A fund bought each year is a trade, not a payment.
    fund = [
        _row(n, date(y, 12, 20), "-7000", "CH0000000001", kind=Kind.SECURITY_TRADE)
        for n, y in ((7, 2024), (8, 2025))
    ]
    found = query.recurring([*salary, *moves, *fund])
    assert found == dict.fromkeys(["1", "2", "3"], "monthly")


def test_rows_add_up_by_top_level_category_and_currency_largest_first() -> None:
    rows = [
        _row(1, date(2026, 9, 1), "-1850"),
        _row(2, date(2026, 9, 2), "-20", "Coop", category="Spesa", parent=None),
        _row(3, date(2026, 9, 3), "-30", "Coop", category="Spesa", parent=None),
        _row(4, date(2026, 9, 4), "-10", "Coop", category="Spesa", parent=None, currency="EUR"),
        _row(5, date(2026, 9, 5), "200", "Savings", kind=Kind.INTERNAL, category=None, parent=None),
    ]
    assert query.by_category(rows) == (
        CategorySum("Casa", "CHF", 1, D("-1850")),
        CategorySum(query.TRANSFERS, "CHF", 1, D("200")),
        CategorySum("Spesa", "CHF", 2, D("-50")),
        CategorySum("Spesa", "EUR", 1, D("-10")),
    )
