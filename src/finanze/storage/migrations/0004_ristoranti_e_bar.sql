-- Finanze database, migration 0004: the one category of `Ristoranti e bar` takes the name of
-- its group (decided 2026-11-06).
--
-- `Ristoranti` was the only category under the top-level `Ristoranti e bar`, so the pages
-- showed two names for one thing. The row is renamed where it is: the transactions and the
-- overrides that carry it point to its id and follow.

UPDATE categories SET name = 'Ristoranti e bar'
WHERE side = 'uscite' AND name = 'Ristoranti'
  AND parent_id = (
      SELECT id FROM categories
      WHERE side = 'uscite' AND parent_id IS NULL AND name = 'Ristoranti e bar'
  );
