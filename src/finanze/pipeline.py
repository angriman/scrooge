"""One month, end to end: extract, reconcile, classify, aggregate. Nothing here writes."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from finanze import classification, router
from finanze.aggregate import Budget, aggregate
from finanze.classification import Instrument, Rule
from finanze.errors import ExtractionError
from finanze.extractors.common import month_end, month_start
from finanze.models import Extraction, Kind, SecurityTrade, Transaction
from finanze.validation import CardBillMismatchError, check_balances, check_card_bill

CARD_SOURCE = "swisscard"
CARD_ACCOUNT = "Migros"  # the bank account the card is paid from
_SLACK = timedelta(days=7)  # between the bank's debit and the day the card books the payment


@dataclass(frozen=True, slots=True)
class Month:
    """Everything known about one month after reconciliation and classification."""

    month: date
    transactions: tuple[Transaction, ...]
    unreconciled: tuple[str, ...]  # accounts the statements give no figure to check against
    skipped: tuple[Path, ...]  # files in the folder that are not statements
    # Last day covered when the statements cover more than one month (database imports only).
    end: date | None = None

    @property
    def period(self) -> tuple[date, date]:
        return self.month, self.end or month_end(self.month)

    @property
    def uncategorized(self) -> tuple[Transaction, ...]:
        return classification.uncategorized(self.transactions)

    @property
    def trades(self) -> tuple[SecurityTrade, ...]:
        """Buys and sells, for the asset tabs. A bill maturity is income and is not listed."""
        return tuple(
            t
            for t in self.transactions
            if isinstance(t, SecurityTrade) and t.kind is Kind.SECURITY_TRADE
        )

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


@dataclass(frozen=True, slots=True)
class Statement:
    """One statement file and what was extracted from it."""

    source: str
    filename: str
    sha256: str
    extraction: Extraction


def relabel(statements: Sequence[Statement], labels: Mapping[str, str]) -> list[Statement]:
    """Rename the accounts of statements whose `account_id` has a label of its own.

    A second account at one broker would otherwise share the names of the first:
    `{"U7654321": "IBKR 2"}` turns that account's `IBKR USD` into `IBKR 2 USD`.
    """
    renamed: list[Statement] = []
    for statement in statements:
        extraction = statement.extraction
        label = labels.get(extraction.account_id)
        if label is None:
            renamed.append(statement)
            continue

        def name(account: str, label: str = label) -> str:
            return f"{label} {account.rpartition(' ')[2]}"

        rows = extraction.transactions
        relabelled = replace(
            extraction,
            transactions=tuple(replace(t, account=name(t.account)) for t in rows),
            balances=tuple(replace(b, account=name(b.account)) for b in extraction.balances),
            controls=tuple(replace(c, account=name(c.account)) for c in extraction.controls),
            unreconciled=tuple(name(account) for account in extraction.unreconciled),
        )
        renamed.append(replace(statement, extraction=relabelled))
    return renamed


def load_folder(folder: Path) -> tuple[list[tuple[str, Extraction]], list[Path]]:
    """Extract every statement in `folder`; returns them plus the files that were skipped."""
    statements, skipped = load_statements(folder)
    return [(s.source, s.extraction) for s in statements], skipped


def load_statements(folder: Path, *, database: bool = False) -> tuple[list[Statement], list[Path]]:
    """Like `load_folder`, keeping for each statement the file it came from."""
    files = (p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
    return load_files(files, database=database)


def load_files(
    paths: Iterable[Path], *, database: bool = False
) -> tuple[list[Statement], list[Path]]:
    """Extract each of `paths`; returns the statements plus the files that are not statements.

    `database` selects how sources that differ between the two destinations are read: see
    `router.load`.

    Every statement gets a name of its own: the file's name, with as many of its folders in
    front as it takes to tell it from the others (monthly invoices are all `swisscard.pdf`).
    """
    statements: list[Statement] = []
    skipped: list[Path] = []
    for path, name in sorted(_names(paths).items()):
        try:
            loaded = router.load(path, name, database=database)
        except ExtractionError as exc:
            msg = f"{name}: {exc}"
            raise type(exc)(msg) from exc
        if loaded is None:
            skipped.append(path)
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            statements.append(Statement(loaded[0], name, digest, loaded[1]))
    return statements, skipped


def _names(paths: Iterable[Path]) -> dict[Path, str]:
    names = {path: path.name for path in paths}  # a path given twice counts once
    depth = 1
    while len(set(names.values())) < len(names):  # two paths always differ somewhere
        depth += 1
        counts = Counter(names.values())
        for path, name in names.items():
            parts = path.parts[-depth:]
            if counts[name] > 1 and len(parts) == depth:
                names[path] = "/".join(parts)
    return names


def process(
    extractions: Sequence[tuple[str, Extraction]],
    rules: Sequence[Rule],
    instruments: dict[str, Instrument],
    skipped: Iterable[Path] = (),
    *,
    database: bool = False,
) -> Month:
    """Reconcile every statement, then classify. Any reconciliation failure raises.

    For the files to paste, the statements must all cover the same period and each card
    invoice must equal the bank's card debit of its month (D3). For the `database` each
    statement may cover its own period, the result spans from the first day any of them
    covers to the last, and the card is an account: its payments are matched to the bank's.
    """
    periods = sorted({extraction.period for _, extraction in extractions})
    if not periods or (not database and len(periods) != 1):
        found = ", ".join(_describe(period) for period in periods) or "none"
        msg = f"statements must all cover the same month, found: {found}"
        raise ExtractionError(msg)
    first, last = periods[0][0], max(end for _, end in periods)
    for _, extraction in extractions:
        check_balances(extraction)
    if database:
        _match_card_payments(extractions)
    else:
        _check_card_bills(extractions)

    everything = [txn for _, extraction in extractions for txn in extraction.transactions]
    transactions = classification.resolve_instruments(
        classification.classify(everything, rules), instruments
    )
    unreconciled = sorted({name for _, e in extractions for name in e.unreconciled})
    end = None if last == month_end(first) else last
    return Month(first, transactions, tuple(unreconciled), tuple(skipped), end)


def _check_card_bills(extractions: Sequence[tuple[str, Extraction]]) -> None:
    """D3, month by month: each card invoice against the bank's card debit of its month."""
    debits: dict[date, list[Decimal]] = defaultdict(list)
    invoices: dict[date, list[Decimal]] = defaultdict(list)
    for source, extraction in extractions:
        if source == CARD_SOURCE:
            invoices[extraction.month] += [control.total for control in extraction.controls]
            continue
        for txn in extraction.transactions:
            if txn.kind is Kind.INTERNAL and txn.account == CARD_ACCOUNT:
                debits[month_start(txn.booking_date)].append(txn.amount)
    for month in sorted({*debits, *invoices}):
        try:
            check_card_bill(_total(debits[month]), _total(invoices[month]))
        except CardBillMismatchError as exc:
            msg = f"{month:%Y-%m}: {exc}"
            raise CardBillMismatchError(msg) from None


def _match_card_payments(extractions: Sequence[tuple[str, Extraction]]) -> None:
    """Every payment a card invoice lists must be a card debit on the bank, and the reverse.

    The two are a few days apart at most. A payment from before the bank statements start, or
    a debit no invoice can list yet, has nothing to be matched with and is left alone.
    """
    debits: list[Transaction] = []
    payments: list[Transaction] = []
    covered: list[tuple[date, date]] = []  # by the bank statements
    invoiced = date.min  # the month of the latest invoice
    for source, extraction in extractions:
        internal = [t for t in extraction.transactions if t.kind is Kind.INTERNAL]
        if source == CARD_SOURCE:
            payments += internal
            invoiced = max(invoiced, extraction.month)
        else:
            debits += [t for t in internal if t.account == CARD_ACCOUNT]
            covered += [extraction.period for b in extraction.balances if b.account == CARD_ACCOUNT]
    for payment in sorted(payments, key=lambda t: t.date):
        near = [
            debit
            for debit in debits
            if debit.amount == -payment.amount and abs(debit.booking_date - payment.date) <= _SLACK
        ]
        if near:
            debits.remove(min(near, key=lambda t: abs(t.booking_date - payment.date)))
        elif any(first <= payment.date <= last for first, last in covered):
            msg = (
                f"a card invoice lists a payment of {payment.amount} on {payment.date}, "
                "but the bank statement has no such card debit"
            )
            raise CardBillMismatchError(msg)
    for debit in debits:
        if month_start(debit.booking_date) < invoiced:
            msg = (
                f"the bank debited {-debit.amount} for the card on {debit.booking_date}, "
                "but no card invoice lists that payment"
            )
            raise CardBillMismatchError(msg)


def _describe(period: tuple[date, date]) -> str:
    first, last = period
    return f"{first:%Y-%m}" if last == month_end(first) else f"{first:%Y-%m} to {last:%Y-%m}"


def _total(amounts: list[Decimal]) -> Decimal | None:
    return sum(amounts, Decimal(0)) if amounts else None
