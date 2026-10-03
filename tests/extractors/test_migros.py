from datetime import date
from decimal import Decimal

import pytest

from finanze.errors import ExtractionError
from finanze.extractors import migros
from finanze.models import CashBalance, Extraction, Kind
from finanze.validation import check_balances
from tests.extractors.golden import FIXTURES, assert_matches_golden

CSV = (FIXTURES / "migros" / "migros.csv").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def result() -> Extraction:
    return migros.extract(CSV, source_file="migros.csv")


def _row(result: Extraction, ref: str) -> tuple[date, Decimal, Kind, str]:
    txn = next(t for t in result.transactions if t.source_ref == ref)
    return txn.date, txn.amount, txn.kind, txn.category


def test_matches_golden_file(result: Extraction) -> None:
    assert_matches_golden(result, "migros")


def test_balances_come_from_the_running_saldo_not_the_export_day_saldo(
    result: Extraction,
) -> None:
    # The metadata says "Saldo: CHF 9000.00" (balance when exported); the period ends at 8647.02.
    assert result.month == date(2026, 9, 1)
    assert result.balances == (
        CashBalance("Migros", "CHF", Decimal("8000.00"), Decimal("8647.02")),
    )
    check_balances(result)


def test_other_currencies_get_their_own_account() -> None:
    eur = migros.extract(CSV.replace('"CHF 9000.00"', '"EUR 9000.00"'), source_file="m.csv")
    assert {(t.account, t.currency) for t in eur.transactions} == {("Migros EUR", "EUR")}


def test_value_date_is_used_unless_it_falls_outside_the_period(result: Extraction) -> None:
    assert _row(result, "3")[0] == date(2026, 9, 4)  # booked 07.09, value 04.09
    assert _row(result, "2")[0] == date(2026, 9, 1)  # value 30.08: booking date instead


def test_transfers_and_the_card_bill_are_classified(result: Extraction) -> None:
    assert _row(result, "3")[2:] == (Kind.TRANSFER, "Topup Revolut")
    assert _row(result, "9")[2:] == (Kind.TRANSFER, "Topup Revolut")
    assert _row(result, "8")[2:] == (Kind.TRANSFER, "Topup IBKR")
    assert _row(result, "6")[1:] == (Decimal(-100), Kind.TRANSFER, "Prelievi")
    assert _row(result, "7")[1:] == (Decimal(20), Kind.TRANSFER, "Prelievi")  # reversal nets
    assert _row(result, "4")[1:] == (Decimal("-1288.18"), Kind.INTERNAL, "")  # D3


def test_money_coming_back_from_a_broker_is_left_for_review() -> None:
    back = migros._classify("Interactive Brokers LLC, One Pickwick Plaza", Decimal(3000))
    assert back == (Kind.TRANSFER, "")


def test_everything_else_is_income_or_expense_for_the_rules(result: Extraction) -> None:
    assert _row(result, "1")[2:] == (Kind.EXPENSE, "")
    assert _row(result, "5")[2:] == (Kind.INCOME, "")


def test_description_and_counterparty(result: Extraction) -> None:
    by_ref = {t.source_ref: t for t in result.transactions}
    assert by_ref["1"].description == "Hans Muster, Beispielweg 8, 5000 Aarau | Affitto settembre"
    assert by_ref["1"].counterparty == "Hans Muster"
    assert by_ref["2"].counterparty == "KONSUM-1234 ZUERICH"
    assert by_ref["11"].counterparty == "Spese per traffico pagamenti"


def test_bom_is_ignored(result: Extraction) -> None:
    assert migros.extract("﻿" + CSV, source_file="migros.csv") == result


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (CSV.replace('"Data";"Testo', '"Datum";"Text'), "no transaction table"),
        (CSV.replace('"Importo";"Saldo";', '"Importo";'), "missing column"),
        (CSV.replace('"2026-09-30"', '"2026-09-29"'), "one calendar month"),
        (CSV.replace('"2026-09-01"', '"2026-09-02"'), "one calendar month"),
        (CSV.replace('"Estratto conto da:";"2026-09-01"\n', ""), "date"),
        (CSV.replace('"Saldo:";"CHF 9000.00"', '"Saldo:";"9000.00"'), "currency"),
        (CSV.replace('"Saldo:";"CHF 9000.00"\n', ""), "currency"),
        (CSV.replace('"-150.00";"5974.60"', '"-150.00";"9769.25"'), "does not follow"),
        (CSV.replace('"-4.40"', '"-4,40"'), "amount"),
        (CSV[: CSV.index('"01.09.2026"')], "no rows"),
        (CSV.replace('"01.09.2026";"Acquisto KONSUM', '"31.08.2026";"Acquisto KONSUM'), "outside"),
        (CSV.replace('"25.09.2026"\n', '"25.9.2026"\n'), "date"),
    ],
)
def test_malformed_statements_raise(content: str, message: str) -> None:
    assert content != CSV
    with pytest.raises(ExtractionError, match=message):
        migros.extract(content, source_file="migros.csv")
