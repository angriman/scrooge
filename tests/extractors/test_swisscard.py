from datetime import date
from decimal import Decimal
from pathlib import Path
from types import TracebackType

import pytest

from finanze.errors import ExtractionError
from finanze.extractors import swisscard
from finanze.models import CashBalance, ControlTotal, Extraction, Kind
from finanze.validation import check_balances
from tests.extractors.golden import FIXTURES, assert_matches_golden

TEXT = (FIXTURES / "swisscard" / "statement.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def result() -> Extraction:
    return swisscard.extract(TEXT, source_file="statement.txt")


def test_matches_golden_file(result: Extraction) -> None:
    assert_matches_golden(result, "swisscard")


def test_known_september_totals(result: Extraction) -> None:
    # D3: sum(purchases) - cashback == Migros debit
    cashback = sum(t.amount for t in result.transactions if t.category == "Cashback")
    purchases = -sum(t.amount for t in result.transactions if t.category != "Cashback")
    assert (purchases, cashback) == (Decimal("1355.98"), Decimal("67.80"))
    assert result.controls == (ControlTotal("Migros", "CHF", Decimal("-1288.18")),)
    check_balances(result)


def test_everything_is_booked_on_migros_in_the_invoice_month(result: Extraction) -> None:
    assert result.month == date(2026, 9, 1)
    assert {(t.account, t.currency, t.booking_date) for t in result.transactions} == {
        ("Migros", "CHF", date(2026, 9, 4))  # D4: the invoice date, not the purchase date
    }
    assert result.transactions[0].date == date(2026, 8, 4)  # D19: the day of the purchase
    assert result.transactions[0].description == "ALPENBAHN AG, BERN (04.08.2026)"
    assert result.transactions[0].counterparty == "ALPENBAHN AG"


def test_previous_payment_and_detail_lines_are_not_transactions(result: Extraction) -> None:
    descriptions = " ".join(t.description for t in result.transactions)
    assert "GRAZIE" not in descriptions
    assert "Cambio" not in descriptions
    assert len(result.transactions) == 13


def test_classification(result: Extraction) -> None:
    by_merchant = {t.counterparty: t for t in result.transactions}
    assert by_merchant["CASHBACK"].kind is Kind.INCOME
    assert (by_merchant["REVOLUT**1234*"].kind, by_merchant["REVOLUT**1234*"].category) == (
        Kind.TRANSFER,
        "Topup Revolut",
    )
    assert (by_merchant["AEROSUR"].kind, by_merchant["AEROSUR"].category) == (Kind.EXPENSE, "")
    refund = next(t for t in result.transactions if t.amount == Decimal("2.40"))
    assert refund.kind is Kind.INCOME


def test_source_refs_name_the_card_and_are_unique(result: Extraction) -> None:
    refs = [t.source_ref for t in result.transactions]
    assert len(refs) == len(set(refs))
    assert {ref.split(":")[0] for ref in refs} == {"0001", "0002", "Varie"}


def test_text_without_word_spacing_parses_the_same_markers(result: Extraction) -> None:
    glued = (
        TEXT.replace("Data della fattura", "Datadellafattura")
        .replace("Nuove transazioni", "Nuovetransazioni")
        .replace("Totale nuove transazioni", "Totalenuovetransazioni")
    )
    assert swisscard.extract(glued, source_file="statement.txt") == result


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (TEXT.replace("Data della fattura", "Invoice date"), "no invoice date"),
        (TEXT.replace("Data della fattura 04.09.2026", "Data della fattura 31.02.2026"), "date"),
        (TEXT.replace("Nuove transazioni\n", "New transactions\n"), "Nuove transazioni"),
        (TEXT.replace("Totale nuove transazioni", "Grand total"), "Totale nuove transazioni"),
        (TEXT.replace("BERN 64.00", "BERN 65.00"), "total of card 0001 is 792.33"),
        (TEXT.replace("ZÜRICH 46.50", "ZÜRICH 45.60"), "total of card 0002"),
        (TEXT.replace("CASHBACK -67.80", "CASHBACK -67.08"), "the invoice total is 1288.18"),
        (TEXT.replace("0000 00XXXX X0001 LUKAS MEIER", "Carta di LUKAS MEIER"), "before any card"),
    ],
)
def test_misread_invoices_raise(content: str, message: str) -> None:
    assert content != TEXT
    with pytest.raises(ExtractionError, match=message):
        swisscard.extract(content, source_file="statement.txt")


class _Page:
    def __init__(self, text: str | None) -> None:
        self._text = text

    def extract_text(self, x_tolerance: float) -> str | None:
        assert x_tolerance == 1.5
        return self._text


class _Pdf:
    pages = (_Page("page one"), _Page(None), _Page("page three"))

    def __enter__(self) -> "_Pdf":
        return self

    def __exit__(
        self, kind: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None


def test_read_text_joins_the_pages_of_the_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[Path] = []

    def fake_open(path: Path) -> _Pdf:
        opened.append(path)
        return _Pdf()

    monkeypatch.setattr("pdfplumber.open", fake_open)
    assert swisscard.read_text(Path("bill.pdf")) == "page one\n\npage three"
    assert opened == [Path("bill.pdf")]


# ------------------------------------------------------------------ the card as an account


@pytest.fixture(scope="module")
def card() -> Extraction:
    return swisscard.extract_account(TEXT, source_file="statement.txt")


def test_as_an_account_the_invoice_reconciles_on_its_own_balances(card: Extraction) -> None:
    assert card.month == date(2026, 9, 1)
    assert card.controls == ()
    assert card.balances == (
        CashBalance("Swisscard", "CHF", Decimal("-1142.60"), Decimal("-1288.18")),
    )
    check_balances(card)
    assert {t.account for t in card.transactions} == {"Swisscard"}


def test_as_an_account_purchases_keep_their_day_and_are_booked_on_the_invoice(
    card: Extraction, result: Extraction
) -> None:
    purchases = [t for t in card.transactions if t.kind is not Kind.INTERNAL]
    assert len(purchases) == len(result.transactions)
    first = purchases[0]
    assert (first.date, first.booking_date) == (date(2026, 8, 4), date(2026, 9, 4))
    assert (first.amount, first.counterparty) == (Decimal("-64.00"), "ALPENBAHN AG")
    same = [(t.amount, t.kind, t.category, t.description) for t in purchases]
    assert same == [(t.amount, t.kind, t.category, t.description) for t in result.transactions]


def test_as_an_account_the_payment_received_is_the_other_side_of_the_bank_debit(
    card: Extraction,
) -> None:
    payments = [t for t in card.transactions if t.kind is Kind.INTERNAL]
    assert [(t.date, t.booking_date, t.amount, t.category) for t in payments] == [
        (date(2026, 8, 26), date(2026, 9, 4), Decimal("1142.60"), "")
    ]
    assert payments[0].description.startswith("IL SUO PAGAMENTO")


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        # the payment line, the header's payments, and its new balance, each off by a cent
        ("GRAZIE 1'142.60", "GRAZIE 1'142.61", "payments 1142.61"),
        ("CHF 1'142.60 CHF 1'142.60 CHF 1'288.18", "CHF 1'142.60 CHF 1'142.61 CHF 1'288.18", "="),
        ("CHF 1'288.18 CHF 1'288.18 CHF 40.00", "CHF 1'288.18 CHF 1'288.19 CHF 40.00", "= 1288.19"),
        ("CHF 1'288.18 CHF 1'288.18 CHF 40.00", "CHF 1'288.19 CHF 1'288.19 CHF 40.00", "1288.18"),
        ("CHF 1'142.60 CHF 1'142.60 CHF", "CHF 1'142.60 1'142.60 CHF", "cannot read the balances"),
    ],
)
def test_as_an_account_the_invoice_must_add_up(old: str, new: str, message: str) -> None:
    assert old in TEXT
    with pytest.raises(ExtractionError, match=message):
        swisscard.extract_account(TEXT.replace(old, new, 1), source_file="statement.txt")
