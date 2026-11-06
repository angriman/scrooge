import re
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finanze import categories, classification, months, pipeline, staging
from finanze.categories import Extra
from finanze.models import CashBalance, Holding, Kind
from finanze.staging import Batch
from finanze.storage import db, store
from finanze.storage.money import PrecisionError, from_nano
from finanze.validation import (
    CertificatesDisagreeError,
    ChainMismatchError,
    MissingLotsError,
    MissingStatementError,
)
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
    certificate,
    certified,
    dump,
    migros,
    rows,
    statement,
    tastytrade,
    trade,
    txn,
)

NOW = datetime(2026, 12, 20, 12, 0, tzinfo=UTC)  # after every day the fixtures cover
LATER = datetime(2026, 12, 21, 9, 30, tzinfo=UTC)


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
    statements, skipped = pipeline.load_statements(folder)
    month = pipeline.process(
        [(s.source, s.extraction) for s in statements],
        classification.load_rules([rules or write_rules(folder)]),
        INSTRUMENTS,
        skipped,
    )
    return Batch(OWNER, statements, month, PEOPLE)


def stage_september(
    conn: sqlite3.Connection, folder: Path, rules: Path | None = None, now: datetime = NOW
) -> staging.Staged:
    """The fixture month, after the August purchase of the bill that matures in it."""
    bought = bill("-9920", "10000", date(2026, 8, 20), price=Decimal("99.2"))
    staging.stage(conn, tastytrade(bought, month=AUGUST), now)
    return staging.stage(conn, fixture_batch(folder, rules), now)


FIRST = "2026-09-01"  # for queries that look at September only

# ------------------------------------------------------------------ the fixture month


def test_every_transaction_of_the_month_is_stored(conn: sqlite3.Connection, folder: Path) -> None:
    september = fixture_batch(folder)
    staged = stage_september(conn, folder)
    assert (staged.transactions, staged.uncategorized) == (len(september.month.transactions), 0)
    assert staged.gaps == staged.differences == staged.nothing_new == ()
    assert (staged.already, staged.later) == (0, 0)
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
    staging.stage(conn, fixture_batch(folder), NOW)
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
    staging.stage(conn, fixture_batch(folder), NOW)
    accounts = dict(rows(conn, "SELECT name, kind || ':' || source FROM accounts"))
    assert accounts["Migros"] == "bank:migros"
    assert accounts["Swisscard"] == "card:swisscard"  # an account of its own
    assert accounts["IBKR USD"] == "broker_cash:ibkr"
    assert accounts["Tastytrade"] == "broker_cash:tastytrade"
    files = dict(rows(conn, "SELECT source, reconciled FROM statement_files"))
    assert files["migros"] == "yes"
    assert files["revolut_savings"] == "nothing_to_check"
    assert set(files) == {"ibkr", "migros", "revolut", "revolut_savings", "swisscard", "tastytrade"}
    # every buy and sell, plus the bill that matured at each broker and the shares moved in
    trades = rows(conn, "SELECT count(*) FROM trades")
    month = fixture_batch(folder).month
    bought = [t for t in month.transactions if t.kind is Kind.SECURITY_TRADE]
    assert trades == [(len(bought) + 3,)]
    bills = rows(conn, "SELECT symbol, maturity FROM instruments WHERE type = 'bill' ORDER BY 1")
    assert ("912797ZZ1", "2026-12-24") in bills


def test_importing_the_same_month_again_changes_nothing(
    conn: sqlite3.Connection, folder: Path
) -> None:
    stage_september(conn, folder)
    before = dump(conn)
    stage_september(conn, folder, now=LATER)
    assert dump(conn) == before


def test_staged_statements_are_invisible_until_committed(
    conn: sqlite3.Connection, folder: Path
) -> None:
    staged = stage_september(conn, folder)
    assert rows(conn, "SELECT count(*) FROM committed_transactions") == [(0,)]
    done = staging.commit(conn, owner=OWNER, now=LATER)
    assert len(done) == 7  # the six statements of September and the bill bought in August
    assert {f.source for f in done} >= {"migros", "swisscard"}
    visible = rows(conn, "SELECT count(*), min(booking_date) FROM committed_transactions")
    assert visible == [(staged.transactions + 2, "2026-08-20")]  # the purchase, the interest
    assert rows(conn, "SELECT DISTINCT committed_at FROM statement_files") == [
        ("2026-12-21T09:30:00+00:00",)
    ]
    sample = rows(
        conn,
        "SELECT owner, account, category, category_parent, category_side "
        "FROM committed_transactions WHERE description LIKE 'FRISCHMARKT%' LIMIT 1",
    )
    assert sample == [(OWNER, "Swisscard", "Spesa", "Alimentari", "uscite")]


def test_committing_again_is_a_no_op(conn: sqlite3.Connection, folder: Path) -> None:
    stage_september(conn, folder)
    staging.commit(conn, owner=OWNER, now=NOW)
    before = dump(conn)
    assert staging.commit(conn, owner=OWNER, now=LATER) == []
    assert dump(conn) == before


def test_a_committed_statement_read_again_adds_nothing(
    conn: sqlite3.Connection, folder: Path
) -> None:
    first = stage_september(conn, folder)
    staging.commit(conn, owner=OWNER, now=NOW)
    before = dump(conn)
    again = staging.stage(conn, fixture_batch(folder), LATER)
    assert dump(conn) == before  # the same statements: found there, nothing touched
    assert (again.transactions, again.files, again.differences) == (0, (), ())
    assert again.already == first.transactions
    assert len(again.nothing_new) == 6


def test_uncategorized_rows_block_the_commit(conn: sqlite3.Connection, folder: Path) -> None:
    rules = folder / "few.yaml"
    rules.write_text("rules:\n  - {match: 'Hans Muster', category: Affitto}\n", encoding="utf-8")
    staged = stage_september(conn, folder, rules)
    assert staged.uncategorized > 0
    with pytest.raises(staging.UncategorizedRowsError, match=f"{staged.uncategorized} staged"):
        staging.commit(conn, owner=OWNER, now=NOW)
    assert rows(conn, "SELECT count(*) FROM committed_transactions") == [(0,)]


# ------------------------------------------------------------------ months by hand


def test_internal_rows_need_no_category(conn: sqlite3.Connection) -> None:
    bill = migros("1000", "-200", kind=Kind.INTERNAL, category="", description="Swisscard")
    assert staging.stage(conn, batch(bill), NOW).uncategorized == 0
    assert staging.commit(conn, owner=OWNER, now=NOW)


def test_nothing_staged_means_nothing_to_commit(conn: sqlite3.Connection) -> None:
    staging.stage(conn, batch(migros("0", "-1")), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    for owner in (OWNER, "Nobody"):
        assert staging.commit(conn, owner=owner, now=LATER) == []


@pytest.mark.parametrize("owner", [OWNER, "Nobody"])
def test_a_statement_that_is_not_there_cannot_be_taken_back_or_discarded(
    conn: sqlite3.Connection, owner: str
) -> None:
    staging.stage(conn, batch(migros("0", "-1")), NOW)
    with pytest.raises(staging.NothingStagedError, match=f"{owner} has no statement 99"):
        staging.uncommit(conn, owner=owner, file_id=99)
    with pytest.raises(staging.NothingStagedError, match=f"{owner} has no statement 99"):
        staging.discard(conn, owner=owner, file_id=99)


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
    staging.stage(conn, batch(statement("migros", list(amounts.values()))), NOW)
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
        staging.stage(conn, batch(migros("0", "-1", category="Unicorni")), NOW)
    assert rows(conn, "SELECT count(*) FROM owners") == [(0,)]  # nothing was kept


def test_the_taxonomy_is_seeded_once(conn: sqlite3.Connection) -> None:
    staging.stage(conn, batch(migros("0", "-1")), NOW)
    before = rows(conn, "SELECT * FROM categories ORDER BY id")
    staging.stage(conn, batch(migros("0", "-1")), NOW)
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
    staging.stage(conn, replace(lent, categories=added), NOW)
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
        txn("-40", description="TWINT Anna Meier", category="Ristoranti e bar"),
        txn("25", description="Bonifico", counterparty="ANNA MEIER", category="Rimborsi"),
        txn("-12", description="KONSUM"),
        txn("-500", description="ANNA MEIER", kind=Kind.TRANSFER, category="Topup Revolut"),
        txn("-3", description="LUKAS MEIER"),  # the owner is not their own counterparty
    ]
    staging.stage(conn, batch(statement("migros", lines)), NOW)
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


def marked(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return rows(
        conn,
        "SELECT o.name, t.amount_nano / 1000000000, c.name FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id JOIN owners o ON o.id = a.owner_id "
        "LEFT JOIN owners c ON c.id = t.counterparty_owner_id ORDER BY o.name, t.source_ref",
    )


def revolut(*lines: tuple[str, str, int], owner: str = OWNER) -> Batch:
    """A Revolut statement: one row per (amount, description, day of September)."""
    found = [
        txn(
            amount,
            description=text,
            account="Revolut EUR",
            currency="EUR",
            date=SEPTEMBER.replace(day=day),
            source_ref=str(n),
            source_file="revolut.tsv",
        )
        for n, (amount, text, day) in enumerate(lines, start=1)
    ]
    return batch(statement("revolut", found), owner=owner)


def test_the_half_that_does_not_name_the_other_person_is_marked_too(
    conn: sqlite3.Connection,
) -> None:
    his = revolut(
        ("-200", "Revolut Bank", 7),  # her 200 of the day after
        ("-200", "Revolut Bank", 20),  # nobody received this one
        ("-50", "Transfer to ANNA MEIER", 9),  # named on both sides
        ("-50", "Revolut Bank", 9),  # her second 50 of that day
        ("30", "Revolut Bank", 12),  # two days from her payment: not the same money
    )
    hers = revolut(
        ("200", "Transfer from LUKAS MEIER", 8),
        ("50", "Transfer from LUKAS MEIER", 9),
        ("50", "Transfer from LUKAS MEIER", 9),
        ("-30", "Transfer to LUKAS MEIER", 10),
        ("200", "Transfer from LUKAS MEIER", 28),  # he sent it from somewhere else
        owner=PARTNER,
    )
    staging.stage(conn, his, NOW)
    assert [who for _, _, who in marked(conn)] == [None, None, PARTNER, None, None]
    staging.stage(conn, hers, NOW)  # her import settles his rows as well
    hers_then_his = [who for _, _, who in marked(conn)]
    assert hers_then_his[:5] == [OWNER] * 5
    assert hers_then_his[5:] == [PARTNER, None, PARTNER, PARTNER, None]
    before = dump(conn)
    staging.stage(conn, his, NOW)  # and the order of the imports does not matter
    assert dump(conn)["transactions"] == before["transactions"]


def test_a_committed_month_is_not_marked_afterwards(conn: sqlite3.Connection) -> None:
    staging.stage(conn, revolut(("-200", "Revolut Bank", 7)), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    staging.stage(conn, revolut(("200", "From LUKAS MEIER", 7), owner=PARTNER), NOW)
    assert [who for _, _, who in marked(conn)] == [OWNER, None]


def test_another_bank_or_currency_is_not_the_other_half(conn: sqlite3.Connection) -> None:
    staging.stage(conn, batch(migros("500", "-200", description="Bonifico")), NOW)
    staging.stage(conn, revolut(("200", "From LUKAS MEIER", 10), owner=PARTNER), NOW)
    assert [who for _, _, who in marked(conn)] == [OWNER, None]


def test_each_person_has_their_own_accounts_and_commits(conn: sqlite3.Connection) -> None:
    staging.stage(conn, batch(migros("100", "-1")), NOW)
    staging.stage(conn, batch(migros("900", "-2"), owner=PARTNER), NOW)
    staging.commit(conn, owner=PARTNER, now=NOW)
    files = "SELECT o.name, f.committed_at IS NOT NULL FROM statement_files f JOIN owners o "
    assert rows(conn, files + "ON o.id = f.owner_id") == [(OWNER, 0), (PARTNER, 1)]
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
    staging.stage(conn, batch(statement("ibkr", buys)), NOW)
    stored = rows(
        conn,
        "SELECT i.symbol, i.type, i.currency, i.maturity, t.quantity, t.price, t.cost_nano "
        "FROM trades t JOIN instruments i ON i.id = t.instrument_id ORDER BY 1",
    )
    assert stored == [
        ("912797ZZ1", "bill", "USD", "2026-12-24", "10000", "98.51", 9_851_000_000_000),
        ("WIDG", "etf", "USD", None, "20", "101.25", 2_025_000_000_000),
    ]
    staging.stage(conn, batch(statement("ibkr", [replace(buys[1], maturity=None)])), NOW)
    assert rows(conn, "SELECT maturity FROM instruments WHERE type = 'bill'") == [("2026-12-24",)]
    assert rows(conn, "SELECT count(*) FROM trades") == [(1,)]  # WIDG went with its transaction


def test_a_manual_category_survives_a_new_import(conn: sqlite3.Connection) -> None:
    staging.stage(conn, batch(migros("0", "-30", "-8")), NOW)
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
    staging.stage(conn, batch(migros("0", "-30", "-8")), LATER)
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
    staging.stage(conn, batch(migros("0", "-30", "-8")), NOW)
    corrected = replace(migros("0", "-30", "-8", "-2"), sha256="another export")
    staging.stage(conn, batch(corrected), LATER)
    assert rows(conn, "SELECT sha256, imported_at FROM statement_files") == [
        ("another export", "2026-12-21T09:30:00+00:00")
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
    staging.stage(conn, batch(statement("revolut", both)), NOW)
    only_chf = [txn("-1", account="Revolut CHF", source_file="chf.tsv")]
    staging.stage(conn, batch(statement("revolut", only_chf)), LATER)
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
    with pytest.raises(staging.DuplicateStatementError, match=r"a\.tsv and b\.tsv are both"):
        staging.stage(conn, batch(first, second), NOW)
    assert rows(conn, "SELECT count(*) FROM transactions") == [(0,)]


def test_one_name_cannot_be_two_accounts(conn: sqlite3.Connection) -> None:
    staging.stage(conn, batch(migros("0", "-1")), NOW)
    euros = statement("migros", [txn("-1", currency="EUR")])
    with pytest.raises(store.AccountMismatchError, match="'Migros' is in CHF"):
        staging.stage(conn, batch(euros), NOW)


def test_an_amount_that_does_not_fit_is_refused_and_nothing_is_kept(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(PrecisionError):
        staging.stage(conn, batch(statement("migros", [txn("-0.0000000001")])), NOW)
    assert rows(conn, "SELECT count(*) FROM statement_files") == [(0,)]


# ------------------------------------------------------------------ the chain of balances


def test_statement_balances_are_stored_at_both_ends(conn: sqlite3.Connection) -> None:
    staging.stage(conn, batch(migros("1000", "-200", "50")), NOW)
    assert rows(conn, "SELECT date, amount_nano, source FROM balances ORDER BY 1") == [
        ("2026-08-31", 1_000_000_000_000, "statement"),
        ("2026-09-30", 850_000_000_000, "statement"),
    ]


def test_the_next_month_must_open_where_this_one_closed(conn: sqlite3.Connection) -> None:
    staging.stage(conn, batch(migros("1000", "-200")), NOW)
    before = dump(conn)
    wrong = month_of(OCTOBER, "790", "-90")
    with pytest.raises(ChainMismatchError) as caught:
        staging.stage(conn, wrong, NOW)
    assert (caught.value.expected, caught.value.actual) == (Decimal(700), Decimal(710))
    assert caught.value.span == (date(2026, 9, 30), date(2026, 10, 31))
    assert "Migros (CHF): balance 700 on 2026-10-31" in str(caught.value)
    assert dump(conn) == before

    staging.stage(conn, month_of(OCTOBER, "800", "-90"), NOW)
    assert rows(conn, "SELECT date, amount_nano FROM balances ORDER BY 1") == [
        ("2026-08-31", 1_000_000_000_000),
        ("2026-09-30", 800_000_000_000),
        ("2026-10-31", 710_000_000_000),
    ]


def test_an_earlier_month_imported_later_is_checked_too(conn: sqlite3.Connection) -> None:
    staging.stage(conn, month_of(OCTOBER, "800", "-90"), NOW)
    with pytest.raises(ChainMismatchError, match="2026-10-31"):
        staging.stage(conn, batch(migros("1000", "-150")), NOW)  # closes on 850, not 800
    staging.stage(conn, batch(migros("1000", "-200")), NOW)
    assert staging.commit(conn, owner=OWNER, now=NOW)


def test_a_month_without_a_statement_is_reported_and_blocks_the_commit(
    conn: sqlite3.Connection,
) -> None:
    november = date(2026, 11, 1)
    staging.stage(conn, batch(migros("1000", "-200")), NOW)
    staged = staging.stage(conn, month_of(november, "650", "-50"), NOW)
    assert staged.gaps == (
        "Migros (CHF): no statement covers the days after 2026-09-30 up to 2026-10-31, "
        "yet the balance went from 800 to 650",
    )
    with pytest.raises(MissingStatementError, match="no statement covers") as caught:
        staging.commit(conn, owner=OWNER, now=NOW)
    assert caught.value.span == (date(2026, 9, 30), date(2026, 10, 31))
    assert caught.value.balances == (Decimal(800), Decimal(650))
    assert rows(conn, "SELECT count(*) FROM committed_transactions") == [(0,)]

    filled = staging.stage(conn, month_of(OCTOBER, "800", "-150"), NOW)
    assert filled.gaps == ()
    assert staging.commit(conn, owner=OWNER, now=NOW)


def test_a_quiet_month_without_a_statement_is_no_gap(conn: sqlite3.Connection) -> None:
    november = date(2026, 11, 1)
    staging.stage(conn, batch(migros("1000", "-200")), NOW)
    staged = staging.stage(conn, month_of(november, "800", "-50"), NOW)
    assert staged.gaps == ()
    assert staging.commit(conn, owner=OWNER, now=NOW)


def test_a_commit_checks_the_chain_again(conn: sqlite3.Connection) -> None:
    staging.stage(conn, batch(migros("1000", "-200")), NOW)
    conn.execute("UPDATE transactions SET amount_nano = amount_nano - 1")
    with pytest.raises(ChainMismatchError):
        staging.commit(conn, owner=OWNER, now=NOW)
    assert rows(conn, "SELECT count(*) FROM committed_transactions") == [(0,)]


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
    staging.stage(conn, batch(current, statement("revolut_savings", [interest])), NOW)
    assert rows(conn, "SELECT sum(amount_nano) FROM transactions") == [(-4_920_000_000,)]
    assert staging.commit(conn, owner=OWNER, now=NOW)


def test_the_card_is_an_account_whose_invoices_chain(
    conn: sqlite3.Connection, folder: Path
) -> None:
    stage_september(conn, folder)
    card = rows(
        conn,
        "SELECT b.date, b.amount_nano FROM balances b JOIN accounts a ON a.id = b.account_id "
        "WHERE a.name = 'Swisscard' ORDER BY 1",
    )
    assert card == [("2026-08-04", -1_142_600_000_000), ("2026-09-04", -1_288_180_000_000)]
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
    staging.stage(conn, batch(migros("1000", "-200"), euro), NOW)
    assert rows(conn, "SELECT count(*) FROM balances") == [(4,)]


# ------------------------------------------------------------------ typed balances


def test_an_account_without_statements_gets_typed_balances(conn: sqlite3.Connection) -> None:
    def typed(day: date, amount: str, currency: str = "EUR") -> None:
        staging.type_balance(
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
    staging.stage(conn, batch(migros("0", "-1")), NOW)
    assert staging.commit(conn, owner=OWNER, now=NOW)


def test_an_account_is_fed_by_statements_or_typed_never_both(conn: sqlite3.Connection) -> None:
    staging.stage(conn, batch(migros("0", "-1")), NOW)
    with pytest.raises(store.AccountMismatchError, match="both statements and typed balances"):
        staging.type_balance(
            conn,
            owner=OWNER,
            account="Migros",
            day=date(2026, 9, 30),
            amount=Decimal(5),
            currency="CHF",
        )
    assert rows(conn, "SELECT count(*) FROM balances WHERE source = 'manual'") == [(0,)]


# ------------------------------------------------------------------ pension certificates

MARCH = date(2026, 3, 1)
BALANCES = (
    "SELECT a.name, b.date, b.amount_nano, b.source, f.filename FROM balances b "
    "JOIN accounts a ON a.id = b.account_id "
    "LEFT JOIN statement_files f ON f.id = b.statement_file_id ORDER BY a.name, b.date"
)


def test_a_certificate_gives_its_account_a_balance_and_no_rows(conn: sqlite3.Connection) -> None:
    axa = certificate(MARCH, "35802.00")
    servisa = certificate(date(2026, 1, 15), "3579.40", issuer="Servisa")
    staged = staging.stage(conn, certified(axa, servisa), NOW)
    assert (len(staged.files), staged.transactions, staged.uncategorized) == (2, 0, 0)
    assert rows(conn, "SELECT name, kind, currency, source FROM accounts ORDER BY name") == [
        ("Pillar 2 AXA", "manual", "CHF", "axa"),
        ("Pillar 2 Servisa", "manual", "CHF", "servisa"),
    ]
    assert rows(conn, BALANCES) == [
        ("Pillar 2 AXA", "2026-03-01", 35_802_000_000_000, "statement", axa.filename),
        ("Pillar 2 Servisa", "2026-01-15", 3_579_400_000_000, "statement", servisa.filename),
    ]
    files = "SELECT source, sha256, period_start, period_end, reconciled, holdings "
    assert rows(conn, files + "FROM statement_files ORDER BY source") == [
        ("axa", axa.sha256, "2026-03-01", "2026-03-01", "yes", "not_printed"),
        ("servisa", servisa.sha256, "2026-01-15", "2026-01-15", "nothing_to_check", "not_printed"),
    ]
    # each is a statement of the one day it is dated
    links = "SELECT f.source, a.name FROM statement_accounts s JOIN statement_files f "
    assert rows(
        conn, links + "ON f.id = s.statement_file_id JOIN accounts a ON a.id = s.account_id"
    ) == [
        ("axa", "Pillar 2 AXA"),
        ("servisa", "Pillar 2 Servisa"),
    ]
    assert rows(conn, "SELECT count(*) FROM transactions") == [(0,)]


def test_a_certificate_read_again_changes_nothing(conn: sqlite3.Connection) -> None:
    staging.stage(conn, certified(certificate(MARCH, "35802.00")), NOW)
    before = dump(conn)
    staging.stage(conn, certified(certificate(MARCH, "35802.00")), LATER)
    assert dump(conn) == before


def test_a_newer_certificate_adds_a_balance_and_leaves_the_older(conn: sqlite3.Connection) -> None:
    old, new = certificate(MARCH, "35802.00"), certificate(date(2027, 3, 1), "41000.50")
    staging.stage(conn, certified(old), NOW)
    staging.stage(conn, certified(new), LATER)
    assert rows(conn, BALANCES) == [
        ("Pillar 2 AXA", "2026-03-01", 35_802_000_000_000, "statement", old.filename),
        ("Pillar 2 AXA", "2027-03-01", 41_000_500_000_000, "statement", new.filename),
    ]
    # Nothing is booked between the two and nothing has to be: they are not chained.
    owner = store.owner_id(conn, OWNER)
    assert owner is not None
    assert store.anchors(conn, owner) == []
    assert staging.would_refuse(conn, owner) == []
    assert len(staging.commit(conn, owner=OWNER, now=LATER)) == 2


def test_a_new_scan_of_a_certificate_replaces_what_the_first_stored(
    conn: sqlite3.Connection,
) -> None:
    staging.stage(conn, certified(certificate(MARCH, "35802.00")), NOW)
    again = certificate(MARCH, "35802.00", filename="rescan.pdf", content="other bytes")
    staging.stage(conn, certified(again), LATER)
    assert rows(conn, BALANCES) == [
        ("Pillar 2 AXA", "2026-03-01", 35_802_000_000_000, "statement", "rescan.pdf")
    ]
    assert rows(conn, "SELECT filename FROM statement_files") == [("rescan.pdf",)]


def test_a_certificate_of_a_committed_day_is_not_replaced_until_taken_back(
    conn: sqlite3.Connection,
) -> None:
    first = certificate(MARCH, "35802.00")
    staging.stage(conn, certified(first), NOW)
    (committed,) = staging.commit(conn, owner=OWNER, now=NOW)
    before = dump(conn)
    same = staging.stage(conn, certified(first), LATER)
    assert (same.files, same.nothing_new) == ((), (first.filename,))
    assert dump(conn) == before

    rescan = certificate(MARCH, "35802.00", filename="rescan.pdf", content="other bytes")
    with pytest.raises(staging.CommittedError, match="is committed: take it back before rescan"):
        staging.stage(conn, certified(rescan), LATER)
    assert dump(conn) == before

    staging.uncommit(conn, owner=OWNER, file_id=committed.id)
    staging.stage(conn, certified(rescan), LATER)
    assert rows(conn, "SELECT filename, committed_at FROM statement_files") == [
        ("rescan.pdf", None)
    ]
    assert len(staging.commit(conn, owner=OWNER, now=LATER)) == 1
    assert rows(conn, BALANCES) == [
        ("Pillar 2 AXA", "2026-03-01", 35_802_000_000_000, "statement", "rescan.pdf")
    ]


def test_a_certificate_of_a_month_with_committed_statements_is_staged(
    conn: sqlite3.Connection,
) -> None:
    staging.stage(conn, batch(migros("1000", "-200", month=MARCH), month=MARCH), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    staged = staging.stage(conn, certified(certificate(MARCH, "35802.00")), LATER)
    assert len(staged.files) == 1
    assert len(staging.commit(conn, owner=OWNER, now=LATER)) == 1
    assert rows(conn, "SELECT count(*) FROM transactions") == [(1,)]
    assert rows(conn, "SELECT count(*) FROM balances") == [(3,)]


def test_statements_and_certificates_are_staged_together_or_not_at_all(
    conn: sqlite3.Connection,
) -> None:
    staging.stage(conn, certified(certificate(MARCH, "35802.00")), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    before = dump(conn)
    september = batch(migros("800", "-1"))
    rescan = certificate(MARCH, "35802.00", filename="rescan.pdf", content="other bytes")
    both = replace(september, certificates=(rescan,))
    with pytest.raises(staging.CommittedError):
        staging.stage(conn, both, LATER)
    assert dump(conn) == before
    later = replace(september, certificates=(certificate(date(2026, 9, 1), "36000"),))
    staged = staging.stage(conn, later, LATER)
    assert (len(staged.files), staged.transactions) == (2, 1)
    assert rows(conn, "SELECT count(*) FROM balances") == [(4,)]
    assert rows(conn, "SELECT count(*) FROM statement_files") == [(3,)]


JANUARY, YEAR_BEFORE = date(2026, 1, 1), date(2025, 1, 1)


def test_a_yearly_certificate_also_gives_the_balance_its_year_began_with(
    conn: sqlite3.Connection,
) -> None:
    yearly = certificate(JANUARY, "84710.65", opened=(YEAR_BEFORE, "58240.30"))
    staging.stage(conn, certified(yearly), NOW)
    assert rows(conn, BALANCES) == [
        ("Pillar 2 AXA", "2025-01-01", 58_240_300_000_000, "statement", yearly.filename),
        ("Pillar 2 AXA", "2026-01-01", 84_710_650_000_000, "statement", yearly.filename),
    ]
    # like the balance a bank statement opens with, it is no statement of that earlier day
    assert rows(conn, "SELECT period_start, period_end FROM statement_files") == [
        ("2026-01-01", "2026-01-01")
    ]
    before = dump(conn)
    staging.stage(conn, certified(yearly), LATER)
    assert dump(conn) == before


def test_a_new_scan_replaces_the_balance_the_year_began_with_too(conn: sqlite3.Connection) -> None:
    staging.stage(
        conn, certified(certificate(JANUARY, "84710.65", opened=(YEAR_BEFORE, "58240.30"))), NOW
    )
    again = certificate(JANUARY, "84710.65", opened=(YEAR_BEFORE, "58240.20"), filename="again.pdf")
    staging.stage(conn, certified(again), LATER)
    assert rows(conn, BALANCES) == [
        ("Pillar 2 AXA", "2025-01-01", 58_240_200_000_000, "statement", "again.pdf"),
        ("Pillar 2 AXA", "2026-01-01", 84_710_650_000_000, "statement", "again.pdf"),
    ]
    assert rows(conn, "SELECT filename FROM statement_files") == [("again.pdf",)]


@pytest.mark.parametrize("together", [True, False])
@pytest.mark.parametrize("yearly_first", [True, False])
def test_a_day_belongs_to_its_own_certificate_whichever_comes_first(
    conn: sqlite3.Connection, together: bool, yearly_first: bool
) -> None:
    own = certificate(YEAR_BEFORE, "58240.30")
    yearly = certificate(JANUARY, "84710.65", opened=(YEAR_BEFORE, "58240.30"))
    scans = (yearly, own) if yearly_first else (own, yearly)
    for group in [scans] if together else [scans[:1], scans[1:]]:
        staging.stage(conn, certified(*group), NOW)
    assert rows(conn, BALANCES) == [
        ("Pillar 2 AXA", "2025-01-01", 58_240_300_000_000, "statement", own.filename),
        ("Pillar 2 AXA", "2026-01-01", 84_710_650_000_000, "statement", yearly.filename),
    ]


@pytest.mark.parametrize("yearly_first", [True, False])
def test_two_certificates_that_disagree_on_a_day_are_refused(
    conn: sqlite3.Connection, yearly_first: bool
) -> None:
    own = certificate(YEAR_BEFORE, "58240.30")
    yearly = certificate(JANUARY, "84710.65", opened=(YEAR_BEFORE, "58240.20"))
    first, second = (yearly, own) if yearly_first else (own, yearly)
    staging.stage(conn, certified(first), NOW)
    before = dump(conn)
    with pytest.raises(
        CertificatesDisagreeError,
        match=r"Pillar 2 AXA \(CHF\): two certificates disagree on 2025-01-01: 58240\.[23]0 and",
    ):
        staging.stage(conn, certified(second), LATER)
    assert dump(conn) == before


def test_two_certificates_of_one_account_for_one_day_are_refused(conn: sqlite3.Connection) -> None:
    one = certificate(MARCH, "35802.00", filename="a.pdf")
    other = certificate(MARCH, "35802.00", filename="b.pdf")
    with pytest.raises(
        staging.DuplicateStatementError,
        match=r"a\.pdf and b\.pdf are both certificates of Pillar 2 AXA for 2026-03-01",
    ):
        staging.stage(conn, certified(one, other), NOW)
    assert rows(conn, "SELECT count(*) FROM balances") == [(0,)]
    # the same day at two pension funds is two accounts
    servisa = certificate(MARCH, "3579.40", issuer="Servisa")
    staging.stage(conn, certified(one, one, servisa), NOW)
    assert rows(conn, "SELECT count(*) FROM balances") == [(2,)]


def test_a_pension_account_is_fed_by_certificates_or_typed_never_both(
    conn: sqlite3.Connection,
) -> None:
    def typed(account: str) -> None:
        staging.type_balance(
            conn, owner=OWNER, account=account, day=MARCH, amount=Decimal(5), currency="CHF"
        )

    staging.stage(conn, certified(certificate(MARCH, "35802.00")), NOW)
    with pytest.raises(store.AccountMismatchError, match="both statements and typed balances"):
        typed("Pillar 2 AXA")
    typed("Pillar 2 Servisa")
    with pytest.raises(store.AccountMismatchError, match="both statements and typed balances"):
        staging.stage(conn, certified(certificate(MARCH, "1", issuer="Servisa")), NOW)
    assert rows(conn, "SELECT count(*) FROM balances") == [(2,)]


@given(
    st.lists(
        st.tuples(
            st.dates(date(2024, 1, 1), date(2027, 12, 31)),
            st.decimals(0, 500_000, places=2),
            st.sampled_from(["AXA", "Servisa"]),
        ),
        min_size=1,
        max_size=6,
    ),
    st.randoms(),
)
def test_however_certificates_arrive_each_day_keeps_the_last_figure_read(
    read: list[tuple[date, Decimal, str]], shuffled: Any
) -> None:
    conn = new_database()
    expected: dict[tuple[str, str], int] = {}
    for n, (day, balance, issuer) in enumerate(read):
        scan = certificate(day, str(balance), issuer=issuer, content=str(n))
        staging.stage(conn, certified(scan), NOW)
        expected[f"Pillar 2 {issuer}", day.isoformat()] = int(balance * 1_000_000_000)
    stored = {(name, day): nano for name, day, nano, _, _ in rows(conn, BALANCES)}
    assert stored == expected
    assert rows(conn, "SELECT count(*) FROM statement_files") == [(len(expected),)]
    # importing everything once more, in any order, changes nothing
    before = dump(conn)
    last = {(issuer, day): n for n, (day, _, issuer) in enumerate(read)}
    kept = [
        certificate(day, str(balance), issuer=issuer, content=str(n))
        for n, (day, balance, issuer) in enumerate(read)
        if last[issuer, day] == n
    ]
    shuffled.shuffle(kept)
    staging.stage(conn, certified(*kept), LATER)
    assert dump(conn) == before
    owner = store.owner_id(conn, OWNER)
    assert owner is not None
    assert staging.would_refuse(conn, owner) == []
    conn.close()


# ------------------------------------------------------------------ several months at once

JULY = date(2026, 7, 1)
PENSION = Path(__file__).parent / "fixtures" / "finpension" / "transactions.tsv"


def pension_batch(folder: Path) -> Batch:
    """The pillar 3a fixture (July to September 2026)."""
    (folder / "pillar 3a.tsv").write_bytes(PENSION.read_bytes())
    statements, skipped = pipeline.load_statements(folder)
    extractions = [(s.source, s.extraction) for s in statements]
    month = pipeline.process(extractions, (), {}, skipped)
    return Batch(OWNER, statements, month, PEOPLE)


def test_a_statement_of_several_months_is_one_file(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    staged = staging.stage(conn, pension_batch(tmp_path), NOW)
    assert (len(staged.files), staged.transactions, staged.uncategorized) == (1, 9, 0)
    assert rows(conn, "SELECT period_start, period_end FROM statement_files") == [
        ("2026-07-06", "2026-09-16")
    ]
    assert rows(conn, "SELECT date, amount_nano FROM balances ORDER BY 1") == [
        ("2026-07-05", 0),
        ("2026-09-16", 13_671_078_000),
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


def test_a_statement_of_several_months_is_committed_whole_and_taken_back_whole(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    staging.stage(conn, pension_batch(tmp_path), NOW)
    before = dump(conn)
    (done,) = staging.commit(conn, owner=OWNER, now=NOW)
    visible = rows(conn, "SELECT DISTINCT substr(booking_date, 1, 7) FROM committed_transactions")
    assert visible == [("2026-07",), ("2026-08",), ("2026-09",)]

    staging.stage(conn, pension_batch(tmp_path), LATER)  # read again: found, nothing touched
    committed = dump(conn)
    assert staging.uncommit(conn, owner=OWNER, file_id=done.id).id == done.id
    assert rows(conn, "SELECT count(*) FROM committed_transactions") == [(0,)]
    assert dump(conn) == before
    assert committed != before
    staging.stage(conn, pension_batch(tmp_path), LATER)
    staging.commit(conn, owner=OWNER, now=NOW)
    assert dump(conn) == committed


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
    staged = staging.stage(conn, both, NOW)
    assert (staged.transactions, staged.gaps) == (2, ())
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
    staging.stage(conn, batch(july, month=JULY), NOW)
    longer_rows = [
        txn("-10", date=date(2026, 7, 10), source_ref="1"),
        txn("-5", date=date(2026, 7, 10), source_ref="2"),
        txn("-20", date=date(2026, 8, 10), source_ref="3"),
    ]
    balance = CashBalance("Migros", "CHF", Decimal(100), Decimal(65))
    august_end = date(2026, 8, 31)
    longer = statement("migros", longer_rows, [balance], month=JULY, end=august_end, content="2")
    staging.stage(conn, batch(longer, month=JULY, end=august_end), LATER)
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
    staged = staging.stage(conn, fixture_batch(folder), NOW)
    assert staged.gaps == (
        "Tastytrade: 912797YY2 matured or was sold on 2026-09-16, but its purchases are not all "
        "stored (import the earlier statements): the interest cannot be split from the cost",
    )
    whole = rows(conn, "SELECT amount_nano FROM transactions WHERE description LIKE 'Maturity%'")
    assert whole == [(10_000_000_000_000,)]
    with pytest.raises(MissingLotsError, match="912797YY2 matured or was sold on 2026-09-16"):
        staging.commit(conn, owner=OWNER, now=NOW)

    # The purchase arrives later: the month already staged is corrected.
    bought = bill("-9920", "10000", date(2026, 8, 20), price=Decimal("99.2"))
    assert staging.stage(conn, tastytrade(bought, month=AUGUST), NOW).gaps == ()
    split = rows(
        conn,
        "SELECT amount_nano FROM transactions WHERE description LIKE 'Maturity%' ORDER BY 1",
    )
    assert split == [(80_000_000_000,), (9_920_000_000_000,)]
    assert staging.commit(conn, owner=OWNER, now=NOW)


def test_lots_are_closed_first_in_first_out(conn: sqlite3.Connection) -> None:
    august = tastytrade(
        bill("-4950", "5000", date(2026, 8, 3)),
        bill("-4960", "5000", date(2026, 8, 17), source_ref="2"),
        month=AUGUST,
    )
    staging.stage(conn, august, NOW)
    september = tastytrade(
        bill("2982", "-3000", date(2026, 9, 2)),  # from the first lot: cost 2970
        bill("7000", "-7000", date(2026, 9, 16), kind=Kind.INCOME, source_ref="m"),
    )
    assert staging.stage(conn, september, NOW).gaps == ()
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
    staging.stage(conn, september, LATER)
    assert dump(conn) == before


def test_a_bill_sold_at_its_cost_has_no_interest_row(conn: sqlite3.Connection) -> None:
    staging.stage(conn, tastytrade(bill("-4950", "5000", date(2026, 8, 3)), month=AUGUST), NOW)
    staging.stage(conn, tastytrade(bill("4950", "-5000", date(2026, 9, 2))), NOW)
    assert rows(conn, "SELECT count(*) FROM transactions WHERE split_of IS NOT NULL") == [(0,)]


def test_a_manual_category_on_the_interest_survives(conn: sqlite3.Connection) -> None:
    staging.stage(conn, tastytrade(bill("-4950", "5000", date(2026, 8, 3)), month=AUGUST), NOW)
    sale = tastytrade(bill("4980", "-5000", date(2026, 9, 2)))
    staging.stage(conn, sale, NOW)
    (dividends,) = rows(conn, "SELECT id FROM categories WHERE name = 'Dividendi'")[0]
    conn.execute(
        "INSERT INTO overrides (transaction_id, category_id, created_at) "
        "SELECT id, ?, 'x' FROM transactions WHERE split_of IS NOT NULL",
        (dividends,),
    )
    staging.stage(conn, sale, LATER)
    kept = rows(
        conn,
        "SELECT c.name, t.category_source, t.amount_nano FROM transactions t "
        "JOIN categories c ON c.id = t.category_id WHERE t.split_of IS NOT NULL",
    )
    assert kept == [("Dividendi", "manual", 30_000_000_000)]


def test_a_bill_sold_at_ibkr_is_split_the_same_way(conn: sqlite3.Connection) -> None:
    ibkr = {"account": "IBKR USD", "source_file": "ibkr.csv"}
    bought = bill("-4950", "5000", date(2026, 8, 3), **ibkr)
    staging.stage(conn, batch(statement("ibkr", [bought], month=AUGUST), month=AUGUST), NOW)
    # IBKR's maturity comes already split by the extractor, and is not a trade.
    matured = {**ibkr, "currency": "USD", "date": date(2026, 9, 16)}
    september = [
        bill("2982", "-3000", date(2026, 9, 2), **ibkr),
        txn("1980", kind=Kind.INCOME, category="Investimenti", description="principal", **matured),
        txn("20", kind=Kind.INTEREST, category="Interessi", description="interest", **matured),
    ]
    staged = staging.stage(conn, batch(statement("ibkr", september)), NOW)
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
    staging.stage(conn, month, NOW)
    total = sum((row.amount for row in month.month.transactions), Decimal(0))
    (stored,) = rows(conn, "SELECT sum(amount_nano) FROM transactions")[0]
    assert from_nano(stored) == total
    (closing,) = rows(conn, "SELECT amount_nano FROM balances WHERE date = '2026-09-30'")[0]
    assert from_nano(closing) == Decimal(500) + total
    conn.close()


@given(AMOUNTS)
def test_taking_a_statement_back_and_importing_it_again_yields_the_same_database(
    amounts: list[str],
) -> None:
    conn = new_database()
    staging.stage(conn, batch(migros("500", *amounts)), NOW)
    (done,) = staging.commit(conn, owner=OWNER, now=NOW)
    before = dump(conn)
    staging.uncommit(conn, owner=OWNER, file_id=done.id)
    staging.stage(conn, batch(migros("500", *amounts)), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    assert dump(conn) == before
    conn.close()


def test_a_commit_with_a_copy_keeps_the_copy_only_when_something_changed(tmp_path: Path) -> None:
    database = tmp_path / "finanze.db"
    conn = db.connect(database)
    db.migrate(conn, NOW)
    staging.stage(conn, month_of(SEPTEMBER, "100", "-20"), NOW)
    copies = tmp_path / "backups"

    def commit() -> Path | None:
        return staging.commit_with_copy(conn, database, owner=OWNER, now=LATER)[1]

    conn.execute("UPDATE transactions SET category_id = NULL, category_source = NULL")
    with pytest.raises(staging.UncategorizedRowsError):
        commit()
    assert list(copies.iterdir()) == []

    staging.stage(conn, month_of(SEPTEMBER, "100", "-20"), NOW)
    copy = commit()
    assert copy == copies / "finanze-20261221T093000.db"
    before = db.connect(copy)
    assert rows(before, "SELECT committed_at FROM statement_files") == [(None,)]
    before.close()
    assert rows(conn, "SELECT committed_at FROM statement_files") == [
        ("2026-12-21T09:30:00+00:00",)
    ]

    assert copy is not None
    copy.unlink()
    assert commit() is None
    assert list(copies.iterdir()) == []
    conn.close()


def test_two_commits_in_one_second_do_not_share_a_copy(tmp_path: Path) -> None:
    database = tmp_path / "finanze.db"
    conn = db.connect(database)
    db.migrate(conn, NOW)
    staging.stage(conn, month_of(SEPTEMBER, "100", "-20"), NOW)
    staging.stage(conn, batch(migros("0", "-5"), owner=PARTNER), NOW)
    conn.execute(
        "UPDATE transactions SET category_id = NULL, category_source = NULL "
        "WHERE amount_nano = -5000000000"
    )
    done, kept = staging.commit_with_copy(conn, database, owner=OWNER, now=LATER)
    assert kept is not None
    assert len(done) == 1
    # The same second: once refused, once with nothing left to commit.
    with pytest.raises(staging.UncategorizedRowsError):
        staging.commit_with_copy(conn, database, owner=PARTNER, now=LATER)
    assert staging.commit_with_copy(conn, database, owner=OWNER, now=LATER) == ([], None)
    assert list((tmp_path / "backups").iterdir()) == [kept]
    before = db.connect(kept)
    assert rows(before, "SELECT DISTINCT committed_at FROM statement_files") == [(None,)]
    before.close()

    second = staging.copy_before(conn, database, LATER)
    third = staging.copy_before(conn, database, LATER)
    assert [second.name, third.name] == [
        "finanze-20261221T093000-2.db",
        "finanze-20261221T093000-3.db",
    ]
    conn.close()


def waiting_month(conn: sqlite3.Connection) -> dict[str, str]:
    """September staged with rows that need a category; returns description -> id."""
    plain = {"category": "", "date": date(2026, 9, 10)}
    found = [
        txn("-20", source_ref="1", description="KIOSK", **plain),
        txn("-30", source_ref="2", description="KIOSK", **plain),
        txn("5", source_ref="3", description="KIOSK", kind=Kind.INCOME, **plain),
        txn("-200", source_ref="4", description="TWINT", counterparty="Anna Meier", **plain),
        txn("-500", source_ref="5", description="TO SAVINGS", **plain),
        txn("-7", source_ref="6", description=" ", **plain),
        txn("-9", source_ref="7", description="DONE", date=date(2026, 9, 10)),
    ]
    balance = CashBalance("Migros", "CHF", Decimal(100), Decimal(-661))
    staging.stage(conn, batch(statement("migros", found, [balance])), NOW)
    stored = rows(conn, "SELECT description, source_ref, id FROM transactions")
    return {f"{description.strip()}{ref}": txn_id for description, ref, txn_id in stored}


def choose(
    conn: sqlite3.Connection,
    ids: list[str],
    category: str,
    remember: staging.Remember | None = None,
    owner: str = OWNER,
) -> int:
    return staging.categorize(
        conn, owner=owner, ids=ids, category=category, now=LATER, remember=remember
    )


def category_of(conn: sqlite3.Connection, txn_id: str) -> tuple[Any, ...]:
    (found,) = rows(
        conn,
        "SELECT c.name, c.side, t.category_source, t.kind FROM transactions t "
        "LEFT JOIN categories c ON c.id = t.category_id WHERE t.id = ?",
        txn_id,
    )
    return found


def test_a_choice_for_these_rows_alone_is_an_override_that_survives_an_import(
    conn: sqlite3.Connection,
) -> None:
    ids = waiting_month(conn)
    assert choose(conn, [ids["KIOSK1"], ids["KIOSK3"]], "Regali") == 2
    assert category_of(conn, ids["KIOSK1"]) == ("Regali", "uscite", "manual", "expense")
    assert category_of(conn, ids["KIOSK3"]) == ("Regali", "uscite", "manual", "income")
    assert category_of(conn, ids["KIOSK2"]) == (None, None, None, "expense")
    # The kind was not chosen: it is not pinned, and a later import may give another.
    assert rows(conn, "SELECT count(*), min(created_at), max(kind) FROM overrides") == [
        (2, "2026-12-21T09:30:00+00:00", None)
    ]

    assert waiting_month(conn) == ids  # imported again: the same rows, the choice put back
    assert category_of(conn, ids["KIOSK1"]) == ("Regali", "uscite", "manual", "expense")
    assert category_of(conn, ids["KIOSK2"]) == (None, None, None, "expense")


def test_a_choice_to_remember_becomes_a_rule_per_counterparty_and_direction(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    ids = waiting_month(conn)
    shared, own = tmp_path / "rules.local.yaml", tmp_path / "rules.lukas.yaml"
    everyone = [pattern for patterns in PEOPLE.values() for pattern in patterns]
    remember = staging.Remember(shared, own, everyone)
    chosen = [ids["KIOSK1"], ids["KIOSK2"], ids["KIOSK3"], ids["TWINT4"]]
    assert choose(conn, chosen, "Regali", remember) == 4
    assert category_of(conn, ids["KIOSK2"]) == ("Regali", "uscite", "rule", "expense")
    assert rows(conn, "SELECT count(*) FROM overrides") == [(0,)]
    assert shared.read_text(encoding="utf-8") == (
        "rules:\n"
        '  - {match: "(?m)^KIOSK$", category: "Regali", direction: out}\n'
        '  - {match: "(?m)^KIOSK$", category: "Regali", direction: in}\n'
    )
    assert own.read_text(encoding="utf-8") == (
        'rules:\n  - {match: "(?m)^Anna\\\\ Meier$", category: "Regali", direction: out}\n'
    )


def test_a_transfer_category_makes_the_row_a_transfer(conn: sqlite3.Connection) -> None:
    ids = waiting_month(conn)
    choose(conn, [ids["TO SAVINGS5"]], "Giroconto")
    assert category_of(conn, ids["TO SAVINGS5"]) == ("Giroconto", "transfer", "manual", "transfer")
    assert rows(conn, "SELECT kind FROM overrides") == [("transfer",)]


def named(conn: sqlite3.Connection, txn_id: str) -> Any:
    return rows(conn, "SELECT counterparty_owner_id FROM transactions WHERE id = ?", txn_id)[0][0]


def test_a_row_made_a_transfer_is_no_longer_money_between_the_two(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    ids = waiting_month(conn)
    twint = ids["TWINT4"]
    assert named(conn, twint) is not None
    choose(conn, [ids["KIOSK1"]], "Regali")
    before = dump(conn)

    # As a rule it would be lost on the next import: transfer rules skip rows naming the other.
    everyone = [pattern for patterns in PEOPLE.values() for pattern in patterns]
    shared, own = tmp_path / "rules.local.yaml", tmp_path / "rules.lukas.yaml"
    remember = staging.Remember(shared, own, everyone, PEOPLE[PARTNER])
    with pytest.raises(staging.StagingError, match=r"a transfer \(Anna Meier\)"):
        choose(conn, [twint], "Giroconto", remember)
    assert dump(conn) == before
    assert not list(tmp_path.iterdir())
    assert choose(conn, [twint], "Regali", remember) == 1  # not a transfer: a rule of his own
    assert named(conn, twint) is not None
    assert [path.name for path in tmp_path.iterdir()] == ["rules.lukas.yaml"]

    ids = waiting_month(conn)  # imported again without that rule: the row waits again
    choose(conn, [twint], "Giroconto")
    assert category_of(conn, twint) == ("Giroconto", "transfer", "manual", "transfer")
    assert named(conn, twint) is None
    assert waiting_month(conn) == ids  # and the override says the same after an import
    assert category_of(conn, twint) == ("Giroconto", "transfer", "manual", "transfer")
    assert named(conn, twint) is None


def test_rules_files_are_left_as_they_were_when_a_choice_fails(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = waiting_month(conn)
    before = dump(conn)
    shared, own = tmp_path / "rules.local.yaml", tmp_path / "rules.lukas.yaml"
    everyone = [pattern for patterns in PEOPLE.values() for pattern in patterns]
    remember = staging.Remember(shared, own, everyone)
    chosen = [ids["KIOSK1"], ids["KIOSK3"], ids["TWINT4"]]
    append = classification.append_rule
    saved: list[Path] = []

    def full_disk(path: Path, *rule: str, first: bool) -> None:
        if len(saved) == 2:  # the third rule cannot be saved
            raise OSError("no space left")
        saved.append(path)
        append(path, *rule, first=first)

    monkeypatch.setattr(classification, "append_rule", full_disk)
    with pytest.raises(OSError, match="no space left"):
        choose(conn, chosen, "Regali", remember)
    assert len(saved) == 2
    assert not list(tmp_path.iterdir())  # files that were not there are gone again

    saved.clear()
    shared.write_text("rules: []\n", encoding="utf-8")
    own.write_text("rules: []\n", encoding="utf-8")
    with pytest.raises(OSError, match="no space left"):
        choose(conn, chosen, "Regali", remember)
    assert shared.read_text(encoding="utf-8") == own.read_text(encoding="utf-8") == "rules: []\n"
    assert dump(conn) == before


def test_a_choice_that_cannot_be_made_changes_nothing(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    ids = waiting_month(conn)
    before = dump(conn)
    remember = staging.Remember(tmp_path / "rules.yaml", tmp_path / "own.yaml", ())
    one = [ids["KIOSK1"]]
    refused: list[tuple[list[str], str, staging.Remember | None, str]] = [
        ([*one, ids["DONE7"]], "Regali", None, "no longer waiting"),
        ([*one, "no-such-row"], "Regali", None, "no longer waiting"),
        ([], "Regali", None, "no longer waiting"),
        (one, "", None, "no category was chosen"),
        ([*one, ids["6"]], "Regali", remember, "any text"),
    ]
    for chosen, category, how, message in refused:
        with pytest.raises(staging.StagingError, match=message):
            choose(conn, chosen, category, how)
    with pytest.raises(store.UnknownCategoryError, match="'Regalo'"):
        choose(conn, one, "Regalo")
    assert dump(conn) == before
    assert not list(tmp_path.iterdir())

    # The same id twice is one row; a row without text can still be set for itself alone.
    assert choose(conn, [ids["6"], ids["6"]], "Regali") == 1


def choose_all(
    conn: sqlite3.Connection,
    choices: list[staging.Choice],
    remember: staging.Remember | None = None,
) -> int:
    return staging.categorize_all(conn, owner=OWNER, choices=choices, now=LATER, remember=remember)


def test_choices_made_together_are_given_all_or_none(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    ids = waiting_month(conn)
    before = dump(conn)
    shared, own = tmp_path / "rules.local.yaml", tmp_path / "rules.lukas.yaml"
    everyone = [pattern for patterns in PEOPLE.values() for pattern in patterns]
    remember = staging.Remember(shared, own, everyone)
    kiosk = staging.Choice((ids["KIOSK1"], ids["KIOSK2"]), "Regali", rule=True)
    unknown = staging.Choice((ids["TO SAVINGS5"],), "Regalo")
    with pytest.raises(store.UnknownCategoryError, match="'Regalo'"):
        choose_all(conn, [kiosk, unknown], remember)
    assert dump(conn) == before
    assert not list(tmp_path.iterdir())  # the rule of the first choice went with it

    savings = staging.Choice((ids["TO SAVINGS5"],), "Giroconto")
    changed = staging.Choice((ids["DONE7"],), "Regali", change=True)
    assert choose_all(conn, [kiosk, savings, changed], remember) == 4
    assert category_of(conn, ids["KIOSK2"]) == ("Regali", "uscite", "rule", "expense")
    assert category_of(conn, ids["TO SAVINGS5"]) == ("Giroconto", "transfer", "manual", "transfer")
    assert category_of(conn, ids["DONE7"]) == ("Regali", "uscite", "manual", "expense")
    assert shared.read_text(encoding="utf-8") == (
        'rules:\n  - {match: "(?m)^KIOSK$", category: "Regali", direction: out}\n'
    )
    assert not own.exists()
    # Without a place for rules, a choice to remember is kept for its rows alone.
    choice = staging.Choice((ids["KIOSK3"],), "Regali", rule=True)
    assert choose_all(conn, [choice]) == 1
    assert category_of(conn, ids["KIOSK3"]) == ("Regali", "uscite", "manual", "income")
    assert choose_all(conn, []) == 0


def test_rows_of_a_committed_statement_or_of_nobody_are_not_waiting_for_a_category(
    conn: sqlite3.Connection,
) -> None:
    staging.stage(conn, month_of(SEPTEMBER, "100", "-20"), NOW)
    (txn_id,) = rows(conn, "SELECT id FROM transactions")[0]
    staging.commit(conn, owner=OWNER, now=NOW)
    with pytest.raises(staging.StagingError, match=f"{staging._NOT_WAITING}"):
        choose(conn, [txn_id], "Regali")
    with pytest.raises(staging.StagingError, match="load the page again"):
        choose(conn, [txn_id], "Regali", owner="Nobody")


def change(
    conn: sqlite3.Connection,
    ids: list[str],
    category: str,
    remember: staging.Remember | None = None,
) -> int:
    return staging.categorize(
        conn,
        owner=OWNER,
        ids=ids,
        category=category,
        now=LATER,
        remember=remember,
        change=True,
    )


def test_a_row_that_has_a_category_can_be_given_another_which_an_import_keeps(
    conn: sqlite3.Connection,
) -> None:
    ids = waiting_month(conn)
    assert category_of(conn, ids["DONE7"]) == ("Spesa", "uscite", "rule", "expense")
    assert change(conn, [ids["DONE7"]], "Regali") == 1
    assert category_of(conn, ids["DONE7"]) == ("Regali", "uscite", "manual", "expense")
    assert waiting_month(conn) == ids  # imported again, with the category the statement gave
    assert category_of(conn, ids["DONE7"]) == ("Regali", "uscite", "manual", "expense")


def test_a_changed_category_to_remember_goes_before_the_rule_that_was_wrong(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    ids = waiting_month(conn)
    shared, own = tmp_path / "rules.local.yaml", tmp_path / "rules.lukas.yaml"
    shared.write_text("rules:\n  - {match: 'DONE', category: Spesa}\n", encoding="utf-8")
    remember = staging.Remember(shared, own, [])
    assert change(conn, [ids["DONE7"]], "Regali", remember) == 1
    assert shared.read_text(encoding="utf-8") == (
        "rules:\n"
        '  - {match: "(?m)^DONE$", category: "Regali", direction: out}\n'
        "  - {match: 'DONE', category: Spesa}\n"
    )
    # These rows keep it whatever the rules say on the next import.
    assert rows(conn, "SELECT transaction_id, kind FROM overrides") == [(ids["DONE7"], None)]
    assert category_of(conn, ids["DONE7"]) == ("Regali", "uscite", "manual", "expense")


def test_a_transfer_given_an_expense_category_is_an_expense_again(
    conn: sqlite3.Connection,
) -> None:
    ids = waiting_month(conn)
    choose(conn, [ids["TO SAVINGS5"], ids["KIOSK3"]], "Giroconto")
    assert category_of(conn, ids["KIOSK3"])[3] == "transfer"
    assert change(conn, [ids["TO SAVINGS5"], ids["KIOSK3"]], "Regali") == 2
    assert category_of(conn, ids["TO SAVINGS5"]) == ("Regali", "uscite", "manual", "expense")
    assert category_of(conn, ids["KIOSK3"]) == ("Regali", "uscite", "manual", "income")
    assert rows(conn, "SELECT kind FROM overrides ORDER BY kind") == [("expense",), ("income",)]


def test_only_rows_that_have_a_category_and_are_money_in_or_out_can_be_changed(
    conn: sqlite3.Connection,
) -> None:
    ids = waiting_month(conn)
    with pytest.raises(staging.StagingError, match="cannot be given another category"):
        change(conn, [ids["KIOSK1"]], "Regali")  # it has none yet
    bought = trade("-1012.50", "WIDG", "10", date=date(2026, 9, 15))
    staging.stage(conn, batch(statement("ibkr", [bought])), NOW)
    (trade_id,) = rows(conn, "SELECT id FROM transactions WHERE kind = 'security_trade'")
    with pytest.raises(staging.StagingError, match="cannot be given another category"):
        change(conn, [trade_id[0]], "Regali")


def test_a_committed_row_can_be_given_another_category(conn: sqlite3.Connection) -> None:
    staging.stage(conn, month_of(SEPTEMBER, "100", "-20"), NOW)
    (txn_id,) = rows(conn, "SELECT id FROM transactions")[0]
    staging.commit(conn, owner=OWNER, now=NOW)
    assert change(conn, [txn_id], "Regali") == 1
    seen = rows(conn, "SELECT category FROM committed_transactions")
    assert seen == [("Regali",)]  # the dashboards follow at once


def added(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return rows(
        conn,
        "SELECT c.name, c.side, p.name FROM categories c "
        "LEFT JOIN categories p ON p.id = c.parent_id WHERE c.name IN ('Prestiti', 'Animali')",
    )


def test_a_category_can_be_added_under_a_group_of_its_own_an_old_one_or_the_income(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    waiting_month(conn)
    file = tmp_path / "categories.yaml"
    staging.add_category(conn, file, name="  Prestiti ", side="uscite", group="")
    staging.add_category(conn, file, name="Animali", side="uscite", group="Casa")
    staging.add_category(conn, file, name="Prestiti", side="entrate", group="")
    assert sorted(added(conn), key=str) == sorted(
        [
            ("Prestiti", "uscite", None),  # the group
            ("Prestiti", "uscite", "Prestiti"),  # and its one leaf
            ("Animali", "uscite", "Casa"),
            ("Prestiti", "entrate", None),
        ],
        key=str,
    )
    assert categories.load(file) == (
        categories.Extra("Prestiti", "uscite", "Prestiti"),
        categories.Extra("Animali", "uscite", "Casa"),
        categories.Extra("Prestiti", "entrate"),
    )


@pytest.mark.parametrize(
    ("wanted", "message"),
    [
        ((" ", "uscite", ""), "a category needs a name"),
        (("Spesa", "uscite", "Casa"), "there is already a category 'Spesa'"),
        (("Stipendio", "entrate", ""), "there is already a category 'Stipendio'"),
        (("Giroconto", "uscite", ""), "there is already a category 'Giroconto'"),
        (("Animali", "uscite", "Zoo"), "no group of categories named 'Zoo'"),
        (("Animali", "transfer", ""), "a category is added to the expenses or to the income"),
    ],
)
def test_a_category_that_cannot_be_added_changes_nothing(
    conn: sqlite3.Connection, tmp_path: Path, wanted: tuple[str, str, str], message: str
) -> None:
    name, side, group = wanted
    waiting_month(conn)
    file = tmp_path / "categories.yaml"
    before = dump(conn)
    with pytest.raises(staging.StagingError, match=message):
        staging.add_category(conn, file, name=name, side=side, group=group)
    assert dump(conn) == before
    assert not file.exists()


def test_a_category_the_database_refuses_is_not_left_in_the_file(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting_month(conn)
    file = tmp_path / "categories.yaml"
    file.write_text("# ours\ncategories:\n  entrate: [Prestiti]\n", encoding="utf-8")

    def broken(*_: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store, "seed_categories", broken)
    with pytest.raises(sqlite3.OperationalError):
        staging.add_category(conn, file, name="Animali", side="uscite", group="Casa")
    assert file.read_text(encoding="utf-8") == "# ours\ncategories:\n  entrate: [Prestiti]\n"


# ------------------------------------------------- statements that reach into committed days


def year_so_far(*extra: Any, first: str = "-10") -> Batch:
    """An export of July to September: July as `migros` above stores it, then two months."""
    found = [
        txn(first, date=date(2026, 7, 10), source_ref="1"),
        txn("-5", date=date(2026, 7, 10), source_ref="2"),
        txn("-20", date=date(2026, 8, 10), source_ref="3"),
        txn("-30", date=date(2026, 9, 10), source_ref="4"),
        *extra,
    ]
    closing = Decimal(100) + sum((row.amount for row in found), Decimal(0))
    balance = CashBalance("Migros", "CHF", Decimal(100), closing)
    last = date(2026, 9, 30)
    export = statement("migros", found, [balance], month=JULY, end=last, content="year")
    return batch(export, month=JULY, end=last)


def committed_july(conn: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    staging.stage(conn, batch(migros("100", "-10", "-5", month=JULY), month=JULY), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    return dump(conn)


def of_july(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return rows(conn, "SELECT * FROM transactions WHERE booking_date < '2026-08' ORDER BY id")


def test_a_statement_that_reaches_into_committed_days_stages_only_the_rest(
    conn: sqlite3.Connection,
) -> None:
    before = committed_july(conn)
    staged = staging.stage(conn, year_so_far(), LATER)
    assert (staged.transactions, staged.already, staged.differences) == (2, 2, ())
    assert len(staged.files) == 1
    assert staged.gaps == ()  # July's closing balance leads to the new one
    assert of_july(conn) == before["transactions"]
    assert rows(conn, "SELECT committed_at IS NOT NULL FROM statement_files ORDER BY id") == [
        (1,),
        (0,),
    ]
    assert rows(conn, "SELECT period_start, period_end FROM statement_files ORDER BY 1") == [
        ("2026-07-01", "2026-07-31"),
        ("2026-08-01", "2026-09-30"),  # the days the export adds
    ]
    assert rows(conn, "SELECT date, amount_nano FROM balances ORDER BY 1") == [
        ("2026-06-30", 100_000_000_000),
        ("2026-07-31", 85_000_000_000),
        ("2026-09-30", 35_000_000_000),
    ]
    assert staging.stage(conn, year_so_far(), LATER) == staged  # and again: the same
    assert len(staging.commit(conn, owner=OWNER, now=LATER)) == 1
    coverage = rows(conn, "SELECT count(*) FROM committed_transactions")
    assert coverage == [(4,)]


def test_rows_that_disagree_with_committed_days_stop_an_import_whose_balances_show_it(
    conn: sqlite3.Connection,
) -> None:
    before = committed_july(conn)
    with pytest.raises(ChainMismatchError, match="off by 1"):
        staging.stage(conn, year_so_far(first="-11"), LATER)
    assert dump(conn) == before


def broker(*found: Any, month: date = JULY, end: date | None = None) -> Batch:
    """A statement without balances, as a broker's list of transactions is."""
    listed = [replace(row, source_file="ytd.csv") for row in found]
    export = statement("tastytrade", listed, month=month, end=end, content=str(found))
    return batch(export, month=month, end=end)


def test_rows_that_disagree_with_committed_days_are_said_and_change_nothing(
    conn: sqlite3.Connection,
) -> None:
    early = txn("-7", date=date(2026, 7, 3), source_ref="0", description="FEE")
    last = txn("-5", date=date(2026, 7, 31), source_ref="1", description="FEE")
    staging.stage(conn, broker(early, last), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    before = of_july(conn)

    august = txn("-9", date=date(2026, 8, 4), source_ref="2")
    # The export begins on 31 July: what was booked before that day is not its business.
    same = staging.stage(conn, broker(last, august, end=date(2026, 8, 31)), LATER)
    assert (same.transactions, same.already, same.differences) == (1, 1, ())

    other = replace(last, amount=Decimal(-6))
    staged = staging.stage(conn, broker(other, august, end=date(2026, 8, 31)), LATER)
    assert staged.differences == (
        "Migros 2026-07-31: FEE (-6 CHF) is in ytd.csv and is not among the committed rows",
        "Migros 2026-07-31: FEE is committed and is not in ytd.csv",
    )
    assert (staged.transactions, staged.already) == (1, 0)
    assert of_july(conn) == before


def test_a_statement_of_committed_days_alone_stores_nothing(conn: sqlite3.Connection) -> None:
    before = committed_july(conn)
    again = statement("migros", [txn("-10", date=date(2026, 7, 10))], month=JULY, content="x")
    staged = staging.stage(conn, batch(again, month=JULY), LATER)
    assert dump(conn) == before
    assert (staged.files, staged.transactions, staged.nothing_new) == ((), 0, ("migros.csv",))
    (difference,) = staged.differences  # the second row of July is not in this file
    assert difference == "Migros 2026-07-10: KONSUM is committed and is not in migros.csv"


def moved_july(conn: sqlite3.Connection) -> None:
    """July committed with its rows booked three days after they happened, as an older
    import booked them."""
    rows_of = [
        txn("-10", date=date(2026, 7, 10), booking_date=date(2026, 7, 13), source_ref="1"),
        txn("-5", date=date(2026, 7, 10), booking_date=date(2026, 7, 13), source_ref="2"),
        txn("-5", date=date(2026, 7, 10), booking_date=date(2026, 7, 13), source_ref="3"),
    ]
    balance = CashBalance("Migros", "CHF", Decimal(100), Decimal(80))
    july = statement("migros", rows_of, [balance], month=JULY, content="old")
    staging.stage(conn, batch(july, month=JULY), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)


def re_export(*amounts: str, closing: str = "60") -> Batch:
    """July to August, the July rows booked on the day they happened; the days of July are
    committed, so it closes on what follows from July's closing balance, 80, and August."""
    found = [
        txn(a, date=date(2026, 7, 10), source_ref=str(n)) for n, a in enumerate(amounts, start=1)
    ]
    found.append(txn("-20", date=date(2026, 8, 10), source_ref="9"))
    balance = CashBalance("Migros", "CHF", Decimal(100), Decimal(closing))
    last = date(2026, 8, 31)
    return batch(
        statement("migros", found, [balance], month=JULY, end=last, content="new"),
        month=JULY,
        end=last,
    )


def test_a_row_an_older_import_booked_on_another_day_is_the_same_row(
    conn: sqlite3.Connection,
) -> None:
    moved_july(conn)
    before = of_july(conn)
    staged = staging.stage(conn, re_export("-10", "-5", "-5"), LATER)
    assert (staged.transactions, staged.already, staged.differences) == (1, 3, ())
    assert of_july(conn) == before


def test_rows_booked_on_another_day_are_matched_one_to_one(conn: sqlite3.Connection) -> None:
    moved_july(conn)
    # Two of the three identical rows are there, the third is missing; a new one is added.
    new = txn("-3", date=date(2026, 7, 11), source_ref="4", description="OTHER")
    found = [
        txn("-10", date=date(2026, 7, 10), source_ref="1"),
        txn("-5", date=date(2026, 7, 10), source_ref="2"),
        txn("-5", date=date(2026, 7, 10), source_ref="2b"),
        txn("-5", date=date(2026, 7, 10), source_ref="2c"),
        new,
    ]
    balance = CashBalance("Migros", "CHF", Decimal(100), Decimal(80))
    export = statement("migros", found, [balance], month=JULY, end=date(2026, 8, 31), content="n")
    staged = staging.stage(conn, batch(export, month=JULY, end=date(2026, 8, 31)), LATER)
    # four of the five match stored rows (a third "-5" has none left), "OTHER" is not stored
    assert staged.already == 3
    assert staged.differences == (
        "Migros 2026-07-10: KONSUM (-5 CHF) is in migros.csv and is not among the committed rows",
        "Migros 2026-07-11: OTHER (-3 CHF) is in migros.csv and is not among the committed rows",
    )

    fewer = re_export("-10", "-5", closing="60")  # and the other way: a stored row it does not have
    again = staging.stage(conn, fewer, LATER)
    assert again.already == 2
    assert again.differences == ("Migros 2026-07-13: KONSUM is committed and is not in migros.csv",)


@pytest.mark.parametrize(("booked", "same"), [(date(2026, 7, 3), True), (date(2026, 7, 2), False)])
def test_a_row_is_looked_for_a_week_before_the_first_day_of_the_statement(
    conn: sqlite3.Connection, booked: date, same: bool
) -> None:
    stored = txn("-10", date=date(2026, 7, 1), booking_date=booked, source_ref="1")
    balance = CashBalance("Migros", "CHF", Decimal(100), Decimal(90))
    staging.stage(
        conn, batch(statement("migros", [stored], [balance], month=JULY), month=JULY), NOW
    )
    staging.commit(conn, owner=OWNER, now=NOW)
    export = statement(
        "migros",
        [
            txn("-10", date=date(2026, 7, 1), booking_date=date(2026, 7, 10), source_ref="1"),
            txn("-1", date=date(2026, 8, 3)),
        ],
        [CashBalance("Migros", "CHF", Decimal(100), Decimal(89))],
        month=JULY,
        end=date(2026, 8, 31),
        content="n",
    )
    staged = staging.stage(conn, batch(export, month=JULY, end=date(2026, 8, 31)), LATER)
    assert (staged.already, len(staged.differences)) == ((1, 0) if same else (0, 1))


# ------------------------------------------------------ days that are not over yet

OCTOBER_FIRST = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)  # 7 October is the last day over


def october(*, holdings: bool = False, content: str = "october") -> Batch:
    """An October statement whose rows are on the 3rd, 9th and 20th, opening on 100."""
    found = [
        txn("-10", date=date(2026, 10, 3), source_ref="1"),
        txn("-20", date=date(2026, 10, 9), source_ref="2"),
        txn("-5", date=date(2026, 10, 20), source_ref="3"),
    ]
    balance = CashBalance("Migros", "CHF", Decimal(100), Decimal(65))
    export = statement("migros", found, [balance], month=OCTOBER, content=content)
    extraction = replace(export.extraction, holdings=() if holdings else None)
    return batch(replace(export, extraction=extraction), month=OCTOBER)


def test_days_that_are_not_over_are_left_for_the_next_import(conn: sqlite3.Connection) -> None:
    staged = staging.stage(conn, october(), OCTOBER_FIRST)
    assert (staged.transactions, staged.later) == (1, 2)
    assert rows(conn, "SELECT date, amount_nano FROM balances ORDER BY 1") == [
        ("2026-09-30", 100_000_000_000),
        ("2026-10-07", 90_000_000_000),  # moved back by what was left out
    ]
    assert rows(conn, "SELECT period_start, period_end FROM statement_files") == [
        ("2026-10-01", "2026-10-07")
    ]
    staging.commit(conn, owner=OWNER, now=OCTOBER_FIRST)

    rest = staging.stage(conn, october(content="whole"), NOW)  # exported again, October over
    assert (rest.transactions, rest.already, rest.later, rest.differences) == (2, 1, 0, ())
    assert rows(conn, "SELECT period_start, period_end FROM statement_files ORDER BY 1") == [
        ("2026-10-01", "2026-10-07"),
        ("2026-10-08", "2026-10-31"),
    ]
    assert rows(conn, "SELECT date, amount_nano FROM balances ORDER BY 1") == [
        ("2026-09-30", 100_000_000_000),
        ("2026-10-07", 90_000_000_000),
        ("2026-10-31", 65_000_000_000),
    ]
    assert len(staging.commit(conn, owner=OWNER, now=NOW)) == 1


def test_the_same_file_committed_before_its_days_were_over_must_be_downloaded_again(
    conn: sqlite3.Connection,
) -> None:
    staging.stage(conn, october(), OCTOBER_FIRST)
    staging.commit(conn, owner=OWNER, now=OCTOBER_FIRST)
    before = dump(conn)
    with pytest.raises(staging.CommittedError, match="download it again"):
        staging.stage(conn, october(), NOW)  # the very same bytes, October now over
    assert dump(conn) == before


def test_a_statement_not_yet_over_replaces_the_staged_part_of_itself(
    conn: sqlite3.Connection,
) -> None:
    staging.stage(conn, october(), OCTOBER_FIRST)
    staged = staging.stage(conn, october(content="whole"), NOW)
    assert (staged.transactions, staged.later) == (3, 0)
    # the rows are the new file's; the shorter file keeps the balance it closed on, as a
    # shorter export does when a longer one replaces it
    assert rows(conn, "SELECT count(*) FROM transactions") == [(3,)]
    assert rows(conn, "SELECT period_end FROM statement_files ORDER BY id") == [
        ("2026-10-07",),
        ("2026-10-31",),
    ]


def test_a_statement_that_begins_today_has_nothing_to_stage_yet(conn: sqlite3.Connection) -> None:
    coming = txn("-5", date=date(2026, 10, 20))
    export = statement("migros", [coming], month=OCTOBER)
    extraction = replace(export.extraction, start=date(2026, 10, 20))
    staged = staging.stage(conn, batch(replace(export, extraction=extraction)), OCTOBER_FIRST)
    assert (staged.files, staged.transactions, staged.later) == ((), 0, 1)
    assert staged.nothing_new == ("migros.csv",)
    assert rows(conn, "SELECT count(*) FROM statement_files") == [(0,)]


def test_a_statement_of_holdings_that_runs_into_today_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(staging.StagingError, match=r"not over yet.*export it up to 2026-10-07"):
        staging.stage(conn, october(holdings=True), OCTOBER_FIRST)
    assert rows(conn, "SELECT count(*) FROM statement_files") == [(0,)]
    assert staging.stage(conn, october(holdings=True), NOW).transactions == 3


# ------------------------------------------------ before what is committed, and after


def test_a_statement_older_than_what_is_committed_is_staged_whole_and_chained(
    conn: sqlite3.Connection,
) -> None:
    staging.stage(conn, batch(migros("90", "-10")), NOW)  # September: 90 -> 80
    staging.commit(conn, owner=OWNER, now=NOW)
    older = staging.stage(conn, batch(migros("100", "-10", month=JULY), month=JULY), LATER)
    assert (older.transactions, older.already, older.differences) == (1, 0, ())
    assert older.gaps == ()
    assert [(c.committed, c.staged) for c in months.coverage(conn, OWNER)] == [
        (date(2026, 9, 30), None)
    ]


def test_a_statement_that_starts_before_what_is_committed_and_reaches_into_it_is_refused(
    conn: sqlite3.Connection,
) -> None:
    staging.stage(conn, month_of(AUGUST, "100", "-10"), NOW)
    staging.stage(conn, batch(migros("90", "-10")), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    before = dump(conn)
    reaching = statement(
        "migros", [txn("-1", date=date(2026, 7, 10))], month=JULY, end=date(2026, 8, 31)
    )
    with pytest.raises(
        staging.CommittedError, match=r"before what is committed of it.*up to 2026-07-31"
    ):
        staging.stage(conn, batch(reaching, month=JULY, end=date(2026, 8, 31)), LATER)
    assert dump(conn) == before


# ---------------------------------------------------------------- taking back, discarding


def three_months(conn: sqlite3.Connection) -> list[int]:
    ids = []
    for month, opening in ((JULY, "100"), (AUGUST, "90"), (SEPTEMBER, "80")):
        staging.stage(conn, month_of(month, opening, "-10"), NOW)
        ids += [f.id for f in staging.commit(conn, owner=OWNER, now=NOW)]
    return ids


def test_a_committed_statement_taken_back_is_staged_again(conn: sqlite3.Connection) -> None:
    july, august, september = three_months(conn)
    taken = staging.uncommit(conn, owner=OWNER, file_id=september)
    assert (taken.id, taken.committed_at) == (september, "2026-12-20T12:00:00+00:00")
    assert rows(conn, "SELECT committed_at FROM statement_files WHERE id = ?", september) == [
        (None,)
    ]
    assert rows(conn, "SELECT DISTINCT substr(booking_date, 1, 7) FROM committed_transactions") == [
        ("2026-07",),
        ("2026-08",),
    ]
    assert [(c.committed, c.staged) for c in months.coverage(conn, OWNER)] == [
        (date(2026, 8, 31), date(2026, 9, 30))
    ]
    staging.uncommit(conn, owner=OWNER, file_id=august)
    staging.uncommit(conn, owner=OWNER, file_id=july)
    assert rows(conn, "SELECT count(*) FROM committed_transactions") == [(0,)]


def test_a_statement_between_committed_ones_cannot_be_taken_back(conn: sqlite3.Connection) -> None:
    july, august, _ = three_months(conn)
    before = dump(conn)
    with pytest.raises(
        staging.CommittedError, match=r"between other committed.*take back the later"
    ):
        staging.uncommit(conn, owner=OWNER, file_id=august)
    assert dump(conn) == before
    staging.uncommit(conn, owner=OWNER, file_id=july)  # the first of them is at an end


def test_only_a_committed_statement_can_be_taken_back(conn: sqlite3.Connection) -> None:
    staging.stage(conn, month_of(SEPTEMBER, "100", "-10"), NOW)
    with pytest.raises(staging.StagingError, match=r"migros\.csv is not committed"):
        staging.uncommit(conn, owner=OWNER, file_id=1)


def test_a_staged_statement_is_discarded_with_everything_it_gave(conn: sqlite3.Connection) -> None:
    staging.stage(conn, tastytrade(bill("-4950", "5000", date(2026, 8, 3)), month=AUGUST), NOW)
    sale = tastytrade(bill("4980", "-5000", date(2026, 9, 2)))
    staging.stage(conn, sale, NOW)
    assert rows(conn, "SELECT count(*) FROM transactions WHERE split_of IS NOT NULL") == [(1,)]
    owner = store.owner_id(conn, OWNER)
    assert owner is not None
    (purchase, _) = store.statement_files(conn, owner, staged=True)

    discarded = staging.discard(conn, owner=OWNER, file_id=purchase.id)
    assert discarded.filename == "tastytrade 2026-08.csv"
    assert rows(conn, "SELECT count(*) FROM trades") == [(1,)]
    assert rows(conn, "SELECT count(*) FROM balances WHERE statement_file_id = ?", purchase.id) == [
        (0,)
    ]
    # without the purchase the interest cannot be split off the sale any more
    assert rows(conn, "SELECT count(*) FROM transactions WHERE split_of IS NOT NULL") == [(0,)]
    assert [type(gap) for gap in staging.would_refuse(conn, owner)] == [MissingLotsError]
    assert [f.filename for f in store.statement_files(conn, owner)] == ["tastytrade 2026-09.csv"]


def test_a_committed_statement_cannot_be_discarded(conn: sqlite3.Connection) -> None:
    ((file_id),) = [
        f.id
        for f in (
            staging.stage(conn, month_of(SEPTEMBER, "1", "-1"), NOW),
            staging.commit(conn, owner=OWNER, now=NOW),
        )[1]
    ]
    before = dump(conn)
    with pytest.raises(staging.CommittedError, match="is committed: take it back first"):
        staging.discard(conn, owner=OWNER, file_id=file_id)
    assert dump(conn) == before
    staging.uncommit(conn, owner=OWNER, file_id=file_id)
    staging.discard(conn, owner=OWNER, file_id=file_id)
    assert rows(conn, "SELECT count(*) FROM statement_files") == [(0,)]


# ------------------------------------------------------- statements of another person

IBAN = "CH9300762011623852957"


def numbered(owner: str, number: str, content: str = "") -> Batch:
    export = migros("0", "-1")
    extraction = replace(export.extraction, account_id=number)
    return batch(replace(export, extraction=extraction, sha256=content + number), owner=owner)


def test_a_statement_of_an_account_imported_for_the_other_person_is_refused(
    conn: sqlite3.Connection,
) -> None:
    staging.stage(conn, numbered(PARTNER, IBAN), NOW)
    before = dump(conn)
    with pytest.raises(staging.OtherOwnerError, match=f"is of {PARTNER}'s account {IBAN}"):
        staging.stage(conn, numbered(OWNER, IBAN, "x"), NOW)
    assert dump(conn) == before
    staging.stage(conn, numbered(PARTNER, IBAN, "again"), LATER)  # hers again is fine


def test_a_statement_whose_account_number_names_the_other_person_is_refused(
    conn: sqlite3.Connection,
) -> None:
    people = {OWNER: [re.compile("CH11")], PARTNER: [re.compile(IBAN)]}
    mine = replace(numbered(OWNER, IBAN), people=people)
    with pytest.raises(staging.OtherOwnerError, match="not of Lukas's"):
        staging.stage(conn, mine, NOW)
    assert rows(conn, "SELECT count(*) FROM owners") == [(0,)]
    # the owner's own patterns settle it first, and a number nobody names is anybody's
    both = {OWNER: [re.compile(IBAN)], PARTNER: [re.compile(IBAN)]}
    assert staging.stage(conn, replace(numbered(OWNER, IBAN), people=both), NOW).transactions == 1
    nobody = replace(numbered(OWNER, "CH0000", "n"), people={PARTNER: [re.compile(IBAN)]})
    assert staging.stage(conn, nobody, NOW).transactions == 1
    no_number = staging.stage(conn, batch(migros("0", "-1"), owner=OWNER), LATER)
    assert no_number.transactions == 1


def test_a_statement_of_two_accounts_stages_the_one_that_has_days_left(
    conn: sqlite3.Connection,
) -> None:
    chf = txn("-1", account="Revolut CHF", source_file="a.tsv", date=date(2026, 7, 5))
    first = statement(
        "revolut",
        [chf],
        [CashBalance("Revolut CHF", "CHF", Decimal(10), Decimal(9))],
        month=JULY,
        content="a",
    )
    staging.stage(conn, batch(first, month=JULY), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)

    both = [
        replace(chf, source_file="b.tsv"),
        txn(
            "-2", account="Revolut EUR", currency="EUR", source_file="b.tsv", date=date(2026, 7, 6)
        ),
    ]
    balances = [
        CashBalance("Revolut CHF", "CHF", Decimal(10), Decimal(9)),
        CashBalance("Revolut EUR", "EUR", Decimal(5), Decimal(3)),
    ]
    second = statement("revolut", both, balances, month=JULY, content="b")
    staged = staging.stage(conn, batch(second, month=JULY), LATER)
    assert (staged.transactions, staged.already, staged.differences) == (1, 1, ())
    assert rows(conn, "SELECT count(*) FROM statement_accounts WHERE statement_file_id = 2") == [
        (1,)
    ]  # the account that has days left; CHF is the first file's
    assert rows(
        conn,
        "SELECT a.name, b.date FROM balances b JOIN accounts a ON a.id = b.account_id "
        "WHERE b.statement_file_id = 2 ORDER BY 1, 2",
    ) == [("Revolut EUR", "2026-06-30"), ("Revolut EUR", "2026-07-31")]


def test_holdings_of_an_account_with_no_days_left_are_not_stored_again(
    conn: sqlite3.Connection,
) -> None:
    usd = CashBalance("IBKR USD", "USD", Decimal(0), Decimal(0))
    first = statement("ibkr", [], [usd], filename="a.csv", month=JULY, content="a")
    first = replace(first, extraction=replace(first.extraction, holdings=()))
    staging.stage(conn, batch(first, month=JULY), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)

    eur = CashBalance("IBKR EUR", "EUR", Decimal(0), Decimal(0))
    printed = (
        Holding("IBKR USD", "WIDG", "USD", Decimal(3), Decimal(50), Decimal(150), "ETFs"),
        Holding("IBKR EUR", "GADG", "EUR", Decimal(2), Decimal(50), Decimal(100), "ETFs"),
    )
    second = statement("ibkr", [], [usd, eur], filename="b.csv", month=JULY, content="b")
    second = replace(second, extraction=replace(second.extraction, holdings=printed))
    staged = staging.stage(conn, batch(second, month=JULY), LATER)
    assert len(staged.files) == 1
    held = rows(
        conn,
        "SELECT a.name FROM positions p JOIN accounts a ON a.id = p.account_id ORDER BY 1",
    )
    assert held == [("IBKR EUR",)]
