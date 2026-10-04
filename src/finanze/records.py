"""Normalized transactions as flat rows: what `--dump` saves and what a database would load.

One row per transaction, same columns for every row; the `SecurityTrade` columns are empty for
everything else. Values keep their types (`date`, `Decimal`, `str`), so a store can bind them
directly and the CSV dump just prints them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, fields
from datetime import date
from decimal import Decimal

from finanze.models import SecurityTrade, Transaction

Value = str | date | Decimal | None
Record = dict[str, Value]

ID = "id"
# `id` first, then every field of the widest transaction type, in declaration order.
FIELDS: tuple[str, ...] = (ID, *(field.name for field in fields(SecurityTrade)))


class DuplicateRecordError(Exception):
    """Two transactions share an id: an extractor reused a `source_ref` within one file."""


def record_id(txn: Transaction) -> str:
    """Where the row comes from. The same statement file always yields the same ids, so loading
    a month twice can replace rows instead of duplicating them."""
    return f"{txn.source_file}:{txn.source_ref}"


def record(txn: Transaction) -> Record:
    return {**dict.fromkeys(FIELDS), **asdict(txn), ID: record_id(txn)}


def records(transactions: Iterable[Transaction]) -> list[Record]:
    """One row per transaction, in order. Ids must be unique, or a keyed store would lose rows."""
    rows = [record(txn) for txn in transactions]
    seen: set[Value] = set()
    for row in rows:
        if row[ID] in seen:
            msg = f"more than one transaction has the id {row[ID]!r}"
            raise DuplicateRecordError(msg)
        seen.add(row[ID])
    return rows
