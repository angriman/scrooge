"""One month, end to end: extract, reconcile, classify, aggregate. Nothing here writes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from finanze import classification, router
from finanze.aggregate import Budget, aggregate
from finanze.classification import Instrument, Rule
from finanze.errors import ExtractionError
from finanze.models import Extraction, Kind, SecurityTrade, Transaction
from finanze.validation import check_balances, check_card_bill

CARD_SOURCE = "swisscard"
CARD_ACCOUNT = "Migros"


@dataclass(frozen=True, slots=True)
class Month:
    """Everything known about one month after reconciliation and classification."""

    month: date
    transactions: tuple[Transaction, ...]
    unreconciled: tuple[str, ...]  # accounts the statements give no figure to check against
    skipped: tuple[Path, ...]  # files in the folder that are not statements

    @property
    def uncategorized(self) -> tuple[Transaction, ...]:
        return classification.uncategorized(self.transactions)

    @property
    def trades(self) -> tuple[SecurityTrade, ...]:
        return tuple(t for t in self.transactions if isinstance(t, SecurityTrade))

    def budget(self) -> Budget:
        """Raises `UncategorizedError` while any row still lacks a category."""
        return aggregate(self.month, self.transactions)

    def renamed(self, names: Mapping[str, str]) -> Month:
        """Use the destination's own account names (config `accounts`) where they differ."""
        if not names:
            return self
        renamed = tuple(
            replace(txn, account=names.get(txn.account, txn.account)) for txn in self.transactions
        )
        return replace(self, transactions=renamed)


def load_folder(folder: Path) -> tuple[list[tuple[str, Extraction]], list[Path]]:
    """Extract every statement in `folder`; returns them plus the files that were skipped."""
    extractions: list[tuple[str, Extraction]] = []
    skipped: list[Path] = []
    for path in sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")):
        try:
            loaded = router.load(path)
        except ExtractionError as exc:
            msg = f"{path.name}: {exc}"
            raise type(exc)(msg) from exc
        if loaded is None:
            skipped.append(path)
        else:
            extractions.append(loaded)
    return extractions, skipped


def process(
    extractions: Sequence[tuple[str, Extraction]],
    rules: Sequence[Rule],
    instruments: dict[str, Instrument],
    skipped: Iterable[Path] = (),
) -> Month:
    """Reconcile every statement, then classify. Any reconciliation failure raises."""
    months = sorted({extraction.month for _, extraction in extractions})
    if len(months) != 1:
        found = ", ".join(f"{m:%Y-%m}" for m in months) or "none"
        msg = f"statements must all cover the same month, found: {found}"
        raise ExtractionError(msg)
    for _, extraction in extractions:
        check_balances(extraction)

    everything = [txn for _, extraction in extractions for txn in extraction.transactions]
    debits = [
        txn.amount
        for source, extraction in extractions
        for txn in extraction.transactions
        if source != CARD_SOURCE and txn.kind is Kind.INTERNAL and txn.account == CARD_ACCOUNT
    ]
    invoices = [
        control.total
        for source, extraction in extractions
        if source == CARD_SOURCE
        for control in extraction.controls
    ]
    check_card_bill(_total(debits), _total(invoices))

    transactions = classification.resolve_instruments(
        classification.classify(everything, rules), instruments
    )
    unreconciled = sorted({name for _, e in extractions for name in e.unreconciled})
    return Month(months[0], transactions, tuple(unreconciled), tuple(skipped))


def _total(amounts: list[Decimal]) -> Decimal | None:
    return sum(amounts, Decimal(0)) if amounts else None
