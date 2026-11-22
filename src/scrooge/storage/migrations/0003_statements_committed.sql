-- Finanze database, migration 0003: statements are committed, not months (decided 2026-10-16).
--
-- An import stages statement files; a commit makes them, and everything they gave the
-- accounts, visible to the dashboards. There is no month to close any more: a person commits
-- whatever was imported, every week or every other month. `committed_at` is NULL while a
-- file is staged. A file that was committed with its months keeps their commit time; one
-- that touches a month still open stays staged.

ALTER TABLE statement_files ADD COLUMN committed_at TEXT;

-- The institution's own number for the account the statement is of (an IBKR account, an
-- IBAN), where it prints one: a statement of another person's account is refused. Files
-- imported before this migration have none.
ALTER TABLE statement_files ADD COLUMN account_number TEXT NOT NULL DEFAULT '';

UPDATE statement_files SET committed_at = (
    SELECT max(m.committed_at) FROM months m
    WHERE m.owner_id = statement_files.owner_id
      AND m.month BETWEEN date(statement_files.period_start, 'start of month')
                      AND statement_files.period_end
)
WHERE NOT EXISTS (
    SELECT 1 FROM months m
    WHERE m.owner_id = statement_files.owner_id
      AND m.month BETWEEN date(statement_files.period_start, 'start of month')
                      AND statement_files.period_end
      AND m.status = 'open'
);

-- The accounts a statement covers: what is committed of an account, and from which day a
-- newer statement of the same source has something to add, is read from here.
CREATE TABLE statement_accounts (
    statement_file_id INTEGER NOT NULL REFERENCES statement_files (id) ON DELETE CASCADE,
    account_id        INTEGER NOT NULL REFERENCES accounts (id),
    PRIMARY KEY (statement_file_id, account_id)
) STRICT;

INSERT OR IGNORE INTO statement_accounts (statement_file_id, account_id)
SELECT statement_file_id, account_id FROM transactions WHERE statement_file_id IS NOT NULL
UNION SELECT statement_file_id, account_id FROM balances WHERE statement_file_id IS NOT NULL
UNION SELECT statement_file_id, account_id FROM positions WHERE statement_file_id IS NOT NULL;

DROP TABLE source_waivers;
DROP TABLE months;

-- Transactions of committed statements, and those typed by hand, with the names a client
-- needs.
DROP VIEW committed_transactions;
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
       c.name  AS category,
       p.name  AS category_parent,
       c.side  AS category_side,
       co.name AS counterparty_owner
FROM transactions t
JOIN accounts a   ON a.id = t.account_id
JOIN owners o     ON o.id = a.owner_id
LEFT JOIN statement_files f ON f.id = t.statement_file_id
LEFT JOIN categories c ON c.id = t.category_id
LEFT JOIN categories p ON p.id = c.parent_id
LEFT JOIN owners co    ON co.id = t.counterparty_owner_id
WHERE t.statement_file_id IS NULL OR f.committed_at IS NOT NULL;
