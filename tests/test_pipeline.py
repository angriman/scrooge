from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finanze import pipeline, router
from finanze.aggregate import BudgetRow, UncategorizedError
from finanze.classification import load_rules
from finanze.errors import ExtractionError
from finanze.models import Kind
from finanze.validation import BalanceMismatchError, CardBillMismatchError, check_card_bill
from tests.conftest import write_rules as _rules


def test_router_detects_every_source_and_skips_the_rest(folder: Path) -> None:
    extractions, skipped = pipeline.load_folder(folder)
    assert [source for source, _ in extractions] == [
        "ibkr",
        "migros",
        "revolut",
        "revolut_savings",
        "swisscard",
        "tastytrade",
    ]
    assert [path.name for path in skipped] == ["ibkr.pdf", "notes.txt", "other.csv"]
    csv_copy = folder / "Revolut CHF.csv"
    csv_copy.write_text((folder / "Revolut CHF.tsv").read_text().replace("\t", ","))
    loaded = router.load(csv_copy)
    assert loaded is not None
    assert loaded[0] == "revolut"


def test_month_without_rules_reconciles_and_lists_what_needs_review(folder: Path) -> None:
    extractions, skipped = pipeline.load_folder(folder)
    month = pipeline.process(extractions, (), {}, skipped)
    assert month.month == date(2026, 9, 1)
    assert month.unreconciled == ("Revolut EUR",)
    assert [path.name for path in month.skipped] == ["ibkr.pdf", "notes.txt", "other.csv"]
    assert len(month.uncategorized) > 10
    assert [(t.ticker, t.asset_tab) for t in month.trades] == [
        ("ACME", "Stocks"),
        ("WIDG", "ETFs"),
        ("912797ZZ1", "Bonds"),
    ]
    with pytest.raises(UncategorizedError):
        month.budget()


def test_fully_classified_month_aggregates(folder: Path) -> None:
    extractions, _ = pipeline.load_folder(folder)
    month = pipeline.process(extractions, load_rules([_rules(folder)]), {})
    assert month.uncategorized == ()
    budget = month.budget()
    uscite = {(r.category, r.account): r.amount for r in budget.uscite}
    entrate = {(r.category, r.account): r.amount for r in budget.entrate}
    assert uscite["Affitto", "Migros"] == Decimal("1850.00")
    # Migros: Konsum 25.40; Swisscard: Frischmarkt 41.85; Revolut: Konsum 20
    assert uscite["Spesa", "Migros"] == Decimal("67.25")
    assert uscite["Spesa", "Revolut CHF"] == Decimal("20.00")
    assert uscite["Revolut Metal", "Revolut CHF"] == Decimal("18.99")
    # Migros 150 + 200 by transfer, plus 100 by credit card (Swisscard)
    assert uscite["Topup Revolut", "Migros"] == Decimal("450.00")
    assert entrate["Topup Revolut", "Revolut CHF"] == Decimal("230.00")
    assert entrate["Cashback", "Migros"] == Decimal("67.80")
    assert entrate["Stipendio", "Migros"] == Decimal("7200.00")
    assert BudgetRow("Interessi", "Revolut EUR", Decimal("0.17")) in budget.entrate
    # D3: the card bill itself never reaches the sheet
    internal = [t for t in month.transactions if t.kind is Kind.INTERNAL and t.account == "Migros"]
    assert [t.amount for t in internal] == [Decimal("-1288.18")]
    assert Decimal("1288.18") not in uscite.values()


def test_card_invoice_must_match_the_bank_debit(folder: Path) -> None:
    migros = folder / "Migros.csv"
    text = migros.read_text(encoding="utf-8")
    migros.write_text(
        text.replace('"-1288.18";"4686.42"', '"-1288.13";"4686.47"').replace(
            '"45.00";"4731.42"', '"44.95";"4731.42"'
        ),
        encoding="utf-8",
    )
    extractions, _ = pipeline.load_folder(folder)
    with pytest.raises(CardBillMismatchError, match=r"-1288\.18, but the bank debited -1288\.13"):
        pipeline.process(extractions, (), {})


def test_missing_invoice_or_missing_debit_blocks(folder: Path) -> None:
    (folder / "swisscard.pdf").unlink()
    extractions, _ = pipeline.load_folder(folder)
    with pytest.raises(CardBillMismatchError, match="no card invoice"):
        pipeline.process(extractions, (), {})
    with pytest.raises(CardBillMismatchError, match="no card debit"):
        check_card_bill(None, Decimal(-10))
    check_card_bill(None, None)


def test_statements_of_different_months_block(folder: Path) -> None:
    tasty = folder / "tastytrade.csv"
    tasty.write_text(tasty.read_text(encoding="utf-8").replace("2026-09-", "2026-08-"))
    extractions, _ = pipeline.load_folder(folder)
    with pytest.raises(ExtractionError, match="2026-08, 2026-09"):
        pipeline.process(extractions, (), {})
    with pytest.raises(ExtractionError, match="found: none"):
        pipeline.process([], (), {})


def test_reconciliation_failure_blocks(folder: Path) -> None:
    ibkr = folder / "IBKR.csv"
    text = ibkr.read_text(encoding="utf-8")
    ibkr.write_text(text.replace("Ending Cash,EUR,249.2,", "Ending Cash,EUR,250.2,"))
    extractions, _ = pipeline.load_folder(folder)
    with pytest.raises(BalanceMismatchError, match="IBKR EUR"):
        pipeline.process(extractions, (), {})


def test_extraction_errors_name_the_file(folder: Path) -> None:
    migros = folder / "Migros.csv"
    migros.write_text(migros.read_text(encoding="utf-8").replace('"-4.40"', '"-4,40"'))
    with pytest.raises(ExtractionError, match=r"Migros\.csv: cannot read amount"):
        pipeline.load_folder(folder)
