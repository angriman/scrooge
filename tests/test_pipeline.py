from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finanze import pipeline, router
from finanze.classification import Rule, UnknownInstrumentError, load_rules
from finanze.errors import ExtractionError
from finanze.extractors import ocr, swisscard
from finanze.models import (
    CashBalance,
    Extraction,
    Kind,
    PensionCertificate,
    SecurityTrade,
    Transaction,
)
from finanze.validation import (
    BalanceMismatchError,
    CardBillMismatchError,
    check_balances,
)
from tests.conftest import FIXTURES, INSTRUMENTS
from tests.conftest import write_rules as _rules


def _trades(month: pipeline.Month) -> list[SecurityTrade]:
    return [
        t
        for t in month.transactions
        if isinstance(t, SecurityTrade) and t.kind is Kind.SECURITY_TRADE
    ]


def _load(folder: Path) -> tuple[list[tuple[str, Extraction]], list[Path]]:
    statements, skipped = pipeline.load_statements(folder)
    return [(s.source, s.extraction) for s in statements], skipped


def _rules_of(folder: Path) -> tuple[Rule, ...]:
    return load_rules([_rules(folder)])


def test_router_detects_every_source_and_skips_the_rest(folder: Path) -> None:
    extractions, skipped = _load(folder)
    assert [source for source, _ in extractions] == [
        "ibkr",
        "migros",
        "revolut",
        "revolut_savings",
        "swisscard",
        "tastytrade",
    ]
    assert [path.name for path in skipped] == ["ibkr.pdf", "notes.txt"]
    csv_copy = folder / "Revolut CHF.csv"
    csv_copy.write_text((folder / "Revolut CHF.tsv").read_text().replace("\t", ","))
    loaded = router.load(csv_copy)
    assert loaded is not None
    assert loaded[0] == "revolut"


def test_unrecognised_csv_blocks_instead_of_dropping_an_account(folder: Path) -> None:
    (folder / "other.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ExtractionError, match=r"other\.csv: not a statement format"):
        _load(folder)


def test_month_without_rules_reconciles_and_lists_what_needs_review(folder: Path) -> None:
    extractions, skipped = _load(folder)
    month = pipeline.process(extractions, (), INSTRUMENTS, skipped)
    # The card invoice prints its billing period, 5 August to 4 September.
    assert month.period == (date(2026, 8, 5), date(2026, 9, 30))
    assert month.month == date(2026, 8, 1)
    assert month.unreconciled == ("Revolut EUR",)
    assert [path.name for path in month.skipped] == ["ibkr.pdf", "notes.txt"]
    assert len(month.uncategorized) > 10
    assert [(t.account, t.ticker, t.asset_tab, t.maturity) for t in _trades(month)] == [
        ("IBKR USD", "ACME", "Stocks", None),
        ("IBKR USD", "WIDG", "ETFs", None),
        ("IBKR USD", "912797ZZ1", "Bonds", date(2026, 12, 24)),
        ("Tastytrade", "912797ZZ1", "Bonds", date(2026, 12, 24)),
        ("Tastytrade", "WIDG", "ETFs", None),
    ]


def test_instrument_the_statements_cannot_place_blocks(folder: Path) -> None:
    extractions, _ = _load(folder)
    with pytest.raises(UnknownInstrumentError, match=r"WIDG|912797ZZ1"):
        pipeline.process(extractions, (), {})


def test_fully_classified_month_has_nothing_left_to_review(folder: Path) -> None:
    extractions, _ = _load(folder)
    month = pipeline.process(extractions, load_rules([_rules(folder)]), INSTRUMENTS)
    assert month.uncategorized == ()
    # D3: the card bill itself is no spending, it is the bank's debit for the card
    internal = [t for t in month.transactions if t.kind is Kind.INTERNAL and t.account == "Migros"]
    assert [t.amount for t in internal] == [Decimal("-1288.18")]


def test_no_statements_block() -> None:
    with pytest.raises(ExtractionError, match="no statements to process"):
        pipeline.process([], (), INSTRUMENTS)


def test_reconciliation_failure_blocks(folder: Path) -> None:
    ibkr = folder / "IBKR.csv"
    text = ibkr.read_text(encoding="utf-8")
    ibkr.write_text(text.replace("Ending Cash,EUR,249.2,", "Ending Cash,EUR,250.2,"))
    extractions, _ = _load(folder)
    with pytest.raises(BalanceMismatchError, match="IBKR EUR"):
        pipeline.process(extractions, (), INSTRUMENTS)


def test_file_in_an_old_encoding_is_an_extraction_error(folder: Path) -> None:
    (folder / "Migros.csv").write_bytes("Data;Saldo\n1;Zürich\n".encode("cp1252"))
    with pytest.raises(ExtractionError, match=r"Migros\.csv: not UTF-8 text"):
        _load(folder)


def test_extraction_errors_name_the_file(folder: Path) -> None:
    migros = folder / "Migros.csv"
    migros.write_text(migros.read_text(encoding="utf-8").replace('"-4.40"', '"-4,40"'))
    with pytest.raises(ExtractionError, match=r"Migros\.csv: cannot read amount"):
        _load(folder)


PENSION = Path(__file__).parent / "fixtures" / "finpension" / "transactions.tsv"


def test_a_statement_of_several_months_is_processed_as_one_period(tmp_path: Path) -> None:
    (tmp_path / "pillar 3a.tsv").write_bytes(PENSION.read_bytes())
    statements, skipped = pipeline.load_statements(tmp_path)
    assert [(s.source, s.filename) for s in statements] == [("finpension", "pillar 3a.tsv")]
    month = pipeline.process([(s.source, s.extraction) for s in statements], (), {}, skipped)
    assert (month.month, month.start, month.end) == (
        date(2026, 7, 1),
        date(2026, 7, 6),
        date(2026, 9, 16),
    )
    assert month.period == (date(2026, 7, 6), date(2026, 9, 16))
    assert month.uncategorized == ()
    assert {t.asset_tab for t in _trades(month)} == {"Funds"}


def test_a_garbled_byte_order_mark_does_not_hide_the_format(tmp_path: Path) -> None:
    # A UTF-8 mark read as Latin-1 and saved again: three stray characters before the header.
    garbled = "\u00ef\u00bb\u00bf".encode() + PENSION.read_bytes()
    (tmp_path / "pillar 3a.tsv").write_bytes(garbled)
    loaded = router.load(tmp_path / "pillar 3a.tsv")
    assert loaded is not None
    assert loaded[0] == "finpension"


def test_files_from_several_folders_get_names_that_tell_them_apart(folder: Path) -> None:
    other = folder / "old"
    (other / "Migros.csv").write_bytes((folder / "Migros.csv").read_bytes())
    bank = folder / "Migros.csv"  # given twice: counted once
    files = [bank, other / "Migros.csv", folder / "IBKR.csv", bank, folder / "notes.txt"]
    statements, skipped = pipeline.load_files(files)
    assert [s.filename for s in statements] == [
        "IBKR.csv",
        f"{folder.name}/Migros.csv",
        "old/Migros.csv",
    ]
    assert [path.name for path in skipped] == ["notes.txt"]
    sources = {t.source_file for s in statements for t in s.extraction.transactions}
    assert sources == {s.filename for s in statements}


def test_statements_of_different_periods_can_be_processed_together(folder: Path) -> None:
    (folder / "pillar 3a.tsv").write_bytes(PENSION.read_bytes())
    extractions, skipped = _load(folder)
    month = pipeline.process(extractions, _rules_of(folder), INSTRUMENTS, skipped)
    assert month.period == (date(2026, 7, 6), date(2026, 9, 30))
    assert {t.account for t in month.transactions} >= {"Finpension 3a", "Migros", "IBKR USD"}


def _bank(*debits: tuple[date, str], months: tuple[date, date]) -> Extraction:
    rows = tuple(
        Transaction(
            date=day,
            account="Migros",
            amount=Decimal(amount),
            currency="CHF",
            description="card bill",
            kind=Kind.INTERNAL,
            source_file="bank.csv",
            source_ref=str(day),
        )
        for day, amount in debits
    )
    total = sum((row.amount for row in rows), Decimal(0))
    balance = CashBalance("Migros", "CHF", Decimal(1000), Decimal(1000) + total)
    return Extraction(month=months[0], transactions=rows, balances=(balance,), end=months[1])


def _invoice(month: date, *payments: tuple[date, str]) -> Extraction:
    rows = tuple(
        Transaction(
            date=day,
            account="Swisscard",
            amount=Decimal(amount),
            currency="CHF",
            description="payment",
            kind=Kind.INTERNAL,
            source_file="card.pdf",
            source_ref=str(day),
            booking_date=month.replace(day=4),
        )
        for day, amount in payments
    )
    total = sum((row.amount for row in rows), Decimal(0))
    balance = CashBalance("Swisscard", "CHF", -total, Decimal(0))
    return Extraction(month=month, transactions=rows, balances=(balance,))


AUTUMN = (date(2026, 8, 1), date(2026, 9, 30))


def test_card_payments_are_matched_to_the_bank_debits_a_few_days_apart() -> None:
    bank = _bank((date(2026, 8, 5), "-50"), (date(2026, 9, 26), "-80"), months=AUTUMN)
    # The card books the August payment two days later; September's is not invoiced yet.
    september = _invoice(date(2026, 9, 1), (date(2026, 8, 7), "50"))
    both = [("migros", bank), ("swisscard", september)]
    month = pipeline.process(both, (), {})
    assert month.period == AUTUMN


def test_a_payment_from_before_the_bank_statements_is_left_alone() -> None:
    bank = _bank(months=AUTUMN)
    august = _invoice(date(2026, 8, 1), (date(2026, 7, 26), "50"))
    pipeline.process([("migros", bank), ("swisscard", august)], (), {})


def test_a_card_payment_the_bank_never_made_blocks() -> None:
    bank = _bank((date(2026, 8, 5), "-50"), months=AUTUMN)
    september = _invoice(date(2026, 9, 1), (date(2026, 8, 20), "50"))  # 15 days apart
    with pytest.raises(CardBillMismatchError, match="lists a payment of 50 on 2026-08-20"):
        pipeline.process([("migros", bank), ("swisscard", september)], (), {})


def test_a_bank_debit_no_invoice_lists_blocks() -> None:
    bank = _bank((date(2026, 8, 5), "-50"), months=AUTUMN)
    september = _invoice(date(2026, 9, 1))
    with pytest.raises(CardBillMismatchError, match="debited 50 for the card on 2026-08-05"):
        pipeline.process([("migros", bank), ("swisscard", september)], (), {})


def test_two_equal_payments_are_matched_one_to_one() -> None:
    bank = _bank((date(2026, 8, 5), "-50"), (date(2026, 8, 6), "-50"), months=AUTUMN)
    september = _invoice(date(2026, 9, 1), (date(2026, 8, 5), "50"), (date(2026, 8, 6), "50"))
    pipeline.process([("migros", bank), ("swisscard", september)], (), {})
    once = _invoice(date(2026, 9, 1), (date(2026, 8, 5), "50"))
    with pytest.raises(CardBillMismatchError, match="no card invoice lists"):
        pipeline.process([("migros", bank), ("swisscard", once)], (), {})


def test_the_older_migros_layout_is_recognised(tmp_path: Path) -> None:
    older = Path(__file__).parent / "fixtures" / "migros" / "older.csv"
    (tmp_path / "migros.csv").write_bytes(older.read_bytes())
    loaded = router.load(tmp_path / "migros.csv")
    assert loaded is not None
    assert (loaded[0], loaded[1].month) == ("migros", date(2023, 5, 1))


def test_a_second_account_at_a_broker_gets_names_of_its_own(folder: Path) -> None:
    statements, _ = pipeline.load_statements(folder)
    renamed = pipeline.relabel(statements, {"U12345678": "IBKR 2", "U0": "unused"})
    accounts = {(s.source, t.account) for s in renamed for t in s.extraction.transactions}
    assert {name for source, name in accounts if source == "ibkr"} == {
        "IBKR 2 CHF",
        "IBKR 2 EUR",
        "IBKR 2 USD",
    }
    assert ("migros", "Migros") in accounts  # the others are left alone
    broker = next(s.extraction for s in renamed if s.source == "ibkr")
    assert {b.account for b in broker.balances} == {"IBKR 2 CHF", "IBKR 2 EUR", "IBKR 2 USD"}
    check_balances(broker)


def test_a_pdf_saved_without_its_extension_is_still_read(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = (folder / "swisscard.pdf").read_text(encoding="utf-8")
    monkeypatch.setattr(swisscard, "read_text", lambda path: text)
    bare = folder / "swisscard"
    bare.write_bytes(b"%PDF-1.7 ...")
    loaded = router.load(bare)
    assert loaded is not None
    assert loaded[0] == "swisscard"
    (folder / "notes").write_text("no extension, not a PDF", encoding="utf-8")
    assert router.load(folder / "notes") is None


def test_a_bill_gets_its_maturity_from_another_row_of_the_same_bill(folder: Path) -> None:
    # Tastytrade buys 912797ZZ1 without a maturity; IBKR's statement has it for the same bill.
    extractions, skipped = _load(folder)
    without = {"WIDG": INSTRUMENTS["WIDG"]}
    month = pipeline.process(extractions, (), without, skipped)
    bought = [t for t in _trades(month) if t.ticker == "912797ZZ1"]
    assert {t.account for t in bought} == {"IBKR USD", "Tastytrade"}
    assert {t.maturity for t in bought} == {date(2026, 12, 24)}


# ------------------------------------------------------------------ pension certificates


def test_scanned_certificates_are_told_from_the_other_files(folder: Path, pillar2: Path) -> None:
    files = [*sorted(folder.iterdir()), *sorted(pillar2.iterdir())]
    files = [path for path in files if path.is_file()]
    certificates, others = pipeline.load_certificates(files)
    assert [(c.filename, c.account, c.adds_up) for c in certificates] == [
        ("Dokument.pdf", "Pillar 2 Servisa", False),
        ("Scan 0001.pdf", "Pillar 2 AXA", True),
    ]
    assert certificates[1].stated == PensionCertificate(
        "axa",
        date(2026, 3, 1),
        "CHF",
        Decimal("35802.00"),
        Decimal("12345.65"),
        date(2021, 9, 1),
    )
    assert certificates[0].stated.balance == Decimal("3579.40")
    assert len({c.sha256 for c in certificates}) == 2
    # every other file is left for `load_files`, the PDFs that are no scans included
    assert sorted(others) == sorted(path for path in files if path.parent == folder)


def test_a_pdf_with_text_is_never_read_with_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(path: Path) -> str:
        raise AssertionError(path)

    monkeypatch.setattr(ocr, "read_text", never)
    monkeypatch.setattr(ocr, "is_scan", lambda path: path.name.startswith("scan"))
    (tmp_path / "invoice.pdf").write_bytes(b"%PDF-1.7 with text")
    (tmp_path / "bare").write_bytes(b"%PDF-1.7 with text, saved without its extension")
    (tmp_path / "scan").write_text("not a PDF: never looked at", encoding="utf-8")
    (tmp_path / "scan.csv").write_text("Date,Amount", encoding="utf-8")
    for path in tmp_path.iterdir():
        assert router.load_certificate(path) is None


def test_a_scan_that_no_issuer_recognises_is_not_a_statement(
    pillar2: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ocr, "read_text", lambda path: "A letter from the dentist\n")
    certificates, others = pipeline.load_certificates(pillar2.iterdir())
    assert certificates == []
    assert sorted(path.name for path in others) == ["Dokument.pdf", "Scan 0001.pdf"]
    statements, skipped = pipeline.load_files(others)
    assert statements == []
    assert sorted(skipped) == sorted(others)


def test_a_certificate_that_does_not_read_cleanly_names_its_file(
    pillar2: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = (FIXTURES / "axa" / "certificate.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(ocr, "read_text", lambda path: text.replace("35'802.00", "35'802.10"))
    with pytest.raises(ExtractionError, match=r"^Dokument\.pdf: AXA: the portions .* do not add"):
        pipeline.load_certificates(pillar2.iterdir())


def test_scans_among_the_statements_are_skipped_unopened(
    folder: Path, pillar2: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(path: Path) -> str:
        raise AssertionError(path)

    monkeypatch.setattr(ocr, "read_text", never)  # not even opened
    for scan in pillar2.iterdir():
        scan.rename(folder / scan.name)
    statements, skipped = pipeline.load_statements(folder)
    assert len(statements) == 6
    assert {"Dokument.pdf", "Scan 0001.pdf"} <= {path.name for path in skipped}
    extractions, skipped = _load(folder)
    month = pipeline.process(extractions, load_rules([_rules(folder)]), INSTRUMENTS, skipped)
    assert not any("Pillar" in txn.account for txn in month.transactions)
