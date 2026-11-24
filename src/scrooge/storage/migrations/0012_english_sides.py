"""Scrooge database, migration 0012: the sides of the taxonomy in English (decided 2026-11-25).

The two sides of the categories were `uscite` (money out) and `entrate` (money in), the last
Italian words of the schema, read by contributors who do not know them. They are `expenses`
and `income` now; `transfer` stays.

A CHECK cannot be changed in place, so `categories` is built again as SQLite says: foreign
keys off (`REBUILDS`), a new table with the new CHECK, every row copied with its id, the old
table dropped and the new one renamed. Ids, keys, names, parents and order stay as they were,
so the rows and overrides that refer to a category still do. `committed_transactions` reads
`categories`, so it is dropped first and made again after, as 0011 left it.
"""

from __future__ import annotations

import sqlite3

REBUILDS = True

_TABLE = """
CREATE TABLE new_categories (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    parent_id  INTEGER REFERENCES categories (id),  -- NULL: top level
    side       TEXT    NOT NULL CHECK (side IN ('expenses', 'income', 'transfer')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    key        TEXT    NOT NULL DEFAULT ''
) STRICT
"""

_COPY = """
INSERT INTO new_categories (id, name, parent_id, side, sort_order, key)
SELECT id, name, parent_id,
       CASE side WHEN 'uscite' THEN 'expenses' WHEN 'entrate' THEN 'income' ELSE side END,
       sort_order, key
FROM categories
"""

_VIEW = """
CREATE VIEW committed_transactions AS
SELECT t.id,
       o.name  AS owner,
       a.name  AS account,
       t.date,
       t.booking_date,
       t.amount_nano,
       t.currency,
       t.description,
       t.counterparty,
       t.kind,
       c.key   AS category,
       p.key   AS category_parent,
       c.side  AS category_side,
       co.name AS counterparty_owner
FROM transactions t
JOIN accounts a   ON a.id = t.account_id
JOIN owners o     ON o.id = a.owner_id
LEFT JOIN statement_files f ON f.id = t.statement_file_id
LEFT JOIN categories c ON c.id = t.category_id
LEFT JOIN categories p ON p.id = c.parent_id
LEFT JOIN owners co    ON co.id = t.counterparty_owner_id
WHERE t.statement_file_id IS NULL OR f.committed_at IS NOT NULL
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("DROP VIEW committed_transactions")
    conn.execute(_TABLE)
    conn.execute(_COPY)
    conn.execute("DROP TABLE categories")
    conn.execute("ALTER TABLE new_categories RENAME TO categories")
    conn.execute(
        "CREATE UNIQUE INDEX categories_key ON categories (side, coalesce(parent_id, 0), key)"
    )
    conn.execute(_VIEW)
