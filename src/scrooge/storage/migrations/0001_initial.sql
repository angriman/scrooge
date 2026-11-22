-- Finanze database, migration 0001. Spec v2.0: D14 to D20. SQLite 3.37+ (STRICT tables).
--
-- Conventions
--   money       INTEGER billionths of the currency unit, column names end in _nano (one
--               franc is 1000000000): statements carry up to 9 decimals, and sums stay exact.
--               The currency is on the row or on its account. Outflows are negative.
--   decimals    quantities, prices and rates are TEXT decimals ('12.5'), never REAL.
--               STRICT rejects a fractional float in a money column, but would store a
--               float given for a TEXT decimal as its text: the repository adapter is the
--               guard there (it binds Decimal only), and a test covers it.
--   dates       TEXT 'YYYY-MM-DD'; a month is the date of its first day. They sort and
--               compare as text, and strftime() groups them by week, month or year.
--   timestamps  TEXT ISO 8601, UTC.
--   computed    nothing computable is stored: converted amounts, daily balances, daily
--               positions and bill lots are derived by queries from the tables below.

-- ---------------------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------------------

-- D14: one row per person. No joint accounts.
CREATE TABLE owners (
    id   INTEGER PRIMARY KEY,
    name TEXT    NOT NULL UNIQUE
) STRICT;

CREATE TABLE currencies (
    code        TEXT    PRIMARY KEY CHECK (length(code) = 3),  -- CHF, EUR, USD
    decimals INTEGER NOT NULL CHECK (decimals BETWEEN 0 AND 9)  -- shown, not stored: 2
) STRICT;

-- An account is (owner, name): both people have a Migros and a Revolut account.
CREATE TABLE accounts (
    id          INTEGER PRIMARY KEY,
    owner_id    INTEGER NOT NULL REFERENCES owners (id),
    name        TEXT    NOT NULL,            -- 'Migros', 'IBKR USD', 'Pillar 3a'
    kind        TEXT    NOT NULL CHECK (kind IN ('bank', 'card', 'broker_cash', 'broker_securities', 'manual')),
    -- NULL only for a securities account, whose instruments carry their own currency.
    currency    TEXT    REFERENCES currencies (code),
    source      TEXT,                        -- extractor that feeds it; NULL for typed accounts
    external_id TEXT,                        -- IBKR account id or IBAN, to tell accounts apart
    UNIQUE (owner_id, name),
    CHECK ((currency IS NULL) = (kind = 'broker_securities'))
) STRICT;

-- The taxonomy is data. Rename or move a category: one row, all history follows.
-- The same name exists on both sides (Trading, Commissioni, Investimenti) and as both a
-- parent and its leaf (Regali, Investimenti), hence the composite uniqueness.
CREATE TABLE categories (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    parent_id  INTEGER REFERENCES categories (id),  -- NULL: top level
    side       TEXT    NOT NULL CHECK (side IN ('uscite', 'entrate', 'transfer')),
    sort_order INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE UNIQUE INDEX categories_name ON categories (side, coalesce(parent_id, 0), name);

-- Absorbs instruments.yaml. Options and futures are not instruments (D20).
CREATE TABLE instruments (
    id           INTEGER PRIMARY KEY,
    symbol       TEXT NOT NULL,              -- ticker, or CUSIP for a bill
    name         TEXT NOT NULL DEFAULT '',
    type         TEXT NOT NULL CHECK (type IN ('etf', 'stock', 'fund', 'bill', 'bond')),
    currency     TEXT NOT NULL REFERENCES currencies (code),
    price_symbol TEXT,                       -- Yahoo symbol; NULL when no price is fetched (bills)
    maturity     TEXT CHECK (maturity IS date(maturity)),  -- bills and bonds
    UNIQUE (symbol, currency)
) STRICT;

-- A split, so that quantities held before it match split-adjusted prices (D20).
CREATE TABLE splits (
    instrument_id INTEGER NOT NULL REFERENCES instruments (id),
    date          TEXT    NOT NULL CHECK (date IS date(date)),
    numerator     INTEGER NOT NULL CHECK (numerator > 0),    -- 4-for-1: 4
    denominator   INTEGER NOT NULL CHECK (denominator > 0),  --           1
    PRIMARY KEY (instrument_id, date)
) STRICT;

-- ---------------------------------------------------------------------------------------
-- Import and closing: the only place where the month exists (D17)
-- ---------------------------------------------------------------------------------------

-- A month of one person. Dashboards read committed months only.
CREATE TABLE months (
    owner_id     INTEGER NOT NULL REFERENCES owners (id),
    month        TEXT    NOT NULL CHECK (month IS date(month, 'start of month')),
    status       TEXT    NOT NULL CHECK (status IN ('open', 'committed')),
    committed_at TEXT,
    PRIMARY KEY (owner_id, month),
    CHECK ((committed_at IS NOT NULL) = (status = 'committed'))
) STRICT;

-- One row per imported file. A file may cover several months (yearly exports).
CREATE TABLE statement_files (
    id           INTEGER PRIMARY KEY,
    owner_id     INTEGER NOT NULL REFERENCES owners (id),
    source       TEXT    NOT NULL,           -- migros, revolut, revolut_savings, ibkr, ...
    filename     TEXT    NOT NULL,
    sha256       TEXT    NOT NULL,
    period_start TEXT    NOT NULL CHECK (period_start IS date(period_start)),
    period_end   TEXT    NOT NULL CHECK (period_end IS date(period_end)),
    reconciled   TEXT    NOT NULL CHECK (reconciled IN ('yes', 'nothing_to_check')),
    imported_at  TEXT    NOT NULL,
    UNIQUE (owner_id, sha256),
    CHECK (period_start <= period_end)
) STRICT;

-- "No activity this month" for a source that is normally expected.
CREATE TABLE source_waivers (
    owner_id INTEGER NOT NULL,
    month    TEXT    NOT NULL,
    source   TEXT    NOT NULL,
    note     TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (owner_id, month, source),
    FOREIGN KEY (owner_id, month) REFERENCES months (owner_id, month)
) STRICT;

-- ---------------------------------------------------------------------------------------
-- Transactions
-- ---------------------------------------------------------------------------------------

CREATE TABLE transactions (
    -- D15: never contains the file name. Built per source from the account and the source's
    -- own reference; 'manual:<uuid>' for typed rows. Exact form per source: open, phase 1.
    id                    TEXT    PRIMARY KEY,
    account_id            INTEGER NOT NULL REFERENCES accounts (id),
    -- D19: the two dates. Equal for most rows.
    date                  TEXT    NOT NULL CHECK (date IS date(date)),                  -- real: view, query, cash flow
    booking_date          TEXT    NOT NULL CHECK (booking_date IS date(booking_date)),  -- in the statement period: reconcile
    amount_nano          INTEGER NOT NULL,   -- signed, native currency
    currency              TEXT    NOT NULL REFERENCES currencies (code),
    description           TEXT    NOT NULL,
    counterparty          TEXT    NOT NULL DEFAULT '',
    kind                  TEXT    NOT NULL CHECK (kind IN (
                              'expense', 'income', 'transfer', 'fx', 'trading', 'commission',
                              'interest', 'dividend', 'tax', 'security_trade', 'internal')),
    -- The category in force (an override is already applied). NULL: not yet categorized,
    -- which blocks the commit of its month.
    category_id           INTEGER REFERENCES categories (id),
    category_source       TEXT    CHECK (category_source IN ('rule', 'manual')),
    -- D14: set when the counterparty is the other person. Such a row counts as income or
    -- expense in a single-person view and is left out of both in the combined view.
    counterparty_owner_id INTEGER REFERENCES owners (id),
    statement_file_id     INTEGER REFERENCES statement_files (id),  -- NULL: typed by hand
    source_ref            TEXT    NOT NULL DEFAULT '',              -- row or line in that file
    confidence            TEXT    NOT NULL DEFAULT '1',
    -- Set on the interest part split off a bill maturity or sale (D13): the row it was taken
    -- from, which keeps the cost of the lots. The two add up to what the statement says.
    split_of              TEXT    REFERENCES transactions (id) ON DELETE CASCADE,
    CHECK ((category_id IS NULL) = (category_source IS NULL))
) STRICT;
CREATE INDEX transactions_date         ON transactions (date);
CREATE INDEX transactions_booking_date ON transactions (booking_date);
CREATE INDEX transactions_account      ON transactions (account_id, date);
CREATE INDEX transactions_category     ON transactions (category_id, date);
CREATE INDEX transactions_file         ON transactions (statement_file_id);
CREATE INDEX transactions_split        ON transactions (split_of);

-- The security side of a transaction: a buy or sell (kind 'security_trade'), or an in-kind
-- move between accounts (kind 'transfer', amount 0, quantity signed). Positions on any day
-- and bill lots (FIFO) are derived from these rows.
CREATE TABLE trades (
    transaction_id TEXT    PRIMARY KEY REFERENCES transactions (id) ON DELETE CASCADE,
    instrument_id  INTEGER NOT NULL REFERENCES instruments (id),
    quantity       TEXT    NOT NULL,          -- signed, sells negative
    price          TEXT,                      -- NULL for an in-kind move
    cost_nano     INTEGER NOT NULL DEFAULT 0 -- total paid, in the transaction's currency
) STRICT;
CREATE INDEX trades_instrument ON trades (instrument_id);

-- A manual category or kind for one transaction. No foreign key on purpose: re-importing a
-- file replaces its transactions, and the override is applied again to the new row.
CREATE TABLE overrides (
    transaction_id TEXT    PRIMARY KEY,
    category_id    INTEGER REFERENCES categories (id),
    kind           TEXT,
    note           TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL,
    CHECK (category_id IS NOT NULL OR kind IS NOT NULL)
) STRICT;

-- ---------------------------------------------------------------------------------------
-- Known points for values: what statements print and what is typed
-- ---------------------------------------------------------------------------------------

-- Cash of an account on a day: statement period ends, the opening snapshot at the cut-over,
-- and every typed value (pillar 3a). The balance on any other day is the latest row on or
-- before it plus the transactions since; for a 'manual' account it is carried flat.
CREATE TABLE balances (
    account_id        INTEGER NOT NULL REFERENCES accounts (id),
    date              TEXT    NOT NULL CHECK (date IS date(date)),
    amount_nano      INTEGER NOT NULL,
    source            TEXT    NOT NULL CHECK (source IN ('statement', 'manual')),
    statement_file_id INTEGER REFERENCES statement_files (id),
    PRIMARY KEY (account_id, date)
) STRICT;

-- Holdings as a statement prints them (or as typed for the opening snapshot): checked
-- against the quantity derived from trades. value_nano is also a known point for a bill.
CREATE TABLE positions (
    account_id        INTEGER NOT NULL REFERENCES accounts (id),
    instrument_id     INTEGER NOT NULL REFERENCES instruments (id),
    date              TEXT    NOT NULL CHECK (date IS date(date)),
    quantity          TEXT    NOT NULL,
    price             TEXT,
    value_nano       INTEGER,                -- in the instrument's currency
    source            TEXT    NOT NULL CHECK (source IN ('statement', 'manual')),
    statement_file_id INTEGER REFERENCES statement_files (id),
    PRIMARY KEY (account_id, instrument_id, date)
) STRICT;

-- D20: one close per instrument and trading day. The same day may have a row per source;
-- precedence when valuing is manual, then statement, then fetched (D10).
CREATE TABLE prices (
    instrument_id INTEGER NOT NULL REFERENCES instruments (id),
    date          TEXT    NOT NULL CHECK (date IS date(date)),
    close         TEXT    NOT NULL,           -- in the instrument's currency, split-adjusted
    source        TEXT    NOT NULL CHECK (source IN ('statement', 'manual') OR source LIKE 'fetched:_%'),
    fetched_at    TEXT,
    PRIMARY KEY (instrument_id, date, source)
) STRICT;

-- D20: CHF per one unit of the currency, per day. EUR and USD views are crossed through CHF.
CREATE TABLE fx_rates (
    date        TEXT NOT NULL CHECK (date IS date(date)),
    currency    TEXT NOT NULL REFERENCES currencies (code) CHECK (currency <> 'CHF'),
    rate_to_chf TEXT NOT NULL,
    source      TEXT NOT NULL CHECK (source IN ('statement', 'manual') OR source LIKE 'fetched:_%'),
    fetched_at  TEXT,
    PRIMARY KEY (date, currency, source)
) STRICT;

-- ---------------------------------------------------------------------------------------
-- What the dashboards read
-- ---------------------------------------------------------------------------------------

-- Transactions of committed months only, with the names a client needs. The month a row
-- belongs to for closing is the month of its booking_date.
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
JOIN months m     ON m.owner_id = a.owner_id
                 AND m.month = date(t.booking_date, 'start of month')
                 AND m.status = 'committed'
LEFT JOIN categories c ON c.id = t.category_id
LEFT JOIN categories p ON p.id = c.parent_id
LEFT JOIN owners co    ON co.id = t.counterparty_owner_id;
