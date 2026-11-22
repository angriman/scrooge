-- Finanze database, migration 0008: how far back each series of fetched figures was asked for.
--
-- A source may have nothing before some day (a fund listed later, a holiday stretch), and
-- the first stored day then says nothing of what was asked: without this, every run asked
-- again for the days before it. A series is `close:<instrument id>` or `rate:<currency>`;
-- `first_day` is the earliest day any request for it began on. Days before it are asked for
-- only when older history was imported since.

CREATE TABLE asked_from (
    series    TEXT NOT NULL,
    source    TEXT NOT NULL CHECK (source LIKE 'fetched:_%'),
    first_day TEXT NOT NULL CHECK (first_day IS date(first_day)),
    PRIMARY KEY (series, source)
) STRICT;

-- What was stored before this: the earlier rule asked for the days before the first stored
-- close when it came more than 10 days after the first day wanted, that is, it took the
-- request to have begun 20 days before that close.
INSERT INTO asked_from (series, source, first_day)
SELECT 'close:' || instrument_id, source, date(min(date), '-20 days')
  FROM prices WHERE source LIKE 'fetched:_%' GROUP BY instrument_id, source;

INSERT INTO asked_from (series, source, first_day)
SELECT 'rate:' || currency, source, date(min(date), '-20 days')
  FROM fx_rates WHERE source LIKE 'fetched:_%' GROUP BY currency, source;
