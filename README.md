# finanze-parser

Parses bank and broker statements, reconciles every account against the statement's own
balances, and stores the transactions in a local SQLite database, shown by a small web app.
Nothing is sent anywhere.

Sources: Migros Bank (CSV), Revolut accounts and Revolut Savings (TSV/CSV), IBKR activity
statement (CSV), Tastytrade transactions (CSV), Swisscard invoice (PDF).

## Use

```sh
uv sync
uv run finanze import statements/2026-09     # reconcile, classify, stage the month
uv run finanze import statements/2026-09 --review   # answer once per unknown merchant; saved as rules
uv run finanze commit 2026-09                # make it final
uv run finanze serve                         # the web app
```

Files are recognised by their content. The import stops if any account does not reconcile.
See "The database" below.

## Configuration

| File | Committed | Purpose |
|---|---|---|
| `rules.yaml` | no | description → category rules, generic words and your own (shops, chains, employer); `--review` and the import page append here; start from `examples/rules.yaml` |
| `rules.<owner>.yaml` | no | one person's rules for rows naming either person (transfers between the two, own top-ups); read first; `--review` saves those answers here |
| `categories.yaml` | no | categories added to the built-in taxonomy; `--review` appends here on `+Name`; read from the folder of the first rules file; see `examples/categories.yaml` |
| `instruments.yaml` | no | asset tab for Tastytrade tickers, maturity of T-bills bought there, overrides; start from `examples/instruments.yaml` |
| `finanze.yaml` | no | database, owners, account labels, web app |

Keys of older configs that nothing reads any more (`locale`, `accounts`) are ignored.

## The database

Each month is stored in a local SQLite database, one row per transaction:

```
finanze import <folder>    # reconcile, classify, then stage the month
finanze commit 2026-09     # make it final; a copy of the database is saved first
finanze reopen 2026-09     # take it back, to import it again
finanze query --from 2026  # find committed transactions (see "Querying it" below)
```

`finanze import --review` asks for the category of unmatched rows.
Answer with a category's number, its name or a part of it (`rist` is enough for `Ristoranti`;
when several match you are asked which one); `?` lists the categories again.
Answer `+Prestiti` to create a category that does not exist yet and use it (`+Casa:Giardino`
puts an expense under the top-level category `Casa`; money received goes to the Entrate, which
have no groups). It is saved in `categories.yaml`.
A statement may cover several months (the pillar 3a report covers the account's whole
history): keep it in a folder of its own, and give `commit` and `reopen` the first and the
last month, e.g. `finanze commit 2022-09 2026-07`.

A month belongs to one person. `finanze import` replaces whatever an earlier import of the same
statements stored, and refuses a month that is already committed. A commit is refused while
any transaction lacks a category, or when a statement's balance does not follow from the
previous statement's balance plus what was booked in between.

Each transaction has two dates: `date`, the day it happened, and `booking_date`, the day it
counts on for its statement (a card purchase made in August and billed in September has
both). Amounts are whole billionths of the currency unit (`amount_nano`).

`finanze.yaml` gains:

```yaml
database: ~/Documents/Finanza/finanze.db
owner: Lukas                       # whose statements these are; or pass --owner
owners:                            # how each person appears as a counterparty
  Lukas: {match: ["LUKAS MEIER"]}
  Anna: {match: ["ANNA MEIER", "CH00 0000 0000"]}
```

Income or expenses whose counterparty is the other person are marked, so that a combined
view can leave them out.

More for the database:

- `finanze import` takes any number of files and folders, each statement with its own period:
  yearly broker files next to monthly card invoices.
- The credit card is an account of its own there. Each invoice reconciles on its balances, and
  every payment it lists must be a card debit on the bank within a week.
- `account_labels: {U7654321: "IBKR 2"}` in `finanze.yaml` names a second account at a broker
  by its account number (`IBKR 2 USD`).
- `finanze balance Sparkasse 2023-01-31 1234.56 EUR` types what an account without statements
  held on a day.

### Querying it

`finanze query` lists the transactions that match, oldest first, then what they add up to.
It reads committed months only, and both people's unless `--owner` names one. The database
is the one in `finanze.yaml`, or `--db`.

```sh
finanze query --from 2026-09 --to 2026-09             # one month
finanze query --from 2025 --category Ristoranti       # a category since January 2025
finanze query --from 2025 --category Casa --summary   # a top-level category, totals only
finanze query --owner Anna --account Revolut --kind income
finanze query --text "meier" --min 500                # by counterparty or description
```

Every filter is optional and they combine; without any, everything committed is listed.

| Option | Matches |
|---|---|
| `--from`, `--to` | a day, a month or a year (`2025-05-17`, `2025-05`, `2025`), both included: `--to 2025` runs through 31 December |
| `--booking` | compare (and sort, group and print) the booking date instead of the day it happened |
| `--owner` | one person's transactions |
| `--account` | one account, by its exact name |
| `--category` | a category by its exact name; a top-level one stands for everything under it |
| `--kind` | `expense`, `income`, `transfer`, `fx`, `trading`, `commission`, `interest`, `dividend`, `tax`, `security_trade` or `internal` |
| `--min`, `--max` | the size of the amount whatever its sign: `--min 500` finds a 500 expense and a 500 income |
| `--text` | the text as typed, anywhere in the counterparty or the description, ignoring case |
| `--summary` | print the totals without the rows |

An owner, account or category the database does not have is an error, not an empty result, so
a typo cannot read as "no transactions".

Each row shows the date, owner, account, amount, currency, category and the counterparty (the
description when there is none). The totals follow, with the number of rows in brackets:

```
14 transactions
Total:
  -1,234.50 CHF  (12)
     -80.00 EUR  (2)
By month:
  2026-08    -410.00 CHF  (5)
  2026-09    -824.50 CHF  (7)
  2026-09     -80.00 EUR  (2)
```

Amounts are never converted, so there is one total per currency. `By year` and `By month`
appear only when the result spans more than one.

## Development

`make check` runs ruff, `mypy --strict` and pytest with branch coverage. Real statements stay in
`statements/` (git-ignored); only redacted fixtures live in `tests/fixtures/`.

`deploy/README.md` says how the web app runs as a container on a NAS, over a copy of the
database. The image is not built by `make check`.
