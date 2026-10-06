"""Transaction ids that do not depend on the statement file (D15)."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable

from finanze.models import Transaction
from finanze.storage.money import to_micro

_LENGTH = 24  # hex digits: 96 bits, far beyond any collision among a lifetime of rows


def transaction_ids(owner: str, transactions: Iterable[tuple[str, Transaction]]) -> list[str]:
    """One id per (source, transaction), from what the statement says and nothing else.

    The id is a digest of owner, source, account, both dates, amount, currency and description.
    Rows identical in all of these are told apart by a counter, in the order given: they are
    interchangeable, so the order among them does not matter. Neither the file name nor the
    category takes part: a re-export under another name, or a changed rule, keeps the ids, and
    with them the manual overrides that point to them.
    """
    seen: Counter[tuple[str, ...]] = Counter()
    ids: list[str] = []
    for source, txn in transactions:
        key = (
            owner,
            source,
            txn.account,
            txn.date.isoformat(),
            txn.booking_date.isoformat(),
            str(to_micro(txn.amount)),
            txn.currency,
            txn.description,
        )
        text = "\x1f".join((*key, str(seen[key])))
        seen[key] += 1
        ids.append(hashlib.sha256(text.encode()).hexdigest()[:_LENGTH])
    return ids
