from dataclasses import replace
from datetime import date

from finanze.storage.ids import transaction_ids
from tests.storage.helpers import txn


def test_the_file_name_and_the_category_do_not_take_part() -> None:
    row = txn("-12.50")
    again = replace(row, source_file="export (2).csv", source_ref="77", category="Ristoranti")
    assert transaction_ids("Lukas", [("migros", row)]) == transaction_ids(
        "Lukas", [("migros", again)]
    )


def test_identical_rows_get_different_ids_in_a_stable_order() -> None:
    row = txn("-4.20", description="CAFFE")
    batch = [("migros", row), ("migros", row), ("migros", txn("-1"))]
    first = transaction_ids("Lukas", batch)
    assert len(set(first)) == 3
    assert first == transaction_ids("Lukas", batch)
    assert first[0] == transaction_ids("Lukas", [("migros", row)])[0]


def test_anything_the_statement_says_changes_the_id() -> None:
    row = txn("-12.50")
    base = transaction_ids("Lukas", [("migros", row)])[0]
    variants = [
        ("Anna", "migros", row),
        ("Lukas", "swisscard", row),
        ("Lukas", "migros", replace(row, account="Migros EUR")),
        ("Lukas", "migros", replace(row, date=date(2026, 9, 11))),
        ("Lukas", "migros", replace(row, booking_date=date(2026, 9, 11))),
        ("Lukas", "migros", txn("-12.51")),
        ("Lukas", "migros", replace(row, currency="EUR")),
        ("Lukas", "migros", replace(row, description="KONSUM 2")),
    ]
    ids = {transaction_ids(owner, [(source, t)])[0] for owner, source, t in variants}
    assert len(ids) == len(variants)
    assert base not in ids
    assert len(base) == 24
