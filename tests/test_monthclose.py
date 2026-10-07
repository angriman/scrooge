import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finanze import classification, monthclose, pipeline
from finanze.categories import Extra
from finanze.models import CashBalance, Kind
from finanze.monthclose import Batch
from finanze.storage import db, store
from finanze.storage.money import PrecisionError, from_nano
from finanze.validation import ChainMismatchError, MissingLotsError, MissingStatementError
from tests.conftest import INSTRUMENTS, write_rules
from tests.storage.helpers import (
    AUGUST,
    OCTOBER,
    OWNER,
    PARTNER,
    PEOPLE,
    SEPTEMBER,
    batch,
    bill,
    dump,
    migros,
    rows,
    statement,
    tastytrade,
    trade,
    txn,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 7, 9, 30, tzinfo=UTC)


def new_database() -> sqlite3.Connection:
    conn = db.connect(":memory:")
    db.migrate(conn, NOW)
    return conn


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = new_database()
    yield connection
    connection.close()


def month_of(month: date, opening: str, *amounts: str) -> Batch:
    """A month with just a Migros statement."""
    return batch(migros(opening, *amounts, month=month), month=month)


def fixture_batch(folder: Path, rules: Path | None = None) -> Batch:
    """September 2026 from the redacted fixtures, through the real pipeline."""
    statements, skipped = pipeline.load_statements(folder, database=True)
    month = pipeline.process(
        [(s.source, s.extraction) for s in statements],
        classification.load_rules([rules or write_rules(folder)]),
        INSTRUMENTS,
        skipped,
        database=True,
    )
    return Batch(OWNER, statements, month, PEOPLE)


def stage_september(
    conn: sqlite3.Connection, folder: Path, rules: Path | None = None, now: datetime = NOW
) -> monthclose.Staged:
    """The fixture month, after the August purchase of the bill that matures in it."""
    bought = bill("-9920", "10000", date(2026, 8, 20), price=Decimal("99.2"))
    monthclose.stage(conn, tastytrade(bought, month=AUGUST), now)
    return monthclose.stage(conn, fixture_batch(folder, rules), now)


FIRST = "2026-09-01"  # for queries that look at September only

# ------------------------------------------------------------------ the fixture month


def test_every_transaction_of_the_month_is_stored(conn: sqlite3.Connection, folder: Path) -> None:
    september = fixture_batch(folder)
    staged = stage_september(conn, folder)
    assert staged == monthclose.Staged(SEPTEMBER, len(september.month.transactions), 0)
    # one row more than the statements have: the interest split off the matured bill
    stored_rows = rows(conn, "SELECT count(*) FROM transactions WHERE booking_date >= ?", FIRST)
    assert stored_rows == [(staged.transactions + 1,)]

    expected: dict[tuple[str, str], Decimal] = {}
    for row in september.month.transactions:
        key = (row.account, row.currency)
        expected[key] = expected.get(key, Decimal(0)) + row.amount
    stored = rows(
        conn,
        "SELECT a.name, t.currency, sum(t.amount_nano) FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id WHERE t.booking_date >= ? GROUP BY 1, 2",
        FIRST,
    )
    assert {(name, currency): from_nano(total) for name, currency, total in stored} == expected


def test_both_dates_are_stored(conn: sqlite3.Connection, folder: Path) -> None:
    monthclose.stage(conn, fixture_batch(folder), NOW)
    card = rows(
        conn,
        "SELECT date, booking_date FROM transactions WHERE description LIKE 'ALPENBAHN%'",
    )
    assert card == [("2026-08-04", "2026-09-04")]  # bought in August, billed in September
    outside = rows(
        conn,
        "SELECT count(*) FROM transactions WHERE booking_date NOT BETWEEN ? AND ?",
        "2026-09-01",
        "2026-09-30",
    )
    assert outside == [(0,)]


def test_accounts_trades_and_statement_files_are_stored(
    conn: sqlite3.Connection, folder: Path
) -> None:
    monthclose.stage(conn, fixture_batch(folder), NOW)
    accounts = dict(rows(conn, "SELECT name, kind || ':' || source FROM accounts"))
    assert accounts["Migros"] == "bank:migros"
    assert accounts["Swisscard"] == "card:swisscard"  # an account of its own
    assert accounts["IBKR USD"] == "broker_cash:ibkr"
    assert accounts["Tastytrade"] == "broker_cash:tastytrade"
    files = dict(rows(conn, "SELECT source, reconciled FROM statement_files"))
    assert files["migros"] == "yes"
    assert files["revolut_savings"] == "nothing_to_check"
    assert set(files) == {"ibkr", "migros", "revolut", "revolut_savings", "swisscard", "tastytrade"}
    # every buy and sell, plus the bill that matured at Tastytrade
    trades = rows(conn, "SELECT count(*) FROM trades")
    assert trades == [(len(fixture_batch(folder).month.trades) + 1,)]
    bills = rows(conn, "SELECT symbol, maturity FROM instruments WHERE type = 'bill' ORDER BY 1")
    assert ("912797ZZ1", "2026-12-24") in bills


def test_importing_the_same_month_again_changes_nothing(
    conn: sqlite3.Connection, folder: Path
) -> None:
    stage_september(conn, folder)
    before = dump(conn)
    stage_september(conn, folder, now=LATER)
    assert dump(conn) == before


def test_a_month_is_invisible_until_committed(conn: sqlite3.Connection, folder: Path) -> None:
    staged = stage_september(conn, folder)
    assert rows(conn, "SELECT count(*) FROM committed_transactions") == [(0,)]
    assert monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=LATER) is True
    visible = rows(conn, "SELECT count(*), min(booking_date) FROM committed_transactions")
    assert visible == [(staged.transactions + 1, "2026-09-01")]  # August is still open
    assert rows(conn, "SELECT status, committed_at FROM months ORDER BY month") == [
        ("open", None),
        ("committed", "2026-10-07T09:30:00+00:00"),
    ]
    sample = rows(
        conn,
        "SELECT owner, account, category, category_parent, category_side "
        "FROM committed_transactions WHERE description LIKE 'FRISCHMARKT%' LIMIT 1",
    )
    assert sample == [(OWNER, "Swisscard", "Spesa", "Alimentari", "uscite")]


def test_committing_again_is_a_no_op(conn: sqlite3.Connection, folder: Path) -> None:
    stage_september(conn, folder)
    monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)
    before = dump(conn)
    assert monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=LATER) is False
    assert dump(conn) == before


def test_a_committed_month_must_be_reopened_before_importing_again(
    conn: sqlite3.Connection, folder: Path
) -> None:
    stage_september(conn, folder)
    monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)
    before = dump(conn)
    with pytest.raises(monthclose.MonthCommittedError, match="2026-09 is committed for Lukas"):
        monthclose.stage(conn, fixture_batch(folder), LATER)
    assert dump(conn) == before

    monthclose.reopen(conn, owner=OWNER, month=SEPTEMBER)
    assert rows(conn, "SELECT DISTINCT status, committed_at FROM months") == [("open", None)]
    monthclose.stage(conn, fixture_batch(folder), LATER)
    monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)
    assert dump(conn) == before


def test_uncategorized_rows_block_the_commit(conn: sqlite3.Connection, folder: Path) -> None:
    rules = folder / "few.yaml"
    rules.write_text("rules:\n  - {match: 'Hans Muster', category: Affitto}\n", encoding="utf-8")
    staged = stage_september(conn, folder, rules)
    assert staged.uncategorized > 0
    with pytest.raises(monthclose.UncategorizedRowsError, match=f"{staged.uncategorized} trans"):
        monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)
    assert rows(conn, "SELECT DISTINCT status FROM months") == [("open",)]


# ------------------------------------------------------------------ months by hand


def test_internal_rows_need_no_category(conn: sqlite3.Connection) -> None:
    bill = migros("1000", "-200", kind=Kind.INTERNAL, category="", description="Swisscard")
    assert monthclose.stage(conn, batch(bill), NOW).uncategorized == 0
    assert monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)


@pytest.mark.parametrize("owner", [OWNER, "Nobody"])
def test_a_month_that_was_never_staged_cannot_be_committed_or_reopened(
    conn: sqlite3.Connection, owner: str
) -> None:
    monthclose.stage(conn, batch(migros("0", "-1")), NOW)
    with pytest.raises(monthclose.NothingStagedError, match=f"for {owner} in 2026-10"):
        monthclose.commit(conn, owner=owner, month=OCTOBER, now=NOW)
    with pytest.raises(monthclose.NothingStagedError):
        monthclose.reopen(conn, owner=owner, month=OCTOBER)


def test_categories_follow_the_sign_and_fall_back_to_the_other_side(
    conn: sqlite3.Connection,
) -> None:
    amounts = {
        "expense": txn("-50", description="expense", category="Trading"),
        "income": txn("70", description="income", category="Trading"),
        "refund": txn("5", description="refund", category="Spesa"),
        "clawback": txn("-9", description="clawback", category="Stipendio"),
        "transfer": txn("-100", description="transfer", category="Topup Revolut"),
    }
    monthclose.stage(conn, batch(statement("migros", list(amounts.values()))), NOW)
    sides = dict(
        rows(
            conn,
            "SELECT t.description, c.side || '/' || c.name FROM transactions t "
            "JOIN categories c ON c.id = t.category_id",
        )
    )
    assert sides == {
        "expense": "uscite/Trading",
        "income": "entrate/Trading",
        "refund": "uscite/Spesa",
        "clawback": "entrate/Stipendio",
        "transfer": "transfer/Topup Revolut",
    }
    assert rows(conn, "SELECT DISTINCT category_source FROM transactions") == [("rule",)]


def test_an_unknown_category_stops_the_import(conn: sqlite3.Connection) -> None:
    with pytest.raises(store.UnknownCategoryError, match="'Unicorni'"):
        monthclose.stage(conn, batch(migros("0", "-1", category="Unicorni")), NOW)
    assert rows(conn, "SELECT count(*) FROM owners") == [(0,)]  # nothing was kept


def test_the_taxonomy_is_seeded_once(conn: sqlite3.Connection) -> None:
    monthclose.stage(conn, batch(migros("0", "-1")), NOW)
    before = rows(conn, "SELECT * FROM categories ORDER BY id")
    monthclose.stage(conn, batch(migros("0", "-1")), NOW)
    assert rows(conn, "SELECT * FROM categories ORDER BY id") == before
    tree = rows(
        conn,
        "SELECT c.name, p.name FROM categories c JOIN categories p ON p.id = c.parent_id "
        "WHERE c.name IN ('Regali', 'Voli') ORDER BY 1",
    )
    assert tree == [("Regali", "Regali"), ("Voli", "Trasporti")]


def test_added_categories_are_stored_with_the_taxonomy(conn: sqlite3.Connection) -> None:
    added = (
        Extra("Prestiti", "uscite", "Prestiti"),
        Extra("Giardino", "uscite", "Casa"),
        Extra("Prestiti", "entrate"),
    )
    lent = batch(migros("0", "-1", category="Prestiti"))
    monthclose.stage(conn, replace(lent, categories=added), NOW)
    tree = rows(
        conn,
        "SELECT c.name, c.side, p.name FROM categories c LEFT JOIN categories p "
        "ON p.id = c.parent_id WHERE c.name IN ('Prestiti', 'Giardino') ORDER BY c.id",
    )
    assert tree == [
        ("Giardino", "uscite", "Casa"),
        ("Prestiti", "uscite", None),
        ("Prestiti", "uscite", "Prestiti"),
        ("Prestiti", "entrate", None),
    ]
    used = "SELECT c.side, p.name FROM transactions t JOIN categories c ON c.id = t.category_id "
    assert rows(conn, used + "JOIN categories p ON p.id = c.parent_id") == [("uscite", "Prestiti")]


def test_money_between_the_two_of_us_is_marked(conn: sqlite3.Connection) -> None:
    lines = [
        txn("-40", description="TWINT Anna Meier", category="Ristoranti"),
        txn("25", description="Bonifico", counterparty="ANNA MEIER", category="Rimborsi"),
        txn("-12", description="KONSUM"),
        txn("-500", description="ANNA MEIER", kind=Kind.TRANSFER, category="Topup Revolut"),
        txn("-3", description="LUKAS MEIER"),  # the owner is not their own counterparty
    ]
    monthclose.stage(conn, batch(statement("migros", lines)), NOW)
    marked = rows(
        conn,
        "SELECT t.amount_nano, o.name FROM transactions t "
        "LEFT JOIN owners o ON o.id = t.counterparty_owner_id ORDER BY t.amount_nano",
    )
    assert marked == [
        (-500_000_000_000, None),
        (-40_000_000_000, PARTNER),
        (-12_000_000_000, None),
        (-3_000_000_000, None),
        (25_000_000_000, PARTNER),
    ]


def test_each_person_has_their_own_accounts_and_months(conn: sqlite3.Connection) -> None:
    monthclose.stage(conn, batch(migros("100", "-1")), NOW)
    monthclose.stage(conn, batch(migros("900", "-2"), owner=PARTNER), NOW)
    monthclose.commit(conn, owner=PARTNER, month=SEPTEMBER, now=NOW)
    months = "SELECT o.name, m.status FROM months m JOIN owners o ON o.id = m.owner_id"
    assert rows(conn, months) == [(OWNER, "open"), (PARTNER, "committed")]
    assert rows(conn, "SELECT owner, account, amount_nano FROM committed_transactions") == [
        (PARTNER, "Migros", -2_000_000_000)
    ]
    assert rows(conn, "SELECT count(*) FROM accounts WHERE name = 'Migros'") == [(2,)]


def test_trades_and_instruments(conn: sqlite3.Connection) -> None:
    buys = [
        trade("-2025", "WIDG", "20"),
        trade(
            "-9851",
            "912797ZZ1",
            "10000",
            price=Decimal("98.51"),
            asset_tab="Bonds",
            maturity=date(2026, 12, 24),
            source_ref="Trades:2",
        ),
    ]
    monthclose.stage(conn, batch(statement("ibkr", buys)), NOW)
    stored = rows(
        conn,
        "SELECT i.symbol, i.type, i.currency, i.maturity, t.quantity, t.price, t.cost_nano "
        "FROM trades t JOIN instruments i ON i.id = t.instrument_id ORDER BY 1",
    )
    assert stored == [
        ("912797ZZ1", "bill", "USD", "2026-12-24", "10000", "98.51", 9_851_000_000_000),
        ("WIDG", "etf", "USD", None, "20", "101.25", 2_025_000_000_000),
    ]
    monthclose.stage(conn, batch(statement("ibkr", [replace(buys[1], maturity=None)])), NOW)
    assert rows(conn, "SELECT maturity FROM instruments WHERE type = 'bill'") == [("2026-12-24",)]
    assert rows(conn, "SELECT count(*) FROM trades") == [(1,)]  # WIDG went with its transaction


def test_a_manual_category_survives_a_new_import(conn: sqlite3.Connection) -> None:
    monthclose.stage(conn, batch(migros("0", "-30", "-8")), NOW)
    (txn_id,) = rows(conn, "SELECT id FROM transactions WHERE amount_nano = -30000000000")[0]
    (flights,) = rows(conn, "SELECT id FROM categories WHERE name = 'Voli'")[0]
    conn.execute(
        "INSERT INTO overrides (transaction_id, category_id, created_at) VALUES (?, ?, 'x')",
        (txn_id, flights),
    )
    (other,) = rows(conn, "SELECT id FROM transactions WHERE amount_nano = -8000000000")[0]
    conn.execute(
        "INSERT INTO overrides (transaction_id, kind, created_at) VALUES (?, 'transfer', 'x')",
        (other,),
    )
    monthclose.stage(conn, batch(migros("0", "-30", "-8")), LATER)
    kept = rows(
        conn,
        "SELECT t.amount_nano, c.name, t.category_source, t.kind FROM transactions t "
        "JOIN categories c ON c.id = t.category_id ORDER BY 1",
    )
    assert kept == [
        (-30_000_000_000, "Voli", "manual", "expense"),
        (-8_000_000_000, "Spesa", "rule", "transfer"),
    ]


def test_a_re_exported_statement_replaces_the_old_one(conn: sqlite3.Connection) -> None:
    monthclose.stage(conn, batch(migros("0", "-30", "-8")), NOW)
    corrected = replace(migros("0", "-30", "-8", "-2"), sha256="another export")
    monthclose.stage(conn, batch(corrected), LATER)
    assert rows(conn, "SELECT sha256, imported_at FROM statement_files") == [
        ("another export", "2026-10-07T09:30:00+00:00")
    ]
    totals = rows(conn, "SELECT count(*), sum(amount_nano) FROM transactions")
    assert totals == [(3, -40_000_000_000)]
    assert rows(conn, "SELECT date, amount_nano FROM balances ORDER BY 1") == [
        ("2026-08-31", 0),
        ("2026-09-30", -40_000_000_000),
    ]


def test_a_file_that_still_has_rows_is_kept(conn: sqlite3.Connection) -> None:
    both = [
        txn("-1", account="Revolut CHF", source_file="all.tsv"),
        txn("-2", account="Revolut EUR", currency="EUR", source_file="all.tsv"),
    ]
    monthclose.stage(conn, batch(statement("revolut", both)), NOW)
    only_chf = [txn("-1", account="Revolut CHF", source_file="chf.tsv")]
    monthclose.stage(conn, batch(statement("revolut", only_chf)), LATER)
    assert rows(conn, "SELECT filename FROM statement_files ORDER BY 1") == [
        ("all.tsv",),
        ("chf.tsv",),
    ]
    assert rows(
        conn,
        "SELECT a.name, f.filename FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "JOIN statement_files f ON f.id = t.statement_file_id ORDER BY 1",
    ) == [("Revolut CHF", "chf.tsv"), ("Revolut EUR", "all.tsv")]


def test_two_statements_of_one_account_are_refused(conn: sqlite3.Connection) -> None:
    first = statement("revolut", [txn("-1", account="Revolut CHF", source_file="a.tsv")])
    second = statement("revolut", [txn("-1", account="Revolut CHF", source_file="b.tsv")])
    with pytest.raises(monthclose.DuplicateStatementError, match=r"a\.tsv and b\.tsv are both"):
        monthclose.stage(conn, batch(first, second), NOW)
    assert rows(conn, "SELECT count(*) FROM transactions") == [(0,)]


def test_one_name_cannot_be_two_accounts(conn: sqlite3.Connection) -> None:
    monthclose.stage(conn, batch(migros("0", "-1")), NOW)
    euros = statement("migros", [txn("-1", currency="EUR")])
    with pytest.raises(store.AccountMismatchError, match="'Migros' is in CHF"):
        monthclose.stage(conn, batch(euros), NOW)


def test_an_amount_that_does_not_fit_is_refused_and_nothing_is_kept(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(PrecisionError):
        monthclose.stage(conn, batch(statement("migros", [txn("-0.0000000001")])), NOW)
    assert rows(conn, "SELECT count(*) FROM months") == [(0,)]


# ------------------------------------------------------------------ the chain of balances


def test_statement_balances_are_stored_at_both_ends(conn: sqlite3.Connection) -> None:
    monthclose.stage(conn, batch(migros("1000", "-200", "50")), NOW)
    assert rows(conn, "SELECT date, amount_nano, source FROM balances ORDER BY 1") == [
        ("2026-08-31", 1_000_000_000_000, "statement"),
        ("2026-09-30", 850_000_000_000, "statement"),
    ]


def test_the_next_month_must_open_where_this_one_closed(conn: sqlite3.Connection) -> None:
    monthclose.stage(conn, batch(migros("1000", "-200")), NOW)
    before = dump(conn)
    wrong = month_of(OCTOBER, "790", "-90")
    with pytest.raises(ChainMismatchError) as caught:
        monthclose.stage(conn, wrong, NOW)
    assert (caught.value.expected, caught.value.actual) == (Decimal(700), Decimal(710))
    assert caught.value.span == (date(2026, 9, 30), date(2026, 10, 31))
    assert "Migros (CHF): balance 700 on 2026-10-31" in str(caught.value)
    assert dump(conn) == before

    monthclose.stage(conn, month_of(OCTOBER, "800", "-90"), NOW)
    assert rows(conn, "SELECT date, amount_nano FROM balances ORDER BY 1") == [
        ("2026-08-31", 1_000_000_000_000),
        ("2026-09-30", 800_000_000_000),
        ("2026-10-31", 710_000_000_000),
    ]


def test_an_earlier_month_imported_later_is_checked_too(conn: sqlite3.Connection) -> None:
    monthclose.stage(conn, month_of(OCTOBER, "800", "-90"), NOW)
    with pytest.raises(ChainMismatchError, match="2026-10-31"):
        monthclose.stage(conn, batch(migros("1000", "-150")), NOW)  # closes on 850, not 800
    monthclose.stage(conn, batch(migros("1000", "-200")), NOW)
    assert monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)


def test_a_month_without_a_statement_is_reported_and_blocks_the_commit(
    conn: sqlite3.Connection,
) -> None:
    november = date(2026, 11, 1)
    monthclose.stage(conn, batch(migros("1000", "-200")), NOW)
    staged = monthclose.stage(conn, month_of(november, "650", "-50"), NOW)
    assert staged.gaps == (
        "Migros (CHF): no statement covers the days after 2026-09-30 up to 2026-10-31, "
        "yet the balance went from 800 to 650",
    )
    with pytest.raises(MissingStatementError, match="no statement covers") as caught:
        monthclose.commit(conn, owner=OWNER, month=november, now=NOW)
    assert caught.value.span == (date(2026, 9, 30), date(2026, 10, 31))
    assert caught.value.balances == (Decimal(800), Decimal(650))
    assert rows(conn, "SELECT status FROM months ORDER BY month") == [("open",), ("open",)]

    filled = monthclose.stage(conn, month_of(OCTOBER, "800", "-150"), NOW)
    assert filled.gaps == ()
    assert monthclose.commit(conn, owner=OWNER, month=november, now=NOW)


def test_a_quiet_month_without_a_statement_is_no_gap(conn: sqlite3.Connection) -> None:
    november = date(2026, 11, 1)
    monthclose.stage(conn, batch(migros("1000", "-200")), NOW)
    staged = monthclose.stage(conn, month_of(november, "800", "-50"), NOW)
    assert staged.gaps == ()
    assert monthclose.commit(conn, owner=OWNER, month=november, now=NOW)


def test_a_commit_checks_the_chain_again(conn: sqlite3.Connection) -> None:
    monthclose.stage(conn, batch(migros("1000", "-200")), NOW)
    conn.execute("UPDATE transactions SET amount_nano = amount_nano - 1")
    with pytest.raises(ChainMismatchError):
        monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)
    assert rows(conn, "SELECT status FROM months") == [("open",)]


def test_rows_another_source_books_on_the_account_stay_out_of_its_balance(
    conn: sqlite3.Connection,
) -> None:
    # D8: savings interest is booked on the Revolut account, whose statement does not show it.
    euro = {"account": "Revolut EUR", "currency": "EUR"}
    current = statement(
        "revolut",
        [txn("-5", source_file="eur.tsv", **euro)],
        [CashBalance("Revolut EUR", "EUR", Decimal(20), Decimal(15))],
    )
    paid = {"source_file": "savings.tsv", "kind": Kind.INTEREST, "category": "Interessi"}
    interest = txn("0.08", **paid, **euro)
    monthclose.stage(conn, batch(current, statement("revolut_savings", [interest])), NOW)
    assert rows(conn, "SELECT sum(amount_nano) FROM transactions") == [(-4_920_000_000,)]
    assert monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)


def test_the_card_is_an_account_whose_invoices_chain(
    conn: sqlite3.Connection, folder: Path
) -> None:
    stage_september(conn, folder)
    card = rows(
        conn,
        "SELECT b.date, b.amount_nano FROM balances b JOIN accounts a ON a.id = b.account_id "
        "WHERE a.name = 'Swisscard' ORDER BY 1",
    )
    assert card == [("2026-08-31", -1_142_600_000_000), ("2026-09-30", -1_288_180_000_000)]
    legs = rows(
        conn,
        "SELECT a.name, t.date, t.amount_nano FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id "
        "WHERE t.kind = 'internal' AND a.name IN ('Migros', 'Swisscard') ORDER BY t.date",
    )
    assert legs == [
        ("Swisscard", "2026-08-26", 1_142_600_000_000),  # August's payment, as the invoice lists it
        ("Migros", "2026-09-08", -1_288_180_000_000),  # September's, as the bank debits it
    ]


def test_accounts_are_chained_one_by_one(conn: sqlite3.Connection) -> None:
    euro = statement(
        "revolut",
        [txn("-5", account="Revolut EUR", currency="EUR", source_file="eur.tsv")],
        [CashBalance("Revolut EUR", "EUR", Decimal(20), Decimal(15))],
    )
    monthclose.stage(conn, batch(migros("1000", "-200"), euro), NOW)
    assert rows(conn, "SELECT count(*) FROM balances") == [(4,)]


# ------------------------------------------------------------------ typed balances


def test_an_account_without_statements_gets_typed_balances(conn: sqlite3.Connection) -> None:
    def typed(day: date, amount: str, currency: str = "EUR") -> None:
        monthclose.type_balance(
            conn, owner=OWNER, account="Kasse", day=day, amount=Decimal(amount), currency=currency
        )

    typed(date(2023, 1, 31), "5000.10")
    typed(date(2023, 2, 28), "1200")
    typed(date(2023, 1, 31), "5000.20")  # corrected
    stored = rows(
        conn,
        "SELECT a.name, a.kind, a.source, b.date, b.amount_nano, b.source FROM balances b "
        "JOIN accounts a ON a.id = b.account_id ORDER BY b.date",
    )
    assert stored == [
        ("Kasse", "manual", None, "2023-01-31", 5_000_200_000_000, "manual"),
        ("Kasse", "manual", None, "2023-02-28", 1_200_000_000_000, "manual"),
    ]
    with pytest.raises(store.AccountMismatchError, match="'Kasse' is in EUR, not CHF"):
        typed(date(2023, 3, 31), "1", "CHF")
    # Typed balances are no statement: nothing is chained through them.
    monthclose.stage(conn, batch(migros("0", "-1")), NOW)
    assert monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)


def test_an_account_is_fed_by_statements_or_typed_never_both(conn: sqlite3.Connection) -> None:
    monthclose.stage(conn, batch(migros("0", "-1")), NOW)
    with pytest.raises(store.AccountMismatchError, match="both statements and typed balances"):
        monthclose.type_balance(
            conn,
            owner=OWNER,
            account="Migros",
            day=date(2026, 9, 30),
            amount=Decimal(5),
            currency="CHF",
        )
    assert rows(conn, "SELECT count(*) FROM balances WHERE source = 'manual'") == [(0,)]


# ------------------------------------------------------------------ several months at once

JULY = date(2026, 7, 1)
PENSION = Path(__file__).parent / "fixtures" / "finpension" / "transactions.tsv"


def pension_batch(folder: Path) -> Batch:
    """The pillar 3a fixture (July to September 2026)."""
    (folder / "pillar 3a.tsv").write_bytes(PENSION.read_bytes())
    statements, skipped = pipeline.load_statements(folder, database=True)
    extractions = [(s.source, s.extraction) for s in statements]
    month = pipeline.process(extractions, (), {}, skipped, database=True)
    return Batch(OWNER, statements, month, PEOPLE)


def test_a_statement_of_several_months_opens_each_of_them(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    staged = monthclose.stage(conn, pension_batch(tmp_path), NOW)
    assert (staged.month, staged.transactions, staged.uncategorized) == (JULY, 9, 0)
    assert rows(conn, "SELECT month, status FROM months ORDER BY 1") == [
        ("2026-07-01", "open"),
        ("2026-08-01", "open"),
        ("2026-09-01", "open"),
    ]
    assert rows(conn, "SELECT period_start, period_end FROM statement_files") == [
        ("2026-07-01", "2026-09-30")
    ]
    assert rows(conn, "SELECT date, amount_nano FROM balances ORDER BY 1") == [
        ("2026-06-30", 0),
        ("2026-09-30", 13_671_078_000),
    ]
    funds = rows(
        conn,
        "SELECT i.symbol, i.type, a.kind, sum(CAST(tr.quantity AS TEXT) <> '') "
        "FROM trades tr JOIN instruments i ON i.id = tr.instrument_id "
        "JOIN transactions t ON t.id = tr.transaction_id JOIN accounts a ON a.id = t.account_id "
        "GROUP BY 1 ORDER BY 1",
    )
    assert funds == [
        ("CH0000000011", "fund", "broker_cash", 2),
        ("CH0000000029", "fund", "broker_cash", 2),
    ]


def test_months_of_one_statement_are_committed_one_by_one(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    monthclose.stage(conn, pension_batch(tmp_path), NOW)
    assert monthclose.commit(conn, owner=OWNER, month=date(2026, 8, 1), now=NOW)
    visible = rows(conn, "SELECT DISTINCT substr(booking_date, 1, 7) FROM committed_transactions")
    assert visible == [("2026-08",)]

    before = dump(conn)
    with pytest.raises(monthclose.MonthCommittedError, match="2026-08 is committed"):
        monthclose.stage(conn, pension_batch(tmp_path), LATER)
    assert dump(conn) == before  # July was not left half reopened

    monthclose.reopen(conn, owner=OWNER, month=date(2026, 8, 1))
    monthclose.stage(conn, pension_batch(tmp_path), LATER)
    monthclose.commit(conn, owner=OWNER, month=date(2026, 8, 1), now=NOW)
    assert dump(conn) == before


def test_statements_of_one_account_for_different_periods_go_in_together(
    conn: sqlite3.Connection,
) -> None:
    july = migros("100", "-10", month=JULY)
    august = migros("90", "-20", month=AUGUST)
    july = replace(july, filename="july.csv")  # same account, same source, another period
    both = Batch(
        OWNER,
        (august, july),
        pipeline.Month(JULY, (), (), (), date(2026, 8, 31)),
        PEOPLE,
    )
    rows_of = [replace(t, source_file="july.csv") for t in july.extraction.transactions]
    both = replace(
        both,
        month=replace(both.month, transactions=(*august.extraction.transactions, *rows_of)),
    )
    staged = monthclose.stage(conn, both, NOW)
    assert (staged.transactions, staged.gaps) == (2, ())
    assert rows(conn, "SELECT month FROM months ORDER BY 1") == [("2026-07-01",), ("2026-08-01",)]
    assert rows(conn, "SELECT date, amount_nano FROM balances ORDER BY 1") == [
        ("2026-06-30", 100_000_000_000),
        ("2026-07-31", 90_000_000_000),
        ("2026-08-31", 70_000_000_000),
    ]
    assert rows(conn, "SELECT filename, period_start FROM statement_files ORDER BY 2") == [
        ("july.csv", "2026-07-01"),
        ("migros.csv", "2026-08-01"),
    ]


def test_a_longer_export_replaces_a_shorter_one(conn: sqlite3.Connection) -> None:
    july = migros("100", "-10", "-5", month=JULY)
    monthclose.stage(conn, batch(july, month=JULY), NOW)
    longer_rows = [
        txn("-10", date=date(2026, 7, 10), source_ref="1"),
        txn("-5", date=date(2026, 7, 10), source_ref="2"),
        txn("-20", date=date(2026, 8, 10), source_ref="3"),
    ]
    balance = CashBalance("Migros", "CHF", Decimal(100), Decimal(65))
    august_end = date(2026, 8, 31)
    longer = statement("migros", longer_rows, [balance], month=JULY, end=august_end, content="2")
    monthclose.stage(conn, batch(longer, month=JULY, end=august_end), LATER)
    totals = rows(conn, "SELECT count(*), sum(amount_nano) FROM transactions")
    assert totals == [(3, -35_000_000_000)]
    assert rows(conn, "SELECT date, amount_nano FROM balances ORDER BY 1") == [
        ("2026-06-30", 100_000_000_000),
        ("2026-07-31", 85_000_000_000),
        ("2026-08-31", 65_000_000_000),
    ]
    assert rows(conn, "SELECT count(*) FROM statement_files") == [(2,)]


# ------------------------------------------------------------------ the interest on bills


def test_the_fixture_bill_is_split_into_cost_and_interest(
    conn: sqlite3.Connection, folder: Path
) -> None:
    stage_september(conn, folder)
    parts = rows(
        conn,
        "SELECT t.description, t.amount_nano, t.kind, c.name, t.split_of IS NOT NULL "
        "FROM transactions t JOIN categories c ON c.id = t.category_id "
        "WHERE t.description LIKE 'Maturity%' ORDER BY t.amount_nano DESC",
    )
    assert parts == [
        ("Maturity 10.0 912797YY2 @ 1000", 9_920_000_000_000, "income", "Investimenti", 0),
        ("Maturity 10.0 912797YY2 @ 1000 (interest)", 80_000_000_000, "interest", "Interessi", 1),
    ]


def test_a_bill_whose_purchase_is_not_stored_blocks_the_commit(
    conn: sqlite3.Connection, folder: Path
) -> None:
    staged = monthclose.stage(conn, fixture_batch(folder), NOW)
    assert staged.gaps == (
        "Tastytrade: 912797YY2 matured or was sold on 2026-09-16, but its purchases are not all "
        "stored (import the earlier statements): the interest cannot be split from the cost",
    )
    whole = rows(conn, "SELECT amount_nano FROM transactions WHERE description LIKE 'Maturity%'")
    assert whole == [(10_000_000_000_000,)]
    with pytest.raises(MissingLotsError, match="912797YY2 matured or was sold on 2026-09-16"):
        monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)

    # The purchase arrives later: the month already staged is corrected.
    bought = bill("-9920", "10000", date(2026, 8, 20), price=Decimal("99.2"))
    assert monthclose.stage(conn, tastytrade(bought, month=AUGUST), NOW).gaps == ()
    split = rows(
        conn,
        "SELECT amount_nano FROM transactions WHERE description LIKE 'Maturity%' ORDER BY 1",
    )
    assert split == [(80_000_000_000,), (9_920_000_000_000,)]
    assert monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)


def test_lots_are_closed_first_in_first_out(conn: sqlite3.Connection) -> None:
    august = tastytrade(
        bill("-4950", "5000", date(2026, 8, 3)),
        bill("-4960", "5000", date(2026, 8, 17), source_ref="2"),
        month=AUGUST,
    )
    monthclose.stage(conn, august, NOW)
    september = tastytrade(
        bill("2982", "-3000", date(2026, 9, 2)),  # from the first lot: cost 2970
        bill("7000", "-7000", date(2026, 9, 16), kind=Kind.INCOME, source_ref="m"),
    )
    assert monthclose.stage(conn, september, NOW).gaps == ()
    parts = rows(
        conn,
        "SELECT t.date, t.kind, c.name, t.amount_nano FROM transactions t "
        "JOIN categories c ON c.id = t.category_id WHERE t.booking_date >= ? ORDER BY 1, 4 DESC",
        FIRST,
    )
    assert parts == [
        ("2026-09-02", "security_trade", "Investimenti", 2_970_000_000_000),
        ("2026-09-02", "interest", "Interessi", 12_000_000_000),
        ("2026-09-16", "income", "Investimenti", 6_940_000_000_000),  # 1980 + 4960
        ("2026-09-16", "interest", "Interessi", 60_000_000_000),
    ]
    # the statement's figures are untouched in total, and the split is stable
    total = rows(conn, "SELECT sum(amount_nano) FROM transactions WHERE booking_date >= ?", FIRST)
    assert total == [(9_982_000_000_000,)]
    before = dump(conn)
    monthclose.stage(conn, september, LATER)
    assert dump(conn) == before


def test_a_bill_sold_at_its_cost_has_no_interest_row(conn: sqlite3.Connection) -> None:
    monthclose.stage(conn, tastytrade(bill("-4950", "5000", date(2026, 8, 3)), month=AUGUST), NOW)
    monthclose.stage(conn, tastytrade(bill("4950", "-5000", date(2026, 9, 2))), NOW)
    assert rows(conn, "SELECT count(*) FROM transactions WHERE split_of IS NOT NULL") == [(0,)]


def test_a_manual_category_on_the_interest_survives(conn: sqlite3.Connection) -> None:
    monthclose.stage(conn, tastytrade(bill("-4950", "5000", date(2026, 8, 3)), month=AUGUST), NOW)
    sale = tastytrade(bill("4980", "-5000", date(2026, 9, 2)))
    monthclose.stage(conn, sale, NOW)
    (dividends,) = rows(conn, "SELECT id FROM categories WHERE name = 'Dividendi'")[0]
    conn.execute(
        "INSERT INTO overrides (transaction_id, category_id, created_at) "
        "SELECT id, ?, 'x' FROM transactions WHERE split_of IS NOT NULL",
        (dividends,),
    )
    monthclose.stage(conn, sale, LATER)
    kept = rows(
        conn,
        "SELECT c.name, t.category_source, t.amount_nano FROM transactions t "
        "JOIN categories c ON c.id = t.category_id WHERE t.split_of IS NOT NULL",
    )
    assert kept == [("Dividendi", "manual", 30_000_000_000)]


def test_a_bill_sold_at_ibkr_is_split_the_same_way(conn: sqlite3.Connection) -> None:
    ibkr = {"account": "IBKR USD", "source_file": "ibkr.csv"}
    bought = bill("-4950", "5000", date(2026, 8, 3), **ibkr)
    monthclose.stage(conn, batch(statement("ibkr", [bought], month=AUGUST), month=AUGUST), NOW)
    # IBKR's maturity comes already split by the extractor, and is not a trade.
    matured = {**ibkr, "currency": "USD", "date": date(2026, 9, 16)}
    september = [
        bill("2982", "-3000", date(2026, 9, 2), **ibkr),
        txn("1980", kind=Kind.INCOME, category="Investimenti", description="principal", **matured),
        txn("20", kind=Kind.INTEREST, category="Interessi", description="interest", **matured),
    ]
    staged = monthclose.stage(conn, batch(statement("ibkr", september)), NOW)
    assert staged.gaps == ()
    parts = rows(
        conn,
        "SELECT t.description, c.name, t.amount_nano FROM transactions t "
        "JOIN categories c ON c.id = t.category_id WHERE t.booking_date >= ? "
        "ORDER BY t.date, 3 DESC",
        FIRST,
    )
    assert parts == [
        ("-3000 912797YY2", "Investimenti", 2_970_000_000_000),
        ("-3000 912797YY2 (interest)", "Interessi", 12_000_000_000),
        ("principal", "Investimenti", 1_980_000_000_000),
        ("interest", "Interessi", 20_000_000_000),
    ]


# ------------------------------------------------------------------ invariants

AMOUNTS = st.lists(
    st.decimals(min_value=-100_000, max_value=100_000, places=2).map(str), min_size=1, max_size=12
)


@given(AMOUNTS)
def test_what_is_stored_adds_up_to_what_was_imported(amounts: list[str]) -> None:
    conn = new_database()
    month = batch(migros("500", *amounts))
    monthclose.stage(conn, month, NOW)
    total = sum((row.amount for row in month.month.transactions), Decimal(0))
    (stored,) = rows(conn, "SELECT sum(amount_nano) FROM transactions")[0]
    assert from_nano(stored) == total
    (closing,) = rows(conn, "SELECT amount_nano FROM balances WHERE date = '2026-09-30'")[0]
    assert from_nano(closing) == Decimal(500) + total
    conn.close()


@given(AMOUNTS)
def test_running_a_month_again_yields_the_same_database(amounts: list[str]) -> None:
    conn = new_database()
    monthclose.stage(conn, batch(migros("500", *amounts)), NOW)
    monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)
    before = dump(conn)
    monthclose.reopen(conn, owner=OWNER, month=SEPTEMBER)
    monthclose.stage(conn, batch(migros("500", *amounts)), LATER)
    monthclose.commit(conn, owner=OWNER, month=SEPTEMBER, now=NOW)
    assert dump(conn) == before
    conn.close()
