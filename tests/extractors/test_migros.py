from datetime import date
from decimal import Decimal

import pytest

from scrooge import categories
from scrooge.errors import ExtractionError
from scrooge.extractors import migros
from scrooge.models import CashBalance, Extraction, Kind
from scrooge.validation import check_balances
from tests.extractors.golden import FIXTURES, assert_matches_golden

CSV = (FIXTURES / "migros" / "migros.csv").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def result() -> Extraction:
    return migros.extract(CSV, source_file="migros.csv")


def _row(result: Extraction, ref: str) -> tuple[date, Decimal, Kind, str]:
    txn = next(t for t in result.transactions if t.source_ref == ref)
    return txn.booking_date, txn.amount, txn.kind, txn.category


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


def test_the_iban_is_the_account_id(result: Extraction) -> None:
    iban = "CH" + "0" * 19
    assert result.account_id == iban
    assert migros.extract(OLDER, source_file="o.csv").account_id == iban


def test_the_booking_date_is_the_day_the_balance_books_the_row(result: Extraction) -> None:
    assert _row(result, "3")[0] == date(2026, 9, 7)  # booked 07.09, value 04.09
    assert _row(result, "2")[0] == date(2026, 9, 1)  # value 30.08


def test_the_real_date_is_always_the_value_date(result: Extraction) -> None:
    dates = {t.source_ref: t.date for t in result.transactions}
    assert dates["3"] == date(2026, 9, 4)
    assert dates["2"] == date(2026, 8, 30)  # D19: kept even though it is outside the period


def test_transfers_and_the_card_bill_are_classified(result: Extraction) -> None:
    assert _row(result, "3")[2:] == (Kind.TRANSFER, categories.REVOLUT_TOP_UP)
    assert _row(result, "9")[2:] == (Kind.TRANSFER, categories.REVOLUT_TOP_UP)
    assert _row(result, "8")[2:] == (Kind.TRANSFER, categories.IBKR_TOP_UP)
    assert _row(result, "6")[1:] == (Decimal(-100), Kind.TRANSFER, categories.WITHDRAWALS)
    reversal_nets = (Decimal(20), Kind.TRANSFER, categories.WITHDRAWALS)
    assert _row(result, "7")[1:] == reversal_nets
    assert _row(result, "4")[1:] == (Decimal("-1288.18"), Kind.INTERNAL, "")  # D3


def test_money_coming_back_from_a_broker_is_left_for_review() -> None:
    back = migros._classify("Interactive Brokers LLC, One Pickwick Plaza", Decimal(3000))
    assert back == (Kind.TRANSFER, "")
    reversal = migros._classify("Storno: Prelevamento al Bancomat BM Zürich", Decimal(20))
    assert reversal == (Kind.TRANSFER, categories.WITHDRAWALS)


def test_everything_else_is_income_or_expense_for_the_rules(result: Extraction) -> None:
    assert _row(result, "1")[2:] == (Kind.EXPENSE, "")
    assert _row(result, "5")[2:] == (Kind.INCOME, "")


def test_description_and_counterparty(result: Extraction) -> None:
    by_ref = {t.source_ref: t for t in result.transactions}
    assert by_ref["1"].description == "Hans Muster, Beispielweg 8, 5000 Aarau | Affitto settembre"
    assert by_ref["1"].counterparty == "Hans Muster"
    assert by_ref["2"].counterparty == "KONSUM-1234 ZUERICH"
    assert by_ref["11"].counterparty == "Spese per traffico pagamenti"


@pytest.mark.parametrize(
    ("party", "counterparty"),
    [
        ("Rossi, Maria, +41790000000 0400000000000001", "Accredito TWINT Rossi"),
        # no surname: the first name and the number tell people apart
        (", Maria, +41790000000 0400000000000001", "Accredito TWINT Maria, +41790000000"),
        (", Piero, +41790000009 0400000000000001", "Accredito TWINT Piero, +41790000009"),
        # a shop: the same whatever the reference of the payment
        ("Kiosk Nr. 10 0400000000000001", "Accredito TWINT Kiosk Nr. 10"),
        ("Kiosk Nr. 10", "Accredito TWINT Kiosk Nr. 10"),
    ],
)
def test_twint_counterparties(party: str, counterparty: str) -> None:
    paid = "Accredito TWINT Rossi, Maria, +41790000000 0400000000000001"
    assert paid in CSV
    result = migros.extract(CSV.replace(paid, f"Accredito TWINT {party}"), source_file="m.csv")
    assert counterparty in {t.counterparty for t in result.transactions}


def test_bom_is_ignored(result: Extraction) -> None:
    assert migros.extract("\ufeff" + CSV, source_file="migros.csv") == result


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (CSV.replace('"Data";"Testo', '"Datum";"Text'), "no transaction table"),
        (CSV.replace('"Importo";"Saldo";', '"Importo";'), "missing column"),
        (CSV.replace('"2026-09-30"', '"2026-09-29"'), "outside"),
        (CSV.replace('"2026-09-01"', '"2026-09-02"'), "outside"),
        (CSV.replace('"2026-09-01"', '"2026-10-01"'), "ends before it starts"),
        (CSV.replace('"Estratto conto da:";"2026-09-01"\n', ""), "date"),
        (CSV.replace('"Saldo:";"CHF 9000.00"', '"Saldo:";"9000.00"'), "currency"),
        (CSV.replace('"Saldo:";"CHF 9000.00"\n', ""), "currency"),
        (CSV.replace('"-150.00";"5974.60"', '"-150.00";"9769.25"'), "does not follow"),
        (CSV.replace('"-4.40"', '"-4,40"'), "amount"),
        (CSV[: CSV.index('"01.09.2026"')].replace("CHF 9000.00", "CHF n/a"), "amount"),
        (CSV.replace('"01.09.2026";"Acquisto KONSUM', '"31.08.2026";"Acquisto KONSUM'), "outside"),
        (CSV.replace('"25.09.2026"\n', '"25.9.2026"\n'), "date"),
    ],
)
def test_malformed_statements_raise(content: str, message: str) -> None:
    assert content != CSV
    with pytest.raises(ExtractionError, match=message):
        migros.extract(content, source_file="migros.csv")


def test_a_period_with_no_bookings_has_the_balance_of_the_export(result: Extraction) -> None:
    empty = migros.extract(CSV[: CSV.index('"01.09.2026"')], source_file="m.csv")
    assert empty.period == (date(2026, 9, 1), date(2026, 9, 30))
    assert empty.transactions == ()
    assert empty.balances == (CashBalance("Migros", "CHF", Decimal("9000.00"), Decimal("9000.00")),)
    assert empty.account_id == "CH0000000000000000000"
    # It holds only if the period runs up to the export: staging checks that.
    assert empty.balances_on_export
    assert not result.balances_on_export
    check_balances(empty)


def test_a_statement_ends_on_the_day_it_prints() -> None:
    # Exported from August on the 12th of October: it covers those days exactly.
    longer = CSV.replace('"2026-09-01"', '"2026-08-01"').replace('"2026-09-30"', '"2026-10-12"')
    result = migros.extract(longer, source_file="m.csv")
    assert result.period == (date(2026, 8, 1), date(2026, 10, 12))
    check_balances(result)
    assert {t.source_ref: t.booking_date for t in result.transactions}["2"] == date(2026, 9, 1)


def test_a_partial_month_covers_the_days_it_prints_and_reconciles() -> None:
    part = CSV.replace('"2026-09-01"\n', '"2026-09-05"\n', 1).replace(
        '"2026-09-30"', '"2026-09-20"'
    )
    # keep only the rows booked from the 5th to the 20th: the first row starts the chain
    lines = part.splitlines()
    head = next(i for i, line in enumerate(lines) if line.startswith('"Data"'))
    kept = [
        line for line in lines[head + 1 :] if line and "2026-09-05" <= _iso(line) <= "2026-09-20"
    ]
    assert kept
    result = migros.extract("\n".join([*lines[: head + 1], *kept]) + "\n", source_file="m.csv")
    assert result.period == (date(2026, 9, 5), date(2026, 9, 20))
    assert result.month == date(2026, 9, 1)
    check_balances(result)


def _iso(line: str) -> str:
    day, month, year = line.split(";", maxsplit=1)[0].strip('"').split(".")
    return f"{year}-{month}-{day}"


def test_a_row_after_the_printed_end_is_refused() -> None:
    cut = CSV.replace('"2026-09-01"', '"2026-08-01"').replace('"2026-09-30"', '"2026-09-12"')
    with pytest.raises(ExtractionError, match="outside the period"):
        migros.extract(cut, source_file="m.csv")


OLDER = (FIXTURES / "migros" / "older.csv").read_text(encoding="utf-8")


def test_the_older_layout_is_read_and_put_back_in_balance_order() -> None:
    result = migros.extract(OLDER, source_file="migros.csv")
    assert result.period == (date(2023, 5, 1), date(2023, 6, 1))  # exported on 1 June
    assert result.balances == (
        CashBalance("Migros", "CHF", Decimal("5000.00"), Decimal("8157.65")),
    )
    check_balances(result)
    # Rows 3 and 4 share a day and are listed the other way round.
    assert [t.source_ref for t in result.transactions] == ["1", "2", "4", "3", "5"]
    by_ref = {t.source_ref: t for t in result.transactions}
    assert (by_ref["2"].kind, by_ref["2"].category) == (Kind.INTERNAL, "")
    assert (by_ref["3"].kind, by_ref["3"].category) == (Kind.TRANSFER, categories.IBKR_TOP_UP)
    assert by_ref["1"].description == "Hans Muster, Beispielweg 8, 5000 Aarau"
    assert by_ref["1"].counterparty == "Hans Muster"


def test_rows_that_only_lead_to_each_other_are_refused() -> None:
    loop = OLDER.replace("-1000.00;1657.65", "100.00;200.00").replace(
        "-80.00;2657.65", "-100.00;100.00"
    )
    loop = loop.replace("6500.00;8157.65", "5420.00;8157.65")  # follows row 2 directly
    with pytest.raises(ExtractionError, match=r"row 3: balance 200\.00 does not follow"):
        migros.extract(loop, source_file="migros.csv")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("-80.00;2657.65", "-80.00;2657.66"),  # a balance no row leads to
        ("-1000.00;1657.65", "-1000.00;8157.65"),  # two rows end on the same balance
        ("25.05.2023;ACME SWITZERLAND GMBH;6500.00;8157.65", "25.05.2023;X;10.00;10.00"),
    ],
)
def test_rows_that_cannot_be_put_in_order_are_refused(old: str, new: str) -> None:
    assert old in OLDER
    with pytest.raises(ExtractionError, match="does not follow"):
        migros.extract(OLDER.replace(old, new), source_file="migros.csv")
