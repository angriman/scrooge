"""Rows that come back at a steady pace, and what rows add up to by category."""

from datetime import date
from decimal import Decimal

from scrooge import categories, query
from scrooge.models import Kind
from scrooge.query import CategorySum, Row
from scrooge.storage import store

D = Decimal


def _row(  # a row is many fields; the tests vary these
    n: int,
    on: date,
    amount: str,
    counterparty: str = "Landlord AG",
    *,
    kind: Kind = Kind.EXPENSE,
    category: str | None = categories.RENT,
    parent: str | None = categories.GROUP_HOME,
    currency: str = "CHF",
    account: str = "Migros",
) -> Row:
    return Row(
        str(n),
        "Anna",
        account,
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


def test_a_shop_some_of_whose_purchases_cost_about_the_same_is_not_monthly() -> None:
    # The purchases near the middle amount are a month apart in the middle of their gaps
    # (5, 51, 21, 30, 43 days), not one after the other.
    days = [date(2026, 1, 1), date(2026, 1, 6), date(2026, 2, 26), date(2026, 3, 19)]
    days += [date(2026, 4, 18), date(2026, 5, 31)]
    shop = [_row(n, on, "-30", "Shop") for n, on in enumerate(days, 1)]
    other = zip(range(10, 14), ("-80", "-12", "-95", "-9"), strict=True)
    shop += [_row(n, date(2026, 2, n), amount, "Shop") for n, amount in other]
    assert query.recurring(shop) == {}


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
        _row(2, date(2026, 9, 2), "-20", "Coop", category=categories.GROCERIES, parent=None),
        _row(3, date(2026, 9, 3), "-30", "Coop", category=categories.GROCERIES, parent=None),
        _row(
            4,
            date(2026, 9, 4),
            "-10",
            "Coop",
            category=categories.GROCERIES,
            parent=None,
            currency="EUR",
        ),
        _row(5, date(2026, 9, 5), "200", "Savings", kind=Kind.INTERNAL, category=None, parent=None),
    ]
    assert query.by_category(rows, store.Ranks({}, {})) == (
        CategorySum(categories.GROUP_HOME, "CHF", 1, D("-1850")),
        CategorySum(query.TRANSFERS, "CHF", 1, D("200")),
        CategorySum(categories.GROCERIES, "CHF", 2, D("-50")),
        CategorySum(categories.GROCERIES, "EUR", 1, D("-10")),
    )


def test_the_groups_say_when_the_next_payment_is_due_and_whether_it_stopped() -> None:
    days = ((1, date(2026, 1, 31)), (2, date(2026, 2, 28)), (3, date(2026, 3, 31)))
    rent = [_row(n, on, "-1850") for n, on in days]
    insurance = [
        _row(4, date(2025, 2, 28), "-600", "Insurer, Zurich 8000"),
        _row(5, date(2026, 2, 28), "-660", "Insurer, Zurich 8000"),
    ]
    late = _row(6, date(2026, 9, 1), "-5", "Kiosk")
    rent_group, insurer = query.recurrences([*rent, *insurance, late])
    assert (rent_group.name, rent_group.next, rent_group.ended) == (
        "Landlord AG",
        date(2026, 4, 30),
        True,
    )
    assert (rent_group.first, rent_group.last, rent_group.previous) == (
        date(2026, 1, 31),
        date(2026, 3, 31),
        None,
    )
    assert (insurer.name, insurer.every, insurer.next, insurer.ended) == (
        "Insurer",
        "yearly",
        date(2027, 2, 28),
        False,
    )
    assert (insurer.previous, insurer.monthly) == (D(-600), D(-55))
    assert rent_group.monthly == D(-1850)


def test_a_counterparty_of_only_digits_is_named_by_its_account_as_the_cash_flow_names_it() -> None:
    days = ((1, 5), (2, 6), (3, 7))
    (group,) = query.recurrences([_row(n, date(2026, m, 1), "-9", "123") for n, m in days])
    assert group.name == "Migros" == query.merchant(Kind.EXPENSE, "Migros", "123")


def test_the_next_payment_falls_on_the_last_day_of_a_shorter_month() -> None:
    def next_after(every: str, *days: date) -> date:
        rows = tuple(_row(n, on, "-10") for n, on in enumerate(days, 1))
        return query.Recurrence("Gym", "Anna", "Migros", every, rows, days[-1]).next

    assert next_after("monthly", date(2025, 12, 31), date(2026, 1, 31)) == date(2026, 2, 28)
    assert next_after("monthly", date(2027, 12, 31), date(2028, 1, 31)) == date(2028, 2, 29)
    assert next_after("monthly", date(2028, 1, 29), date(2028, 2, 29)) == date(2028, 3, 29)
    assert next_after("yearly", date(2023, 2, 28), date(2024, 2, 29)) == date(2025, 2, 28)
    assert next_after("monthly", date(2026, 11, 30), date(2026, 12, 31)) == date(2027, 1, 31)


def test_an_account_whose_statements_stop_earlier_does_not_end_its_payments() -> None:
    # The card's statements stop in June; the bank account's go on to October.
    card = [
        _row(n, date(2026, m, 3), "-12", "Streamio", account="Card")
        for n, m in enumerate(range(3, 7), 1)
    ]
    bank = [_row(n, date(2026, m, 1), "-1850") for n, m in enumerate(range(3, 11), 10)]
    gym = [_row(n, date(2026, m, 5), "-80", "Gym") for n, m in enumerate(range(3, 7), 20)]
    found = {group.name: group for group in query.recurrences([*card, *bank, *gym])}
    assert (found["Streamio"].through, found["Streamio"].ended) == (date(2026, 6, 3), False)
    assert (found["Gym"].through, found["Gym"].ended) == (date(2026, 10, 1), True)
    assert found["Landlord AG"].ended is False


def test_two_groups_of_the_same_name_are_told_apart_by_their_first_row() -> None:
    # Two plans of one service, each paid on its own: the same merchant, two groups.
    one = [_row(n, date(2026, m, 3), "-12", "Streamio 1") for n, m in ((1, 5), (2, 6), (3, 7))]
    two = [_row(n, date(2026, m, 9), "-30", "Streamio 2") for n, m in ((4, 5), (5, 6), (6, 7))]
    first, second = query.recurrences([*one, *two])
    assert (first.name, first.id) == ("Streamio", "1")
    assert (second.name, second.id) == ("Streamio", "4")


def test_only_a_change_from_the_payment_before_the_latest_is_a_price_change() -> None:
    # The rise came a year ago: it is the price now, not a change.
    rent = [_row(n, date(2025, m, 1), "-1800") for n, m in enumerate(range(1, 10), 1)]
    rent += [_row(n, date(2026, m, 1), "-1850") for n, m in enumerate(range(1, 10), 20)]
    (group,) = query.recurrences(rent)
    assert group.previous is None
    (group,) = query.recurrences([*rent, _row(40, date(2026, 10, 1), "-1900")])
    assert group.previous == D(-1850)
