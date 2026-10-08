-- Finanze database, migration 0002: what a statement says about holdings and their worth,
-- kept to check the values derived from transactions and prices (plan phase 4).

-- 'printed': the statement lists what its accounts held at the end of its period (IBKR's Open
-- Positions), as rows of `positions`; an instrument without a row was not held. Files
-- imported before this migration say 'not_printed' until they are imported again.
ALTER TABLE statement_files ADD COLUMN holdings TEXT NOT NULL DEFAULT 'not_printed'
    CHECK (holdings IN ('printed', 'not_printed'));

-- 1: the quantity includes shares awarded and not yet vested (IBKR lists them from the day
-- they are granted). They are counted when they vest, so this row is no known point for the
-- quantity held; its price still is the close of that day.
ALTER TABLE positions ADD COLUMN unvested INTEGER NOT NULL DEFAULT 0 CHECK (unvested IN (0, 1));

-- The statement's own valuation at the end of its period, line by line (IBKR's Net Asset
-- Value: Cash, Stock, Bonds, ...), in the currency the statement reports in.
CREATE TABLE statement_values (
    statement_file_id INTEGER NOT NULL REFERENCES statement_files (id) ON DELETE CASCADE,
    label             TEXT    NOT NULL,
    currency          TEXT    NOT NULL REFERENCES currencies (code),
    value_nano        INTEGER NOT NULL,
    PRIMARY KEY (statement_file_id, label)
) STRICT;

-- prices.close is the price of the day itself, as it traded: not adjusted for later splits,
-- unlike what migration 0001 says. A close adjusted when fetched would stop matching the
-- closes fetched after the next split; a fetched close is turned back with the splits known
-- that day. Quantities follow instead: `splits` multiplies what is held on the day of a split.
