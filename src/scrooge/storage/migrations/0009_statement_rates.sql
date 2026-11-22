-- Finanze database, migration 0009: what a statement prints about prices and rates belongs
-- to the statement, so that a staged or discarded one moves nothing.
--
-- Until now staging wrote the closes and rates a statement prints into `prices` and
-- `fx_rates` (source 'statement'), tables with no link to any statement file: a statement
-- that was staged and then discarded left them behind, and a staged one was read as if it
-- were committed. From now on a printed close is read from `positions`, which carries its
-- statement file; a printed rate is kept here, one per statement file and currency, and goes
-- with the file. Dashboards read both only for committed statements.

CREATE TABLE statement_rates (
    statement_file_id INTEGER NOT NULL REFERENCES statement_files (id) ON DELETE CASCADE,
    currency          TEXT    NOT NULL REFERENCES currencies (code) CHECK (currency <> 'CHF'),
    date              TEXT    NOT NULL CHECK (date IS date(date)),
    rate_to_chf       TEXT    NOT NULL,           -- CHF per one unit of the currency
    PRIMARY KEY (statement_file_id, currency)
) STRICT;

-- Only IBKR prints rates. A rate whose statement is not stored any more is dropped: it was
-- left behind by a statement that was discarded.
INSERT INTO statement_rates (statement_file_id, currency, date, rate_to_chf)
SELECT f.id, r.currency, r.date, r.rate_to_chf
FROM fx_rates r JOIN statement_files f ON f.period_end = r.date AND f.source = 'ibkr'
WHERE r.source = 'statement';

DELETE FROM fx_rates WHERE source = 'statement';

-- A printed close is the price of a position row of the same statement.
DELETE FROM prices WHERE source = 'statement';

-- What committed statements print, for the dashboards: the close of each share they list a
-- position of (a bill has none) and the rates of the day they end. A statement that is
-- staged, or discarded, prints nothing.
CREATE VIEW committed_closes AS
SELECT p.instrument_id AS instrument_id, p.date AS date, p.price AS close
FROM positions p
JOIN statement_files f ON f.id = p.statement_file_id
JOIN instruments i ON i.id = p.instrument_id
WHERE p.source = 'statement' AND p.price IS NOT NULL AND i.type <> 'bill'
  AND f.committed_at IS NOT NULL;

CREATE VIEW committed_rates AS
SELECT r.currency AS currency, r.date AS date, r.rate_to_chf AS rate
FROM statement_rates r
JOIN statement_files f ON f.id = r.statement_file_id
WHERE f.committed_at IS NOT NULL;
