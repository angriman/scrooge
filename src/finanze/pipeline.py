"""Statements, end to end: extract, reconcile, classify. Nothing here writes."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

from finanze import classification, router
from finanze.classification import Instrument, Rule
from finanze.errors import ExtractionError
from finanze.extractors.common import month_end, month_start
from finanze.models import Extraction, Kind, PensionCertificate, SecurityTrade, Transaction
from finanze.validation import CardBillMismatchError, check_balances

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
    # Last day covered; None: the end of `month`.
    end: date | None = None
    # First day covered; None: the first of `month`.
    start: date | None = None

    @property
    def period(self) -> tuple[date, date]:
        return self.start or self.month, self.end or month_end(self.month)

    @property
    def uncategorized(self) -> tuple[Transaction, ...]:
        return classification.uncategorized(self.transactions)


@dataclass(frozen=True, slots=True)
class Statement:
    """One statement file and what was extracted from it."""

    source: str
    filename: str
    sha256: str
    extraction: Extraction


@dataclass(frozen=True, slots=True)
class Certificate:
    """One scanned pension certificate and what was read off it."""

    filename: str
    sha256: str
    account: str  # the pension account it states the balance of
    adds_up: bool  # False: nothing on the certificate checks the figure that was read
    stated: PensionCertificate


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
            holdings=extraction.holdings
            and tuple(replace(h, account=name(h.account)) for h in extraction.holdings),
        )
        renamed.append(replace(statement, extraction=relabelled))
    return renamed


def resolve_holdings(
    statements: Sequence[Statement], instruments: Mapping[str, Instrument]
) -> list[Statement]:
    """Apply `instruments.yaml` to what statements say was held, as `process` does to trades."""
    resolved: list[Statement] = []
    for statement in statements:
        held = statement.extraction.holdings
        if held:
            named = tuple(classification.resolve_holding(h, instruments) for h in held)
            statement = replace(statement, extraction=replace(statement.extraction, holdings=named))  # noqa: PLW2901  # the same statement, with its instruments named
        resolved.append(statement)
    return resolved


def load_statements(folder: Path) -> tuple[list[Statement], list[Path]]:
    """Extract every statement in `folder`; returns them plus the files that were skipped."""
    files = (p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
    return load_files(files)


def load_files(paths: Iterable[Path]) -> tuple[list[Statement], list[Path]]:
    """Extract each of `paths`; returns the statements plus the files that are not statements.

    Every statement gets a name of its own: the file's name, with as many of its folders in
    front as it takes to tell it from the others (monthly invoices are all `swisscard.pdf`).
    """
    statements: list[Statement] = []
    skipped: list[Path] = []
    for path, name in sorted(_names(paths).items()):
        try:
            loaded = router.load(path, name)
        except ExtractionError as exc:
            msg = f"{name}: {exc}"
            raise type(exc)(msg) from exc
        if loaded is None:
            skipped.append(path)
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            statements.append(Statement(loaded[0], name, digest, loaded[1]))
    return statements, skipped


def load_certificates(paths: Iterable[Path]) -> tuple[list[Certificate], list[Path]]:
    """Read the scanned pension certificates among `paths`; returns them plus the other paths.

    `load_files` skips every scan unopened. Certificates are named as `load_files` names
    statements.
    """
    certificates: list[Certificate] = []
    others: list[Path] = []
    for path, name in sorted(_names(paths).items()):
        try:
            scanned = router.load_certificate(path)
        except ExtractionError as exc:
            msg = f"{name}: {exc}"
            raise type(exc)(msg) from exc
        if scanned is None:
            others.append(path)
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            account, adds_up, stated = scanned
            certificates.append(Certificate(name, digest, account, adds_up, stated))
    return certificates, others


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
) -> Month:
    """Reconcile every statement, then classify. Any reconciliation failure raises.

    Each statement may cover its own period, the result spans from the first day any of them
    covers to the last, and the card is an account: its payments are matched to the bank's.
    """
    periods = sorted({extraction.period for _, extraction in extractions})
    if not periods:
        msg = "there are no statements to process"
        raise ExtractionError(msg)
    first, last = periods[0][0], max(end for _, end in periods)
    for _, extraction in extractions:
        check_balances(extraction)
    _match_card_payments(extractions)

    everything = _with_known_maturities(
        txn for _, extraction in extractions for txn in extraction.transactions
    )
    transactions = classification.resolve_instruments(
        classification.classify(everything, rules), instruments
    )
    unreconciled = sorted({name for _, e in extractions for name in e.unreconciled})
    end = None if last == month_end(first) else last
    start = None if first.day == 1 else first
    return Month(month_start(first), transactions, tuple(unreconciled), tuple(skipped), end, start)


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


def _with_known_maturities(transactions: Iterable[Transaction]) -> list[Transaction]:
    """Give a bill bought without a maturity date the one another row has for it.

    Tastytrade's export says when a bill matures only on the maturity row itself, and IBKR
    says it for every bill: either one settles it for the same bill's other trades.
    """
    rows = list(transactions)
    known = {
        row.ticker: row.maturity
        for row in rows
        if isinstance(row, SecurityTrade) and row.maturity is not None
    }
    return [
        replace(row, maturity=known[row.ticker])
        if isinstance(row, SecurityTrade) and row.maturity is None and row.ticker in known
        else row
        for row in rows
    ]
