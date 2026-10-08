"""What a statement says about holdings and their worth, from the files to the database."""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finanze import classification, monthclose, pipeline
from finanze.classification import Instrument, UnknownInstrumentError
from finanze.extractors import ibkr
from finanze.models import CashBalance, Holding, Kind, PrintedValue, Rate
from finanze.storage import store
from tests.conftest import INSTRUMENTS
from tests.storage.conftest import NOW
from tests.storage.helpers import OWNER, batch, rows, statement, trade
from tests.test_monthclose import fixture_batch, new_database, stage_september

D = Decimal


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = new_database()
    yield connection
    connection.close()


def test_holdings_values_and_rates_of_the_fixture_month_are_stored(
    conn: sqlite3.Connection, folder: Path
) -> None:
    assert stage_september(conn, folder).gaps == ()
    held = rows(
        conn,
        "SELECT a.name, i.symbol, i.type, p.date, p.quantity, p.price, p.value_nano, p.unvested "
        "FROM positions p JOIN accounts a ON a.id = p.account_id "
        "JOIN instruments i ON i.id = p.instrument_id ORDER BY i.symbol",
    )
    assert held == [
        ("IBKR USD", "912797ZZ1", "bill", "2026-09-30", "10000", "98.71", 9_871_000_000_000, 0),
        ("IBKR USD", "ACME", "stock", "2026-09-30", "20", "113.1", 2_262_000_000_000, 0),
        ("IBKR USD", "GOOG", "stock", "2026-09-30", "5", "204.3", 1_021_500_000_000, 0),
    ]
    # the close of a share is a price of that day; a bill has none
    prices = rows(
        conn,
        "SELECT i.symbol, p.date, p.close, p.source FROM prices p "
        "JOIN instruments i ON i.id = p.instrument_id ORDER BY i.symbol",
    )
    assert prices == [
        ("ACME", "2026-09-30", "113.1", "statement"),
        ("GOOG", "2026-09-30", "204.3", "statement"),
    ]
    assert rows(conn, "SELECT date, currency, rate_to_chf, source FROM fx_rates ORDER BY 2") == [
        ("2026-09-30", "EUR", "0.95", "statement"),
        ("2026-09-30", "USD", "0.8", "statement"),
    ]
    values = rows(
        conn,
        "SELECT v.label, v.currency, v.value_nano FROM statement_values v "
        "JOIN statement_files f ON f.id = v.statement_file_id WHERE f.source = 'ibkr' "
        "ORDER BY v.label",
    )
    assert values == [
        ("Bonds", "CHF", 7_896_800_000_000),
        ("Cash", "CHF", 5_986_200_000_000),
        ("Options", "CHF", -644_000_000_000),
        ("Stock", "CHF", 2_626_800_000_000),
        ("Total", "CHF", 15_865_800_000_000),
    ]
    printed = dict(rows(conn, "SELECT source, holdings FROM statement_files"))
    assert printed["ibkr"] == "printed"
    assert printed["migros"] == "not_printed"


def test_staging_the_same_statements_again_stores_the_same(
    conn: sqlite3.Connection, folder: Path
) -> None:
    stage_september(conn, folder)
    counts = "SELECT (SELECT count(*) FROM positions), (SELECT count(*) FROM statement_values)"
    before = rows(conn, counts)
    monthclose.stage(conn, fixture_batch(folder), NOW)
    assert rows(conn, counts) == before == [(3, 5)]


def test_a_maturity_its_statement_has_split_closes_its_lots_and_is_left_alone(
    conn: sqlite3.Connection,
) -> None:
    # IBKR prints the profit: principal and interest come apart in the extractor.
    bought = trade("-4875", "912797YY2", "5000", date=date(2026, 9, 2), asset_tab="Bonds")
    principal = trade(
        "4879.6",
        "912797YY2",
        "-5000",
        date=date(2026, 9, 17),
        kind=Kind.INCOME,
        asset_tab="Bonds",
        source_ref="Corporate Actions:1:principal",
    )
    balance = CashBalance("IBKR USD", "USD", D(5000), D("5004.6"))
    staged = monthclose.stage(conn, batch(statement("ibkr", [bought, principal], [balance])), NOW)
    assert staged.gaps == ()
    assert rows(conn, "SELECT amount_nano, split_of FROM transactions ORDER BY date") == [
        (-4_875_000_000_000, None),
        (4_879_600_000_000, None),
    ]


def test_a_maturity_its_statement_has_split_needs_no_stored_purchase(
    conn: sqlite3.Connection,
) -> None:
    principal = trade(
        "4879.6", "912797YY2", "-5000", kind=Kind.INCOME, asset_tab="Bonds", source_ref="1"
    )
    balance = CashBalance("IBKR USD", "USD", D(0), D("4879.6"))
    staged = monthclose.stage(conn, batch(statement("ibkr", [principal], [balance])), NOW)
    assert staged.gaps == ()
    assert monthclose.would_refuse(conn, store.owner_id(conn, OWNER) or 0) == []


def test_rates_in_another_base_than_francs_are_not_kept(conn: sqlite3.Connection) -> None:
    base = statement("ibkr", [], [CashBalance("IBKR USD", "USD", D(0), D(0))])
    rates = (Rate("USD", "CHF", D("0.8")), Rate("CHF", "EUR", D("1.05")))
    values = (PrintedValue("Total", "JPY", D(1000)),)
    extraction = replace(base.extraction, rates=rates, values=values)
    monthclose.stage(conn, batch(replace(base, extraction=extraction)), NOW)
    assert rows(conn, "SELECT currency, rate_to_chf FROM fx_rates") == [("USD", "0.8")]
    # a currency is known from the first value in it
    assert rows(conn, "SELECT currency FROM statement_values") == [("JPY",)]


def test_a_holding_in_a_currency_without_cash_gets_an_account(conn: sqlite3.Connection) -> None:
    base = statement("ibkr", [], [CashBalance("IBKR USD", "USD", D(0), D(0))])
    held = (Holding("IBKR EUR", "WIDG", "EUR", D(3), D(10), D(30), "ETFs"),)
    monthclose.stage(
        conn, batch(replace(base, extraction=replace(base.extraction, holdings=held))), NOW
    )
    assert rows(conn, "SELECT name, currency FROM accounts ORDER BY name") == [
        ("IBKR EUR", "EUR"),
        ("IBKR USD", "USD"),
    ]
    assert rows(conn, "SELECT count(*) FROM positions") == [(1,)]


def test_a_second_account_at_the_broker_holds_under_its_own_name(folder: Path) -> None:
    statements, _ = pipeline.load_statements(folder, database=True)
    renamed = pipeline.relabel(statements, {"U12345678": "IBKR 2"})
    [broker] = [s for s in renamed if s.source == "ibkr"]
    assert {h.account for h in broker.extraction.holdings or ()} == {"IBKR 2 USD"}
    [migros] = [s for s in renamed if s.source == "migros"]
    assert migros.extraction.holdings is None


def test_instruments_yaml_names_and_types_what_is_held(folder: Path) -> None:
    statements, _ = pipeline.load_statements(folder, database=True)
    instruments = INSTRUMENTS | {"ACME": Instrument(tab="ETFs", ticker="ACMX")}
    resolved = pipeline.resolve_holdings(statements, instruments)
    [broker] = [s for s in resolved if s.source == "ibkr"]
    held = {h.ticker: h.asset_tab for h in broker.extraction.holdings or ()}
    assert held == {"ACMX": "ETFs", "GOOG": "Stocks", "912797ZZ1": "Bonds"}
    assert len(resolved) == len(statements)


def test_a_holding_of_unknown_type_is_refused() -> None:
    held = Holding("IBKR USD", "MYST", "USD", D(1), D(1), D(1))
    with pytest.raises(UnknownInstrumentError, match="MYST: no asset tab"):
        classification.resolve_holding(held, {})
    known = classification.resolve_holding(held, {"MYST": Instrument(tab="Stocks")})
    assert (known.ticker, known.asset_tab) == ("MYST", "Stocks")


def test_instruments_yaml_may_say_where_prices_are_fetched(tmp_path: Path) -> None:
    path = tmp_path / "instruments.yaml"
    path.write_text("instruments:\n  WIDG: {tab: ETFs, price_symbol: WIDG.SW}\n  ACME:\n")
    assert classification.load_instruments(path) == {
        "WIDG": Instrument(tab="ETFs", price_symbol="WIDG.SW"),
        "ACME": Instrument(),
    }


def test_holdings_with_unvested_shares_are_marked() -> None:
    csv = (Path(__file__).parent / "fixtures" / "ibkr" / "activity.csv").read_text("utf-8")
    plain = "Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L"
    csv = csv.replace(plain, plain.replace("Symbol,", "Symbol,Open,"), 1)
    stocks = (
        "Stocks - Held with Interactive Brokers (U.K.) Limited carried by Interactive Brokers LLC"
    )
    csv = csv.replace(
        f"Open Positions,Data,Summary,{stocks},USD,ACME,20,",
        f"Open Positions,Data,Summary,{stocks},USD,ACME,-,20,",
    )
    csv = csv.replace(
        f"Open Positions,Data,Lot,{stocks},USD,ACME,20,",
        f"Open Positions,Data,Lot,{stocks},USD,ACME,2026-09-15 (Vesting: 2027-09-15),20,",
    )
    csv = csv.replace(
        f"Open Positions,Data,Summary,{stocks},USD,GOOG,5,",
        f"Open Positions,Data,Summary,{stocks},USD,GOOG,-,5,",
    )
    held = {h.ticker: h.unvested for h in ibkr.extract(csv, "activity.csv").holdings or ()}
    assert held == {"ACME": True, "GOOG": False, "912797ZZ1": False}
