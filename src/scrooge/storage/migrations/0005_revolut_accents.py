"""Finanze database, migration 0005: Revolut's accents read as they were meant (2026-11-06).

Revolut's export encodes every accented letter twice (`CaffÃ¨` for `Caffè`) and the rows were
stored that way. The extractor now repairs the text, and with it the id of each row, which is a
digest of its description (D15). The rows of Revolut statements are given the same text and id
here, so a statement read again finds them. The id of each row is checked against what the old
text gives before it is replaced: a row whose id does not follow stops the migration.

The repair and the digest are copied, not imported, so that this migration stays as it ran.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3

from scrooge.storage.db import MigrationError

_TWICE_ENCODED = re.compile(
    "[\u00c2-\u00df][\u0080-\u00bf]"
    "|[\u00e0-\u00ef][\u0080-\u00bf]{2}"
    "|[\u00f0-\u00f4][\u0080-\u00bf]{3}"
)


def _repaired(text: str) -> str:
    def decoded(match: re.Match[str]) -> str:
        try:
            return match[0].encode("latin-1").decode("utf-8")
        except UnicodeDecodeError:
            return match[0]

    return _TWICE_ENCODED.sub(decoded, text)


def _digest(key: tuple[str, ...], counter: int) -> str:
    return hashlib.sha256("\x1f".join((*key, str(counter))).encode()).hexdigest()[:24]


def migrate(conn: sqlite3.Connection) -> None:
    """Repair the description and counterparty of every Revolut row, and its id with them."""
    rows = conn.execute(
        "SELECT t.id, o.name AS owner, f.source, a.name AS account, t.date, t.booking_date, "
        "t.amount_nano, t.currency, t.description, t.counterparty "
        "FROM transactions t JOIN statement_files f ON f.id = t.statement_file_id "
        "JOIN accounts a ON a.id = t.account_id JOIN owners o ON o.id = a.owner_id "
        "WHERE f.source = 'revolut' ORDER BY t.id"
    ).fetchall()
    for row in rows:
        description, counterparty = _repaired(row["description"]), _repaired(row["counterparty"])
        if (description, counterparty) == (row["description"], row["counterparty"]):
            continue
        fields = (
            row["owner"],
            row["source"],
            row["account"],
            row["date"],
            row["booking_date"],
            str(row["amount_nano"]),
            row["currency"],
        )
        # Rows alike in everything are told apart by a counter: find the one this row had.
        counter = next(
            (n for n in range(len(rows)) if _digest((*fields, row["description"]), n) == row["id"]),
            None,
        )
        if counter is None:
            msg = f"transaction {row['id']}: its id does not follow from what it says"
            raise MigrationError(msg)
        new = _digest((*fields, description), counter)
        conn.execute(
            "UPDATE transactions SET id = ?, description = ?, counterparty = ? WHERE id = ?",
            (new, description, counterparty, row["id"]),
        )
        conn.execute(
            "UPDATE overrides SET transaction_id = ? WHERE transaction_id = ?", (new, row["id"])
        )
