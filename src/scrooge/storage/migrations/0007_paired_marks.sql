-- Scrooge database, migration 0007: which counterparty marks come from pairing.
--
-- A row the statement itself says is money from or to the other person carries
-- `counterparty_owner_id` from the import. The other half of such a payment, which names
-- nobody, is marked by pairing (`storage/pairs.py`) with the row of the other person that
-- does. A mark of the second kind is derived data: it must go when its partner does, so it
-- is told apart from the first. Marks made before this column existed stay as they were.

ALTER TABLE transactions ADD COLUMN counterparty_paired INTEGER NOT NULL DEFAULT 0
    CHECK (counterparty_paired IN (0, 1));
