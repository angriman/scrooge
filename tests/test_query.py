import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scrooge import query, staging
from scrooge.models import CashBalance, Kind
from scrooge.query import Filter
from scrooge.storage import db, store
from tests.storage.helpers import (
    OWNER,
    PARTNER,
    batch,
    certificate,
    certified,
    close,
    statement,
    txn,
)

NOW = datetime(2026, 12, 20, 12, 0, tzinfo=UTC)
START = date(2025, 1, 1)
EUR = {"account": "Revolut EUR", "currency": "EUR", "source_file": "revolut.tsv"}
GIFT = "Anna Meier"  # the other person: recognised whatever the case
FOOD = {"category": "Ristoranti e bar", "counterparty": "Trattoria Ölberg"}

# Lukas, January 2025 to September 2026; September is staged and not committed.
LUKAS = [
    txn("-45.20", date=date(2025, 5, 3), source_ref="1"),
    txn("-80", date=date(2025, 5, 20), source_ref="2", description="CARD", **FOOD),
    txn("6000", date=date(2025, 6, 25), source_ref="3", kind=Kind.INCOME, category="Stipendio"),
    txn("6100", date=date(2026, 6, 25), source_ref="4", kind=Kind.INCOME, category="Stipendio"),
    # Bought on the last day of June, booked in July.
    txn("-15", date=date(2026, 6, 30), booking_date=date(2026, 7, 2), source_ref="5"),
    txn("-1000", date=date(2026, 7, 2), source_ref="6", kind=Kind.TRANSFER, category="Giroconto"),
    txn("-200", date=date(2026, 7, 10), source_ref="7", category="Regali", counterparty=GIFT),
    txn("-12", date=date(2026, 9, 10), source_ref="8", source_file="migros sep.csv"),
]
LUKAS_EUR = [
    txn("-30", date=date(2026, 7, 5), source_ref="1", description="Caffè Roma", **EUR, **FOOD),
]
ANNA = [
    txn(
        "200",
        date=date(2026, 7, 10),
        source_ref="1",
        kind=Kind.INCOME,
        category="Rimborsi",
        counterparty="LUKAS MEIER",
    ),
    txn("-60", date=date(2026, 7, 12), source_ref="2"),
]


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    lukas = (START, date(2026, 8, 31))
    committed = LUKAS[:-1]
    close(connection, OWNER, lukas, lukas[1], ("migros", committed), ("revolut", LUKAS_EUR))
    balance = sum((row.amount for row in committed), Decimal(0))
    september = statement(
        "migros",
        LUKAS[-1:],
        [CashBalance("Migros", "CHF", balance, balance + LUKAS[-1].amount)],
        month=date(2026, 9, 1),
    )
    staging.stage(connection, batch(september), NOW)
    july = (date(2026, 7, 1), date(2026, 7, 31))
    close(connection, PARTNER, july, july[1], ("migros", ANNA))
    yield connection
    connection.close()


def amounts(result: query.Result) -> list[str]:
    return [str(row.amount) for row in result.rows]


def sums(found: Sequence[query.Sum]) -> list[tuple[str, str, int, str]]:
    return [(s.period, s.currency, s.count, str(s.total)) for s in found]


def test_everything_committed_comes_back_oldest_first(conn: sqlite3.Connection) -> None:
    result = query.run(conn, Filter())
    days = [row.date for row in result.rows]
    assert days == sorted(days)
    assert len(result.rows) == len(LUKAS) + len(LUKAS_EUR) + len(ANNA) - 1
    assert date(2026, 9, 10) not in days  # September is staged
    assert sums(result.totals) == [("", "CHF", 9, "10899.8"), ("", "EUR", 1, "-30")]


def test_a_row_carries_what_the_page_shows(conn: sqlite3.Connection) -> None:
    (row,) = query.run(conn, Filter(text="anna")).rows
    assert (row.owner, row.account, row.currency) == (OWNER, "Migros", "CHF")
    assert (row.date, row.booking_date) == (date(2026, 7, 10), date(2026, 7, 10))
    assert (row.amount, row.kind) == (Decimal(-200), Kind.EXPENSE)
    assert (row.category, row.category_parent) == ("Regali", "Regali")
    assert (row.counterparty, row.description) == ("Anna Meier", "KONSUM")
    assert row.counterparty_owner == PARTNER
    assert row.id


def test_a_top_level_category_includes_its_leaves(conn: sqlite3.Connection) -> None:
    may = Filter(first=date(2025, 5, 1), last=date(2025, 5, 31), category="Ristoranti e bar")
    assert amounts(query.run(conn, may)) == ["-80"]
    assert amounts(query.run(conn, Filter(category="Ristoranti e bar"))) == ["-80", "-30"]


def test_a_category_over_years_is_summed_per_year_and_month(conn: sqlite3.Connection) -> None:
    result = query.run(conn, Filter(category="Stipendio"))
    assert sums(result.totals) == [("", "CHF", 2, "12100")]
    assert sums(result.by_year) == [("2025", "CHF", 1, "6000"), ("2026", "CHF", 1, "6100")]
    assert sums(result.by_month) == [("2025-06", "CHF", 1, "6000"), ("2026-06", "CHF", 1, "6100")]


def test_each_currency_has_its_own_total(conn: sqlite3.Connection) -> None:
    result = query.run(conn, Filter(category="Ristoranti e bar"))
    assert sums(result.totals) == [("", "CHF", 1, "-80"), ("", "EUR", 1, "-30")]


def test_the_range_is_on_the_real_date_unless_booking_is_asked(conn: sqlite3.Connection) -> None:
    july = Filter(first=date(2026, 7, 1), last=date(2026, 7, 2), owner=OWNER)
    assert amounts(query.run(conn, july)) == ["-1000"]
    booked = query.run(conn, replace(july, booking=True))
    assert sorted(amounts(booked)) == ["-1000", "-15"]  # the same day: in no set order
    assert sums(booked.by_month) == [("2026-07", "CHF", 2, "-1015")]
    real = query.run(conn, Filter(first=date(2026, 6, 30), last=date(2026, 6, 30)))
    assert sums(real.by_month) == [("2026-06", "CHF", 1, "-15")]


def test_both_ends_of_the_range_are_included(conn: sqlite3.Connection) -> None:
    result = query.run(conn, Filter(first=date(2025, 5, 3), last=date(2025, 5, 20)))
    assert amounts(result) == ["-45.2", "-80"]


def test_owner_account_and_kind_narrow_the_result(conn: sqlite3.Connection) -> None:
    assert amounts(query.run(conn, Filter(owner=PARTNER))) == ["200", "-60"]
    assert amounts(query.run(conn, Filter(account="Revolut EUR"))) == ["-30"]
    assert amounts(query.run(conn, Filter(owner=OWNER, account="Revolut EUR"))) == ["-30"]
    assert amounts(query.run(conn, Filter(kind=Kind.TRANSFER))) == ["-1000"]
    assert amounts(query.run(conn, Filter(kind=Kind.INCOME, owner=OWNER))) == ["6000", "6100"]
    both = Filter(kinds=(Kind.INCOME, Kind.TRANSFER), owner=OWNER)
    assert amounts(query.run(conn, both)) == ["6000", "6100", "-1000"]


def test_the_amount_range_is_on_the_size_whatever_the_sign(conn: sqlite3.Connection) -> None:
    result = query.run(conn, Filter(at_least=Decimal(200), at_most=Decimal(1000)))
    assert sorted(amounts(result)) == ["-1000", "-200", "200"]
    assert amounts(query.run(conn, Filter(at_most=Decimal(15)))) == ["-15"]


def test_text_is_found_in_counterparty_or_description_ignoring_case(
    conn: sqlite3.Connection,
) -> None:
    assert amounts(query.run(conn, Filter(text="ölberg"))) == ["-80", "-30"]
    assert amounts(query.run(conn, Filter(text="CAFFÈ"))) == ["-30"]
    assert query.run(conn, Filter(text="nowhere")) == query.Result((), (), (), ())


def test_a_merchant_is_who_was_paid_as_the_cash_flow_names_them(
    conn: sqlite3.Connection,
) -> None:
    assert amounts(query.run(conn, Filter(merchant="trattoria ÖLBERG"))) == ["-80", "-30"]
    assert amounts(query.run(conn, Filter(merchant=" Trattoria  Ölberg "))) == ["-80", "-30"]
    assert amounts(query.run(conn, Filter(merchant="Trattoria"))) == []  # the whole name
    # A row that names nobody is the account's, and only what was spent there is listed: not
    # the salary or the transfer.
    nobody = Filter(owner=OWNER, merchant="migros")
    assert amounts(query.run(conn, nobody)) == ["-45.2", "-15"]
    # What the two paid each other is not spent for both together.
    assert amounts(query.run(conn, Filter(owner=OWNER, merchant=GIFT))) == ["-200"]
    assert amounts(query.run(conn, Filter(merchant=GIFT))) == []


def test_a_merchant_is_named_without_numbers_places_or_addresses() -> None:
    written = "KONSUM-1234 ZUERICH, Bahnhofstrasse 1"
    assert query.merchant(Kind.EXPENSE, "Migros", written) == "KONSUM ZUERICH"
    assert query.merchant(Kind.EXPENSE, "Migros", " 42 ") == "Migros"
    assert query.merchant(Kind.COMMISSION, "IBKR USD", "/ESZ6") == "IBKR USD"


@pytest.mark.parametrize(
    ("wanted", "message"),
    [
        (Filter(owner="Lucas"), "no owner named 'Lucas'"),
        (Filter(account="Migross"), "no account named 'Migross'"),
        (Filter(owner=PARTNER, account="Revolut EUR"), "no account named 'Revolut EUR' of Anna"),
        (Filter(category="Stipendi"), "no category named 'Stipendi'"),
    ],
)
def test_a_name_the_database_does_not_have_is_refused(
    conn: sqlite3.Connection, wanted: Filter, message: str
) -> None:
    with pytest.raises(query.QueryError, match=message):
        query.run(conn, wanted)


@given(
    first=st.dates(START, date(2026, 9, 30)),
    days=st.integers(0, 700),
    booking=st.booleans(),
    owner=st.sampled_from([None, OWNER, PARTNER]),
)
def test_subtotals_add_up_to_the_totals_and_to_the_rows(
    conn: sqlite3.Connection, first: date, days: int, booking: bool, owner: str | None
) -> None:
    last = date.fromordinal(first.toordinal() + days)
    result = query.run(conn, Filter(first=first, last=last, booking=booking, owner=owner))

    def per_currency(found: Sequence[query.Sum]) -> dict[str, tuple[int, Decimal]]:
        added: dict[str, tuple[int, Decimal]] = {}
        for item in found:
            count, total = added.get(item.currency, (0, Decimal(0)))
            added[item.currency] = (count + item.count, total + item.total)
        return added

    from_rows = per_currency([query.Sum("", r.currency, 1, r.amount) for r in result.rows])
    assert per_currency(result.totals) == from_rows
    assert per_currency(result.by_year) == from_rows
    assert per_currency(result.by_month) == from_rows


def test_people_are_listed_with_what_is_committed_and_what_is_staged(
    conn: sqlite3.Connection,
) -> None:
    anna, lukas = query.people(conn)
    july = date(2026, 7, 1)
    assert anna == query.Person(PARTNER, 1, july, date(2026, 7, 31), staged=0, transactions=2)
    assert lukas == query.Person(OWNER, 2, START, date(2026, 8, 31), staged=1, transactions=8)


def test_a_certificate_is_a_statement_but_covers_no_days_of_the_person() -> None:
    conn = db.connect(":memory:")
    db.migrate(conn, NOW)
    end = date(2025, 12, 31)
    close(conn, OWNER, (START, end), end, ("migros", [txn("-45.20", date=date(2025, 5, 3))]))
    scans = certified(certificate(date(2026, 1, 1), "9400"), certificate(date(2023, 1, 1), "8000"))
    staging.stage(conn, scans, NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    anna, lukas = query.people(conn)
    assert anna.committed == 0
    assert (lukas.committed, lukas.first, lukas.last) == (3, START, end)
    conn.close()


def test_a_person_with_certificates_alone_has_no_days_covered() -> None:
    conn = db.connect(":memory:")
    db.migrate(conn, NOW)
    staging.stage(conn, certified(certificate(date(2026, 1, 1), "9400")), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    _, lukas = query.people(conn)
    assert lukas == query.Person(OWNER, 1, None, None, staged=0, transactions=0)
    conn.close()


def test_a_person_with_nothing_committed_is_listed_too() -> None:
    empty = db.connect(":memory:")
    db.migrate(empty, NOW)
    store.ensure_owner(empty, "Zoe")
    assert query.people(empty) == (query.Person("Zoe", 0, None, None, staged=0, transactions=0),)
    empty.close()


def test_the_choices_are_the_accounts_in_view_and_the_whole_taxonomy(
    conn: sqlite3.Connection,
) -> None:
    assert query.choices(conn).accounts == ("Migros", "Revolut EUR")
    assert query.choices(conn, PARTNER).accounts == ("Migros",)
    groups = dict(query.choices(conn).categories)
    assert groups["Casa"] == ("Casa", "Affitto", "Utenze", "Arredamento e casa")
    assert groups["Regali"] == ("Regali", "Donazioni")  # the leaf named like its parent: once
    assert groups["Entrate"][0] == "Stipendio"
    assert "Giroconto" in groups["Transfer"]
    leaves = dict(query.choices(conn).leaves)
    assert leaves["Casa"] == ("Affitto", "Utenze", "Arredamento e casa")
    assert leaves["Regali"] == ("Regali", "Donazioni")
    assert leaves["Entrate"] == groups["Entrate"]
    assert next(iter(groups)) == "Casa"
    assert [*groups][-2:] == ["Entrate", "Transfer"]


@pytest.mark.parametrize("text", ["May", "20266", "2026-13", "2026-02-30", ""])
def test_a_span_must_be_a_day_a_month_or_a_year(text: str) -> None:
    with pytest.raises(ValueError, match=r"month|isoformat|invalid literal|day is out"):
        query.span(text)
