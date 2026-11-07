-- Finanze database, migration 0006: the logo of each instrument, kept in the database
-- (decided 2026-11-07).
--
-- The user's own files, never fetched by the app: a copy of the database shows them as the
-- database does, and its backups keep them. A holding without one wears its letters; a bill
-- wears the bill icon of the pages.

CREATE TABLE logos (
    symbol     TEXT PRIMARY KEY,                 -- as `instruments` has it: ticker, ISIN, CUSIP
    media_type TEXT NOT NULL CHECK (media_type IN ('image/svg+xml', 'image/png')),
    image      BLOB NOT NULL CHECK (length(image) BETWEEN 1 AND 262144),
    digest     TEXT NOT NULL,                    -- SHA-256 of `image`, hex: in its address
    source     TEXT NOT NULL DEFAULT '',         -- where it came from: a file's name, a page
    added_at   TEXT NOT NULL
) STRICT;
