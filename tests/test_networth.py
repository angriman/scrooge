"""Net worth day by day: cash, holdings at the close of the day, at the rate of the day."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from scrooge import networth, query, staging
from scrooge.models import (
    CashBalance,
    ControlTotal,
    Holding,
    Kind,
    PrintedValue,
    Rate,
    SecurityTrade,
    Transaction,
)
from scrooge.networth import (
    BILLS,
    CARD,
    CASH,
    ETFS,
    PENSION,
    PILLAR2,
    STOCKS,
    MissingRateError,
)
from scrooge.pipeline import Statement
from scrooge.storage import db, store
from tests.storage.helpers import (
    AUGUST,
    OCTOBER,
    OWNER,
    PARTNER,
    SEPTEMBER,
    batch,
    certificate,
    certified,
    statement,
    trade,
    txn,
)

NOW = datetime(2026, 12, 20, 12, 0, tzinfo=UTC)  # after every day the tests cover
D = Decimal


def day(n: int, month: int = 9) -> date:
    return date(2026, month, n)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    db.migrate(connection, NOW)
    yield connection
    connection.close()


def account(
    source: str,
    name: str,
    currency: str,
    opening: str,
    *rows: Transaction,
    month: date = SEPTEMBER,
    filename: str = "",
) -> Statement:
    """A statement of one account whose closing balance follows from its rows."""
    closing = D(opening) + sum((row.amount for row in rows), D(0))
    balance = CashBalance(name, currency, D(opening), closing)
    file = filename or f"{name} {month:%Y-%m}.csv"
    rows = tuple(replace(row, source_file=file) for row in rows)
    return statement(source, rows, [balance], filename=file, month=month)


def close(
    conn: sqlite3.Connection, *statements: Statement, owner: str = OWNER, month: date = SEPTEMBER
) -> None:
    """Stage the statements of one month and commit it."""
    staging.stage(conn, batch(*statements, owner=owner, month=month), NOW)
    staging.commit(conn, owner=owner, now=NOW)


def spent(amount: str, on: date, **fields: object) -> Transaction:
    return txn(amount, date=on, source_ref=f"{on}{amount}", **fields)


def bought(ticker: str, quantity: str, amount: str, on: date, **fields: object) -> SecurityTrade:
    quantity_, amount_ = D(quantity), D(amount)
    fields.setdefault("price", abs(amount_ / quantity_) if quantity_ else D(0))
    return trade(amount, ticker, quantity, date=on, source_ref=f"{ticker}{on}{quantity}", **fields)


def price(
    conn: sqlite3.Connection, symbol: str, on: date, close_: str, source: str = "fetched:x"
) -> None:
    instrument = conn.execute("SELECT id FROM instruments WHERE symbol = ?", (symbol,)).fetchone()[
        0
    ]
    store.set_price(conn, instrument, on, D(close_), source=source)


def rate(
    conn: sqlite3.Connection, currency: str, on: date, rate_: str, source: str = "fetched:x"
) -> None:
    store.set_rate(conn, on, currency, D(rate_), source=source)


def printed(
    conn: sqlite3.Connection, symbol: str, on: date, close_: str, file: str = "IBKR USD 2026-09.csv"
) -> None:
    """A committed statement prints the close of a share it lists a position of."""
    [account_id, file_id] = conn.execute(
        "SELECT a.id, f.id FROM accounts a, statement_files f "
        "WHERE a.name = 'IBKR USD' AND f.filename = ?",
        (file,),
    ).fetchone()
    held = Holding("IBKR USD", symbol, "USD", D(10), D(close_), D(10) * D(close_), "Stocks")
    store.set_holding(conn, account_id, on, held, file_id)


def printed_rate(
    conn: sqlite3.Connection,
    currency: str,
    on: date,
    rate_: str,
    file: str = "IBKR USD 2026-09.csv",
) -> None:
    """A committed statement prints the rate of a currency."""
    [file_id] = conn.execute(
        "SELECT id FROM statement_files WHERE filename = ?", (file,)
    ).fetchone()
    store.set_statement_rates(conn, file_id, on, {currency: D(rate_)})


def line(result: networth.NetWorth, asset: str, owner: str = OWNER) -> dict[date, Decimal]:
    [found] = [x for x in result.lines if (x.owner, x.asset) == (owner, asset)]
    return dict(zip(result.days, found.values, strict=True))


# ------------------------------------------------------------------------------- nothing


def test_nothing_committed_is_no_net_worth(conn: sqlite3.Connection) -> None:
    assert networth.run(conn) == networth.NetWorth("EUR", (), (), ())
    assert networth.run(conn).total == ()
    assert networth.checks(conn) == ()
    staging.stage(conn, batch(account("migros", "Migros", "CHF", "1000")), NOW)
    assert networth.run(conn, currency="CHF").days == ()  # staged is not committed


def test_a_person_with_nothing_committed_is_no_net_worth(conn: sqlite3.Connection) -> None:
    store.ensure_owner(conn, OWNER)
    assert networth.run(conn, currency="CHF").days == ()


def test_a_committed_statement_with_no_figure_in_it_is_no_net_worth(
    conn: sqlite3.Connection,
) -> None:
    empty = statement("tastytrade", [], filename="empty.csv")
    controls = (ControlTotal("Tastytrade", "USD", D(0)),)
    empty = replace(empty, extraction=replace(empty.extraction, controls=controls))
    staging.stage(conn, batch(empty), NOW)
    assert len(staging.commit(conn, owner=OWNER, now=NOW)) == 1
    assert networth.run(conn, currency="CHF").days == ()


def test_net_worth_ends_on_the_last_day_committed_and_for_everyone_on_the_latest(
    conn: sqlite3.Connection,
) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000"))
    staging.stage(conn, certified(certificate(day(15), "500"), owner=PARTNER), NOW)
    staging.commit(conn, owner=PARTNER, now=NOW)
    later = batch(account("migros", "Migros", "CHF", "1000", month=OCTOBER), month=OCTOBER)
    staging.stage(conn, later, NOW)  # October is staged, not committed
    assert networth.run(conn, currency="CHF", owner=OWNER).days[-1] == day(30)
    assert networth.run(conn, currency="CHF", owner=PARTNER).days == (day(15),)
    everyone = networth.run(conn, currency="CHF")
    assert everyone.days[-1] == day(30)
    assert line(everyone, PILLAR2, PARTNER)[day(30)] == D(500)  # carried from her last day


def test_an_unknown_person_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(query.QueryError, match="Nobody"):
        networth.run(conn, owner="Nobody")


# ---------------------------------------------------------------------------------- cash


def test_cash_starts_with_the_first_statement_and_follows_its_rows(
    conn: sqlite3.Connection,
) -> None:
    rows = [
        spent("-200", day(10)),
        spent("-50.25", day(10)),
        spent("3000", day(25), kind=Kind.INCOME, category="Stipendio"),
    ]
    close(conn, account("migros", "Migros", "CHF", "1000", *rows))
    result = networth.run(conn, currency="CHF")
    assert (result.days[0], result.days[-1]) == (day(31, 8), day(30))
    assert len(result.days) == 31
    cash = line(result, CASH)
    assert [cash[d] for d in (day(31, 8), day(9), day(10), day(24), day(25), day(30))] == [
        D(1000),
        D(1000),
        D("749.75"),
        D("749.75"),
        D("3749.75"),
        D("3749.75"),
    ]
    assert result.total == tuple(cash.values())
    assert result.notes == ()


def test_only_committed_months_count(conn: sqlite3.Connection) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000", spent("-200", day(10))))
    october = account("migros", "Migros", "CHF", "800", spent("-100", day(5, 10)), month=OCTOBER)
    staging.stage(conn, batch(october, month=OCTOBER), NOW)
    result = networth.run(conn, currency="CHF")
    assert result.days[-1] == day(30)
    assert result.total[-1] == 800
    staging.commit(conn, owner=OWNER, now=NOW)
    result = networth.run(conn, currency="CHF")
    assert (result.days[-1], result.total[-1]) == (day(31, 10), D(700))


def test_an_account_whose_first_statement_is_not_committed_does_not_count_yet(
    conn: sqlite3.Connection,
) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000"))
    # Its statement opens on the last day of September, which is committed: October is not.
    staged = account("revolut", "Revolut CHF", "CHF", "500", month=OCTOBER)
    staging.stage(conn, batch(staged, month=OCTOBER), NOW)
    assert networth.run(conn, currency="CHF").total[-1] == 1000
    staging.commit(conn, owner=OWNER, now=NOW)
    result = networth.run(conn, currency="CHF")
    assert [result.total[result.days.index(d)] for d in (day(29), day(30))] == [D(1000), D(1500)]


def test_an_account_counts_for_nothing_before_its_first_statement(
    conn: sqlite3.Connection,
) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000", month=AUGUST), month=AUGUST)
    close(
        conn,
        account("migros", "Migros", "CHF", "1000"),
        account("revolut", "Revolut CHF", "CHF", "500"),
    )
    cash = line(networth.run(conn, currency="CHF"), CASH)
    assert [cash[d] for d in (day(31, 7), day(30, 8), day(31, 8), day(30))] == [
        D(1000),
        D(1000),
        D(1500),
        D(1500),
    ]


def test_money_set_aside_inside_an_account_stays_part_of_it(conn: sqlite3.Connection) -> None:
    # D8: the savings are part of the Revolut account. Its statement shows 400 leaving for
    # them, and the savings report shows what they earn.
    moved = spent(
        "-400", day(5), account="Revolut EUR", currency="EUR", kind=Kind.INTERNAL, category=""
    )
    paid = spent("-100", day(8), account="Revolut EUR", currency="EUR")
    current = account("revolut", "Revolut EUR", "EUR", "1000", moved, paid)
    earned = spent(
        "0.5",
        day(20),
        account="Revolut EUR",
        currency="EUR",
        kind=Kind.INTEREST,
        category="Interessi",
        source_file="savings.tsv",
    )
    savings = statement("revolut_savings", [earned], filename="savings.tsv")
    close(conn, current, savings)
    cash = line(networth.run(conn, currency="EUR"), CASH)
    assert [cash[d] for d in (day(4), day(5), day(8), day(19), day(20), day(30))] == [
        D(1000),
        D(1000),
        D(900),
        D(900),
        D("900.5"),
        D("900.5"),
    ]


def test_an_internal_row_of_an_account_nothing_else_feeds_is_cash_leaving(
    conn: sqlite3.Connection,
) -> None:
    # The card debit on the bank: internal, and the money is gone from the bank.
    debit = spent("-300", day(5), kind=Kind.INTERNAL, category="")
    payment = spent("300", day(6), account="Swisscard", kind=Kind.INTERNAL, category="")
    purchase = spent("-120", day(12), account="Swisscard")
    close(
        conn,
        account("migros", "Migros", "CHF", "1000", debit),
        account("swisscard", "Swisscard", "CHF", "-300", payment, purchase),
    )
    result = networth.run(conn, currency="CHF")
    assert line(result, CASH)[day(5)] == 700
    card = line(result, CARD)
    assert [card[d] for d in (day(31, 8), day(6), day(12), day(30))] == [
        D(-300),
        D(0),
        D(-120),
        D(-120),
    ]
    assert result.total[-1] == 580
    assert [x.asset for x in result.lines] == [CASH, CARD]


def test_an_account_that_is_only_typed_is_carried_flat_and_says_so(
    conn: sqlite3.Connection,
) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000"))
    for on, amount in ((day(5), "250"), (day(20), "300")):
        staging.type_balance(
            conn, owner=OWNER, account="Old bank", day=on, amount=D(amount), currency="CHF"
        )
    result = networth.run(conn, currency="CHF")
    assert [
        result.total[result.days.index(d)] for d in (day(4), day(5), day(19), day(20), day(30))
    ] == [
        D(1000),
        D(1250),
        D(1250),
        D(1300),
        D(1300),
    ]
    assert result.notes == (
        "Old bank of Lukas has no statements: it is carried at what was typed, last for "
        "2026-09-20.",
    )


def test_a_pension_fund_is_a_line_of_its_own_from_the_day_of_its_certificate(
    conn: sqlite3.Connection,
) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000"))
    before = networth.run(conn, currency="CHF")
    staging.stage(conn, certified(certificate(day(5), "35802.00")), NOW)
    assert networth.run(conn, currency="CHF") == before  # staged is not committed
    staging.stage(conn, certified(certificate(day(20), "36000.50")), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)

    result = networth.run(conn, currency="CHF")
    assert result.days == before.days
    assert [x.asset for x in result.lines] == [CASH, PILLAR2]
    assert set(line(result, CASH).values()) == {D(1000)}
    fund = line(result, PILLAR2)
    assert [fund[d] for d in (day(1), day(4), day(5), day(19), day(20), day(30))] == [
        D(0),
        D(0),
        D("35802.00"),
        D("35802.00"),
        D("36000.50"),
        D("36000.50"),
    ]
    assert result.notes == (
        "Pillar 2 AXA of Lukas is carried at what its certificates state, the last one for "
        "2026-09-20.",
    )
    assert networth.checks(conn) == ()


def test_a_certificate_after_the_statements_is_no_day_they_cover(conn: sqlite3.Connection) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000"))
    staging.stage(conn, certified(certificate(day(10, 10), "36000.50")), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    valued = networth.value(conn, OWNER)
    assert valued is not None
    assert valued.through == {OWNER: day(30)}  # what the statements cover
    assert valued.days[-1] == day(10, 10)  # the fund's own day is still shown
    assert line(networth.run(conn, currency="CHF"), PILLAR2)[day(10, 10)] == D("36000.50")
    assert networth.checks(conn) == ()


def test_a_person_with_certificates_alone_counts_up_to_the_last_of_them(
    conn: sqlite3.Connection,
) -> None:
    staging.stage(conn, certified(certificate(day(5), "100"), certificate(day(9), "200")), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    valued = networth.value(conn, OWNER)
    assert valued is not None
    assert valued.through == {OWNER: day(9)}


def test_a_pension_fund_is_converted_like_any_cash_in_francs(conn: sqlite3.Connection) -> None:
    rate(conn, "EUR", day(1), "0.95")
    rate(conn, "EUR", day(15), "0.94")
    staging.stage(conn, certified(certificate(day(10), "9400"), certificate(day(15), "9400")), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    result = networth.run(conn)
    assert (result.days[0], result.days[-1]) == (day(10), day(15))  # nothing is known outside
    fund = line(result, PILLAR2)
    assert (fund[day(10)], fund[day(15)]) == (D("9894.74"), D("10000.00"))


def test_the_pension_fund_of_one_person_is_not_the_others(conn: sqlite3.Connection) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000"))
    close(conn, account("migros", "Migros", "CHF", "500"), owner=PARTNER)
    scan = certificate(day(1), "3579.40", issuer="Servisa")
    staging.stage(conn, certified(scan, owner=PARTNER), NOW)
    staging.commit(conn, owner=PARTNER, now=NOW)
    both = networth.run(conn, currency="CHF")
    assert line(both, PILLAR2, PARTNER)[day(1)] == D("3579.40")
    assert line(both, CASH, PARTNER)[day(1)] == D(500)
    assert PILLAR2 not in {x.asset for x in both.lines if x.owner == OWNER}
    at = both.days.index(day(1))
    assert both.total[at - 1 : at + 1] == (D(1500), D("5079.40"))
    assert networth.run(conn, owner=OWNER, currency="CHF").notes == ()


@settings(deadline=None, max_examples=40)
@given(
    st.dictionaries(st.integers(1, 30), st.decimals(0, 500_000, places=2), min_size=1, max_size=5)
)
def test_between_certificates_the_pension_fund_stands_still(stated: dict[int, Decimal]) -> None:
    conn = db.connect(":memory:")
    db.migrate(conn, NOW)
    close(conn, account("migros", "Migros", "CHF", "1000"))
    without = networth.run(conn, currency="CHF").total
    scans = [certificate(day(n), str(balance)) for n, balance in stated.items()]
    staging.stage(conn, certified(*scans), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    result = networth.run(conn, currency="CHF")
    conn.close()
    for on, total, other in zip(result.days, result.total, without, strict=True):
        known = [stated[at] for at in sorted(stated) if day(at) <= on]
        assert total - other == (known[-1] if known else 0)


def test_cash_without_any_statement_balance_adds_up_from_nothing(conn: sqlite3.Connection) -> None:
    # Tastytrade prints totals and no balances.
    rows = [
        spent(
            "5000",
            day(3),
            account="Tastytrade",
            currency="USD",
            kind=Kind.TRANSFER,
            category="Giroconto",
            source_file="tasty.csv",
        ),
        spent(
            "-120",
            day(9),
            account="Tastytrade",
            currency="USD",
            kind=Kind.TRADING,
            category="Trading",
            source_file="tasty.csv",
        ),
    ]
    close(conn, statement("tastytrade", rows, filename="tasty.csv"))
    result = networth.run(conn, currency="USD")
    cash = line(result, CASH)
    assert (min(cash), cash[day(3)], cash[day(30)]) == (day(3), D(5000), D(4880))
    # its first statement opens at zero, so there is nothing to warn about
    assert result.notes == ()


def test_rows_booked_before_the_first_balance_lead_up_to_it(conn: sqlite3.Connection) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000", spent("-200", day(10))))
    # an earlier balance is dropped: the row of the 10th is then before the first one known
    conn.execute("DELETE FROM balances WHERE date = '2026-08-31'")
    cash = line(networth.run(conn, currency="CHF"), CASH)
    assert (min(cash), cash[day(10)], cash[day(30)]) == (day(10), D(800), D(800))


# -------------------------------------------------------------------------------- shares


def ibkr(*rows: Transaction, opening: str = "10000", month: date = SEPTEMBER) -> Statement:
    return account("ibkr", "IBKR USD", "USD", opening, *rows, month=month)


def test_shares_are_worth_the_last_close_on_or_before_the_day(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15))))
    price(conn, "WIDG", day(11), "49")  # a Friday before it was bought
    price(conn, "WIDG", day(15), "51")
    price(conn, "WIDG", day(18), "53.5")
    result = networth.run(conn, currency="USD")
    etfs = line(result, ETFS)
    assert [etfs[d] for d in (day(14), day(15), day(17), day(18), day(30))] == [
        D(0),
        D(510),
        D(510),
        D(535),
        D(535),
    ]
    assert line(result, CASH)[day(15)] == 9500
    assert result.total[-1] == 10035
    assert result.notes == ()


def test_a_close_typed_or_printed_by_a_statement_counts_before_a_fetched_one(
    conn: sqlite3.Connection,
) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15), asset_tab="Stocks")))
    price(conn, "WIDG", day(20), "60")
    printed(conn, "WIDG", day(20), "61")
    printed(conn, "WIDG", day(25), "70")
    price(conn, "WIDG", day(25), "71")
    price(conn, "WIDG", day(25), "72", "manual")
    stocks = line(networth.run(conn, currency="USD"), STOCKS)
    assert [stocks[d] for d in (day(15), day(20), day(25))] == [D(500), D(610), D(720)]


def _october_printing_widg_at_70() -> Statement:
    """A statement of the next month that prints a close of 70 for WIDG and a rate for USD."""
    base = ibkr(opening="9500", month=OCTOBER)
    printed_ = (Holding("IBKR USD", "WIDG", "USD", D(10), D(70), D(700), "ETFs"),)
    rates = (Rate("USD", "CHF", D("0.9")),)
    return replace(base, extraction=replace(base.extraction, holdings=printed_, rates=rates))


def _september_holding_widg(conn: sqlite3.Connection) -> networth.NetWorth:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15))))
    rate(conn, "USD", day(1), "0.8")
    return networth.run(conn, currency="CHF")


def test_a_staged_statement_moves_neither_the_days_nor_a_close_nor_a_rate(
    conn: sqlite3.Connection,
) -> None:
    before = _september_holding_widg(conn)
    assert before.days[-1] == day(30)
    staging.stage(conn, batch(_october_printing_widg_at_70(), month=OCTOBER), NOW)
    assert networth.run(conn, currency="CHF") == before


def test_a_discarded_statement_leaves_no_close_and_no_rate_behind(
    conn: sqlite3.Connection,
) -> None:
    before = _september_holding_widg(conn)
    staging.stage(conn, batch(_october_printing_widg_at_70(), month=OCTOBER), NOW)
    [staged] = store.statement_files(conn, store.ensure_owner(conn, OWNER), staged=True)
    staging.discard(conn, owner=OWNER, file_id=staged.id)
    assert conn.execute("SELECT count(*) FROM statement_rates").fetchone()[0] == 0
    assert networth.run(conn, currency="CHF") == before


def test_what_a_statement_prints_counts_once_committed_and_not_once_taken_back(
    conn: sqlite3.Connection,
) -> None:
    before = _september_holding_widg(conn)
    staging.stage(conn, batch(_october_printing_widg_at_70(), month=OCTOBER), NOW)
    staging.commit(conn, owner=OWNER, now=NOW)
    after = networth.run(conn, currency="CHF")
    assert after.days[-1] == date(2026, 10, 31)
    # 10 shares at the printed 70 dollars, each dollar at the printed 0.9 francs
    assert line(after, ETFS)[date(2026, 10, 31)] == 630
    assert line(after, ETFS)[day(30)] == line(before, ETFS)[day(30)]
    [file] = store.statement_files(conn, store.ensure_owner(conn, OWNER), staged=False)[-1:]
    staging.uncommit(conn, owner=OWNER, file_id=file.id)
    assert networth.run(conn, currency="CHF") == before


def test_a_trade_another_person_staged_is_no_price_for_the_shares_someone_holds(
    conn: sqlite3.Connection,
) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15))))
    rate(conn, "USD", day(1), "0.8")
    before = networth.run(conn, currency="USD")
    assert line(before, ETFS)[day(30)] == 500  # the price of the last trade
    other = ibkr(bought("WIDG", "5", "-400", day(20)))
    staging.stage(conn, batch(other, owner=PARTNER), NOW)
    assert networth.run(conn, currency="USD") == before
    staging.commit(conn, owner=PARTNER, now=NOW)
    assert line(networth.run(conn, currency="USD"), ETFS)[day(30)] == 800  # theirs, committed


def test_without_any_close_the_last_trade_price_stands_in_and_a_note_says_so(
    conn: sqlite3.Connection,
) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15)), bought("WIDG", "-4", "220", day(20))))
    result = networth.run(conn, currency="USD")
    etfs = line(result, ETFS)
    assert [etfs[d] for d in (day(15), day(19), day(20), day(30))] == [
        D(500),
        D(500),
        D(330),
        D(330),
    ]
    assert result.notes == (
        "No close is stored for WIDG: valued at the price of the last trade. Run `scrooge prices`.",
    )


def test_shares_sold_before_any_close_leave_no_note(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15)), bought("WIDG", "-10", "500", day(15))))
    result = networth.run(conn, currency="USD")
    assert [x.asset for x in result.lines] == [CASH]
    assert result.notes == ()


def test_shares_moved_in_without_a_price_are_not_counted_and_a_note_says_so(
    conn: sqlite3.Connection,
) -> None:
    close(conn, ibkr(bought("WIDG", "10", "0", day(15), kind=Kind.TRANSFER, category="Giroconto")))
    conn.execute("UPDATE trades SET price = NULL")
    result = networth.run(conn, currency="USD")
    assert [x.asset for x in result.lines] == [CASH]
    assert result.notes == ("WIDG has no price at all: it is not counted.",)


def test_a_split_changes_the_quantity_and_not_the_worth(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-2000", day(3), asset_tab="Stocks")))
    price(conn, "WIDG", day(9), "200")
    price(conn, "WIDG", day(10), "50.5")  # the first day of the new shares
    instrument = conn.execute("SELECT id FROM instruments").fetchone()[0]
    store.add_split(conn, instrument, day(10), 4, 1)
    stocks = line(networth.run(conn, currency="USD"), STOCKS)
    assert [stocks[d] for d in (day(9), day(10))] == [D(2000), D(2020)]


def test_holdings_a_statement_prints_are_where_the_quantity_starts_from(
    conn: sqlite3.Connection,
) -> None:
    # No trade is stored: the shares were there before the history starts.
    base = ibkr()
    held = (Holding("IBKR USD", "WIDG", "USD", D(10), D(55), D(550), "ETFs"),)
    close(conn, replace(base, extraction=replace(base.extraction, holdings=held)))
    result = networth.run(conn, currency="USD")
    etfs = line(result, ETFS)
    # Held when the statement begins: counted from its first day, not only from its close.
    # Nothing is counted before the statement.
    assert [etfs[d] for d in (day(1), day(29), day(30))] == [D(550), D(550), D(550)]
    assert etfs[day(31, 8)] == D(0)  # the day its opening balance is for


def test_fund_units_and_cash_of_a_pension_account_are_one_asset(conn: sqlite3.Connection) -> None:
    unit = bought(
        "CH0000000001", "2", "-300", day(15), asset_tab="Funds", account="Pillar 3a", currency="CHF"
    )
    close(conn, account("finpension", "Pillar 3a", "CHF", "1000", unit))
    price(conn, "CH0000000001", day(15), "155")
    result = networth.run(conn, currency="CHF")
    assert [x.asset for x in result.lines] == [PENSION]
    pension = line(result, PENSION)
    assert [pension[d] for d in (day(14), day(15))] == [D(1000), D(1010)]


# --------------------------------------------------------------------------------- bills


def bill(quantity: str, amount: str, on: date, **fields: object) -> SecurityTrade:
    fields.setdefault("maturity", date(2026, 12, 24))
    return bought("912797ZZ1", quantity, amount, on, asset_tab="Bonds", price=D(99), **fields)


def test_a_bill_runs_in_a_straight_line_from_its_cost_to_its_face(conn: sqlite3.Connection) -> None:
    # 100 days from 15 September to 24 December: 1.5 a day on the 150 below face.
    close(conn, ibkr(bill("10000", "-9850", day(15))))
    bills = line(networth.run(conn, currency="USD"), BILLS)
    assert [bills[d] for d in (day(14), day(15), day(16), day(25), day(30))] == [
        D(0),
        D(9850),
        D("9851.5"),
        D(9865),
        D("9872.5"),
    ]


def test_bill_lots_are_sold_oldest_first(conn: sqlite3.Connection) -> None:
    rows = [
        bill("10000", "-9850", day(15)),
        bill("10000", "-9900", day(20), maturity=None),
        bill("-10000", "9870", day(25)),
    ]
    close(conn, ibkr(*rows, opening="30000"))
    bills = line(networth.run(conn, currency="USD"), BILLS)
    # until the 24th both lots; from the 25th only the second, bought at 9900 on the 20th
    both_lots = D(9850) + D("1.5") * 9 + D(9900) + D(100) * 4 / 95
    assert bills[day(24)] == both_lots.quantize(D("0.01"))
    assert bills[day(25)] == (D(9900) + D(100) * 5 / 95).quantize(D("0.01"))
    assert bills[day(30)] == (D(9900) + D(100) * 10 / 95).quantize(D("0.01"))


def test_a_bill_is_worth_its_face_from_maturity_until_it_is_repaid(
    conn: sqlite3.Connection,
) -> None:
    rows = [
        bill("10000", "-9990", day(5), maturity=day(15)),
        bill("-10000", "10000", day(17), maturity=day(15), kind=Kind.INCOME),
    ]
    close(conn, ibkr(*rows, opening="20000"))
    result = networth.run(conn, currency="USD")
    bills = line(result, BILLS)
    assert [bills[d] for d in (day(5), day(10), day(15), day(16), day(17))] == [
        D(9990),
        D(9995),
        D(10000),
        D(10000),
        D(0),
    ]
    assert result.total[result.days.index(day(17))] == 20010


def test_a_bill_without_a_maturity_stays_at_its_cost(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bill("10000", "-9850", day(15))))
    conn.execute("UPDATE instruments SET maturity = NULL")
    bills = line(networth.run(conn, currency="USD"), BILLS)
    assert (bills[day(15)], bills[day(30)]) == (D(9850), D(9850))


def test_a_bill_repaid_without_its_purchase_is_not_counted(conn: sqlite3.Connection) -> None:
    # The history starts after it was bought: nothing is known of it but the cash it repaid.
    close(conn, ibkr(bill("-10000", "9870", day(25), kind=Kind.INCOME)))
    result = networth.run(conn, currency="USD")
    assert [x.asset for x in result.lines] == [CASH]
    assert result.notes == ()


def test_a_bill_a_statement_says_is_held_without_its_purchase_is_not_counted_and_a_note_says_so(
    conn: sqlite3.Connection,
) -> None:
    base = ibkr()
    held = (Holding("IBKR USD", "912797YY2", "USD", D(10000), D("98.5"), D(9850), "Bonds"),)
    close(conn, replace(base, extraction=replace(base.extraction, holdings=held)))
    result = networth.run(conn, currency="USD")
    assert [x.asset for x in result.lines] == [CASH]
    assert result.notes == ("912797YY2 was bought before the history starts: it is not counted.",)


# ---------------------------------------------------------------------------- currencies


def test_every_day_is_converted_at_its_own_rate(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(), account("migros", "Migros", "CHF", "900"))
    rate(conn, "USD", day(1), "0.8")
    rate(conn, "USD", day(16), "0.9")
    rate(conn, "EUR", day(1), "1")
    rate(conn, "EUR", day(16), "0.9")
    in_francs = networth.run(conn, currency="CHF")
    assert [in_francs.total[in_francs.days.index(d)] for d in (day(31, 8), day(15), day(16))] == [
        D(8900),  # the first rate stands in for the days before it
        D(8900),
        D(9900),
    ]
    # crossed through francs: 10000 USD and 900 CHF in euros
    in_euros = networth.run(conn, currency="EUR")
    assert [in_euros.total[in_euros.days.index(d)] for d in (day(15), day(16))] == [
        D(8900),
        D(11000),
    ]
    assert in_euros.currency == "EUR"
    in_dollars = networth.run(conn, currency="USD")
    assert in_dollars.total[in_dollars.days.index(day(16))] == D(11000)


def test_a_rate_printed_by_a_statement_counts_before_a_fetched_one(
    conn: sqlite3.Connection,
) -> None:
    close(conn, ibkr())
    rate(conn, "USD", day(30), "0.8")
    printed_rate(conn, "USD", day(30), "0.81")
    assert networth.run(conn, currency="CHF").total[-1] == 8100
    rate(conn, "USD", day(30), "0.82", "manual")
    assert networth.run(conn, currency="CHF").total[-1] == 8200
    rate(conn, "USD", day(30), "0.9", "fetched:other")
    assert networth.run(conn, currency="CHF").total[-1] == 8200


def test_a_currency_without_any_rate_cannot_be_converted(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(), account("migros", "Migros", "CHF", "900"))
    with pytest.raises(MissingRateError, match="no exchange rate is stored for USD"):
        networth.run(conn, currency="CHF")
    rate(conn, "USD", day(1), "0.8")
    assert networth.run(conn, currency="CHF").total[-1] == 8900
    with pytest.raises(MissingRateError, match="no exchange rate is stored for EUR"):
        networth.run(conn)


def test_what_is_already_in_the_currency_asked_for_needs_no_rate(conn: sqlite3.Connection) -> None:
    close(conn, ibkr())
    assert networth.run(conn, currency="USD").total[-1] == 10000


# -------------------------------------------------------------------------------- people


def both(conn: sqlite3.Connection) -> None:
    close(
        conn,
        account("migros", "Migros", "CHF", "1000", spent("-200", day(10))),
        ibkr(bought("WIDG", "10", "-500", day(15))),
    )
    close(
        conn, account("migros", "Migros", "CHF", "400", month=AUGUST), owner=PARTNER, month=AUGUST
    )
    close(
        conn,
        account("migros", "Migros", "CHF", "400", spent("50", day(12), kind=Kind.INCOME)),
        owner=PARTNER,
    )
    rate(conn, "USD", day(1, 7), "0.8")
    rate(conn, "EUR", day(1, 7), "0.95")


def test_each_person_has_lines_of_their_own_and_the_views_add_up(conn: sqlite3.Connection) -> None:
    both(conn)
    everyone = networth.run(conn, currency="CHF")
    assert [(x.owner, x.asset) for x in everyone.lines] == [
        (PARTNER, CASH),
        (OWNER, CASH),
        (OWNER, ETFS),
    ]
    assert everyone.days[0] == day(31, 7)  # her first statement
    mine = networth.run(conn, owner=OWNER, currency="CHF")
    hers = networth.run(conn, owner=PARTNER, currency="CHF")
    assert mine.days[0] == day(31, 8)
    assert [x.owner for x in mine.lines] == [OWNER, OWNER]
    assert everyone.total[-1] == mine.total[-1] + hers.total[-1] == D(800) + D(8000) + D(450)
    by_owner = everyone.by("owner")
    assert list(by_owner) == [PARTNER, OWNER]
    assert by_owner[OWNER][-1] == mine.total[-1]
    by_asset = everyone.by("asset")
    assert list(by_asset) == [CASH, ETFS]
    assert by_asset[CASH][-1] == D(800) + D(9500) * D("0.8") + D(450)


def test_several_people_each_count_up_to_their_own_last_day_and_are_carried_after(
    conn: sqlite3.Connection,
) -> None:
    both(conn)
    close(conn, account("migros", "Migros", "CHF", "800", month=OCTOBER), month=OCTOBER)
    assert networth.run(conn, owner=OWNER, currency="CHF").days[-1] == day(31, 10)
    assert networth.run(conn, owner=PARTNER, currency="CHF").days[-1] == day(30)
    everyone = networth.run(conn, currency="CHF")
    assert everyone.days[-1] == day(31, 10)
    assert line(everyone, CASH, PARTNER)[day(31, 10)] == D(450)
    assert everyone.notes[0] == (
        f"The statements of {PARTNER} end on 2026-09-30: from then on, what was held that day "
        "is valued at the closes and rates stored up to 2026-10-31."
    )


def test_what_was_held_on_the_last_day_is_valued_at_the_newest_closes(
    conn: sqlite3.Connection,
) -> None:
    close(conn, ibkr(bought("WIDG", "10", "-500", day(15)), bought("SPRK", "2", "-100", day(16))))
    sprk = conn.execute("SELECT id FROM instruments WHERE symbol = 'SPRK'").fetchone()[0]
    store.add_split(conn, sprk, day(10, 10), 3, 1)  # still multiplies what was held
    price(conn, "WIDG", day(30), "50")
    price(conn, "WIDG", day(2, 11), "60")
    price(conn, "SPRK", day(30), "50")
    price(conn, "SPRK", day(12, 10), "20")
    rate(conn, "USD", day(30), "0.8")
    rate(conn, "USD", day(3, 11), "0.9")  # the newest of all
    result = networth.run(conn, currency="CHF")
    assert result.days[-1] == day(3, 11)
    cash, etfs = line(result, CASH), line(result, ETFS)
    assert [cash[d] for d in (day(30), day(3, 11))] == [D(9400) * D("0.8"), D(9400) * D("0.9")]
    assert [etfs[d] for d in (day(30), day(1, 11), day(2, 11), day(3, 11))] == [
        D(10 * 50 + 2 * 50) * D("0.8"),
        D(10 * 50 + 6 * 20) * D("0.8"),
        D(10 * 60 + 6 * 20) * D("0.8"),
        D(10 * 60 + 6 * 20) * D("0.9"),
    ]
    assert result.notes == (
        f"The statements of {OWNER} end on 2026-09-30: from then on, what was held that day "
        "is valued at the closes and rates stored up to 2026-11-03.",
    )


def test_a_bill_goes_on_towards_its_face_after_the_last_day(conn: sqlite3.Connection) -> None:
    close(conn, ibkr(bill("10000", "-9850", day(15)), opening="50000"))
    rate(conn, "USD", day(24, 12), "1")
    bills = line(networth.run(conn, currency="USD"), BILLS)
    assert [bills[d] for d in (day(30), day(24, 12))] == [D("9872.50"), D(10000)]


@settings(max_examples=25, deadline=None)
@given(
    st.lists(st.tuples(st.integers(1, 28), st.integers(-5000, 5000)), max_size=6),
    st.lists(st.tuples(st.integers(1, 28), st.integers(1, 40)), max_size=3),
    st.sampled_from(["CHF", "EUR", "USD"]),
)
def test_the_total_is_the_sum_of_its_parts_however_it_is_cut(
    payments: list[tuple[int, int]], buys: list[tuple[int, int]], currency: str
) -> None:
    conn = db.connect(":memory:")
    db.migrate(conn, NOW)
    cash = [
        replace(spent(str(amount), day(n)), source_ref=str(i))
        for i, (n, amount) in enumerate(payments)
        if amount
    ]
    shares = [
        replace(bought("WIDG", str(quantity), str(-50 * quantity), day(n)), source_ref=f"t{i}")
        for i, (n, quantity) in enumerate(buys)
    ]
    close(conn, account("migros", "Migros", "CHF", "1000", *cash), ibkr(*shares))
    close(conn, account("migros", "Migros", "CHF", "400"), owner=PARTNER)
    rate(conn, "USD", day(1), "0.8")
    rate(conn, "USD", day(16), "0.83")
    rate(conn, "EUR", day(1), "0.95")
    everyone = networth.run(conn, currency=currency)
    for attribute in ("owner", "asset"):
        parts = everyone.by(attribute).values()
        assert tuple(sum(values) for values in zip(*parts, strict=True)) == everyone.total
    mine = networth.run(conn, owner=OWNER, currency=currency)
    hers = networth.run(conn, owner=PARTNER, currency=currency)
    cent = D("0.01") * len(everyone.lines)  # each line is rounded to the cent on its own
    for whole, a, b in zip(everyone.total, mine.total, hers.total, strict=True):
        assert abs(whole - a - b) <= cent
    # the same database always gives the same figures
    assert networth.run(conn, currency=currency) == everyone
    conn.close()


# -------------------------------------------------------------------------------- checks


def test_a_statement_s_own_valuation_is_set_against_what_is_derived(
    conn: sqlite3.Connection,
) -> None:
    base = ibkr(
        bought("WIDG", "10", "-500", day(15)), bill("10000", "-9850", day(15)), opening="20000"
    )
    values = (
        PrintedValue("Cash", "CHF", D(7720)),
        PrintedValue("Stock", "CHF", D(440)),
        PrintedValue("Bonds", "CHF", D(7900)),
        PrintedValue("Options", "CHF", D(-644)),
        PrintedValue("Total", "CHF", D(15416)),
    )
    held = (Holding("IBKR USD", "WIDG", "USD", D(10), D(55), D(550), "ETFs"),)
    close(
        conn,
        replace(base, extraction=replace(base.extraction, values=values, holdings=held)),
        account("migros", "Migros", "CHF", "1000"),
    )
    rate(conn, "USD", day(30), "0.8")
    found = networth.checks(conn)
    assert [(c.label, c.printed, c.derived, c.difference) for c in found] == [
        ("Bonds", D(7900), D(7898), D(-2)),  # 9872.5 dollars in a straight line
        ("Cash", D(7720), D(7720), D(0)),  # the broker's cash only, not the bank's
        ("Options", D(-644), None, None),  # not an asset here
        ("Stock", D(440), D(440), D(0)),  # at the close the statement prints
        ("Total", D(15416), D(16058), D(642)),  # the options, less the 2 on the bill
    ]
    assert {(c.owner, c.accounts, c.day, c.currency) for c in found} == {
        (OWNER, "IBKR USD", day(30), "CHF")
    }
    assert networth.checks(conn, owner=PARTNER) == ()


def test_one_person_s_checks_leave_out_the_other_s_statements(conn: sqlite3.Connection) -> None:
    his = account("migros", "Migros", "CHF", "1000")
    values = (PrintedValue("Cash", "CHF", D(1000)),)
    close(conn, replace(his, extraction=replace(his.extraction, values=values)))
    close(conn, account("migros", "Migros", "CHF", "500", filename="hers.csv"), owner=PARTNER)
    [mine] = networth.checks(conn, owner=OWNER)
    assert (mine.owner, mine.printed, mine.derived) == (OWNER, D(1000), D(1000))
    assert networth.checks(conn) == (mine,)
    assert networth.checks(conn, owner=PARTNER) == ()


def test_a_valuation_of_a_month_not_committed_is_not_checked(conn: sqlite3.Connection) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000"))
    base = ibkr(month=OCTOBER)
    values = (PrintedValue("Cash", "USD", D(10000)),)
    staged = replace(base, extraction=replace(base.extraction, values=values))
    staging.stage(conn, batch(staged, month=OCTOBER), NOW)
    assert networth.checks(conn) == ()
    staging.commit(conn, owner=OWNER, now=NOW)
    [check] = networth.checks(conn)
    assert (check.printed, check.derived) == (D(10000), D(10000))


def test_every_kind_of_asset_has_its_place_in_the_stack() -> None:
    assert sorted(networth.STACK) == sorted(networth.ASSETS)
    assert networth.STACK[0] == CASH  # the steadiest at the bottom


def test_days_run_without_a_gap() -> None:
    days = networth._days(day(27, 2), day(2, 3))
    assert days == tuple(day(27, 2) + timedelta(days=n) for n in range(4))


def test_an_account_that_comes_into_view_later_arrives_with_what_it_holds(
    conn: sqlite3.Connection,
) -> None:
    close(conn, account("migros", "Migros", "CHF", "1000", spent("-100", day(10))))
    october = date(2026, 10, 1)
    rows = spent("-20", date(2026, 10, 5), account="Revolut CHF")
    close(conn, account("revolut", "Revolut CHF", "CHF", "500", rows, month=october), month=october)
    result = networth.run(conn, currency="CHF")
    arrived = dict(zip(result.days, result.arrived, strict=True))
    # The bank account is there from the first day: nothing arrives with it. The second one
    # counts from the eve of its first statement, with the 500 francs it opens with.
    assert {when: amount for when, amount in arrived.items() if amount} == {day(30): D("500.00")}
    assert result.by("asset", arrivals=True)[CASH] == result.arrived
    assert result.by("owner", arrivals=True) == {OWNER: result.arrived}


def test_an_account_arrives_with_the_shares_its_first_statement_begins_with(
    conn: sqlite3.Connection,
) -> None:
    august = date(2026, 8, 1)
    close(conn, account("revolut", "Revolut USD", "USD", "100", month=august), month=august)
    # A broker whose first statement, a month later, opens with cash and with shares held.
    base = ibkr()
    held = (Holding("IBKR USD", "WIDG", "USD", D(10), D(55), D(550), "ETFs"),)
    close(conn, replace(base, extraction=replace(base.extraction, holdings=held)))
    result = networth.run(conn, currency="USD")
    arrived = dict(zip(result.days, result.arrived, strict=True))
    # Its cash on the eve of the statement, its shares on its first day: all of it brought,
    # none of it earned.
    assert {when: amount for when, amount in arrived.items() if amount} == {
        day(31, 8): D("10000.00"),
        day(1): D("550.00"),
    }
    assert result.by("asset", arrivals=True)[ETFS][(day(1) - result.days[0]).days] == D("550.00")
