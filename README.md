# finanze-parser

Parses a month of bank and broker statements, reconciles every account against the statement's
own balances, and saves the aggregated month as files to paste into the Finanze Google Sheet.
It never connects to the sheet.

Sources: Migros Bank (CSV), Revolut accounts and Revolut Savings (TSV/CSV), IBKR activity
statement (CSV), Tastytrade transactions (CSV), Swisscard invoice (PDF).

## Use

```sh
uv sync
uv run finanze statements/2026-09             # dry run: parse, reconcile, classify, print
uv run finanze statements/2026-09 --review    # answer once per unknown merchant; saved as rules
uv run finanze statements/2026-09 --write     # save the files to paste in output/2026-09/
```

Put one month of statements in a folder; files are recognised by their content. The run stops
if any account does not reconcile, if the Swisscard invoice does not match the Migros debit, or
while any transaction has no category.

`--write` saves these in `output/YYYY-MM/` (git-ignored; `--output` changes the folder):

| File | Paste at |
|---|---|
| `budget_uscite.tsv` | `Budget Mensile!A5` (category, account, amount) |
| `budget_entrate.tsv` | `Budget Mensile!F5` |
| `etfs.tsv`, `stocks.tsv`, `bonds.tsv` | below the trade log of that tab; only saved when the month has such trades |
| `transactions.csv` | nowhere: every transaction behind the numbers, with an `id` and, for security trades, the ticker, quantity and price |

The paste files are tab-separated with no header, so the whole file pastes as cells. In the
sheet: set `Budget Mensile!A1` to the first day of the month, clear the old rows, paste with
**values only** (Cmd+Shift+V) so the dropdowns survive, review, then click **Archivia Mese**.
Nothing checks what the sheet already holds: pasting a month's trades twice duplicates them,
and an account name that is not in `Cash!A2:A` only shows when you archive.

`--dump` saves `transactions.csv` alone, also while some transactions still lack a category.

## Configuration

| File | Committed | Purpose |
|---|---|---|
| `rules.yaml` | yes | generic word → category rules ("ristorante", "apotheke"); names no business |
| `rules.local.yaml` | no | your own rules (shops, chains, people, employer); read first; `--review` appends here |
| `instruments.yaml` | yes | asset tab for Tastytrade tickers, maturity of T-bills bought there, overrides |
| `finanze.yaml` | no | sheet locale, account names |

`finanze.yaml`:

```yaml
locale: it      # the sheet's locale (File > Settings): it = 1234,56 (default), en = 1234.56
accounts:       # only where the name in Cash!A2:A differs from the parser's
  Tastytrade: "Tastytrade (USD)"
```

With the wrong locale the sheet takes the pasted amounts for text.

## The database

The same month can be stored in a local SQLite database, one row per transaction:

```
finanze import <folder>    # reconcile, classify, then stage the month
finanze commit 2026-09     # make it final; a copy of the database is saved first
finanze reopen 2026-09     # take it back, to import it again
```

`finanze import --review` asks for the category of unmatched rows, as `finanze --review` does.
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

## Using the transactions elsewhere

The paste files are one consumer of the pipeline; anything else (a database loader, later)
starts from the same reconciled month and never needs to touch an extractor:

```python
extractions, skipped = pipeline.load_folder(folder)  # parse every statement
month = pipeline.process(extractions, rules, instruments, skipped)  # reconcile, then classify
month = month.renamed(account_names)  # optional
rows = records.records(month.transactions)  # flat rows, same as --dump
```

Each row has the columns in `records.FIELDS` with typed values (`date`, `Decimal`, `str`). Its
`id` is `<source_file>:<source_ref>`, so loading the same statement again yields the same ids.

## Development

`make check` runs ruff, `mypy --strict` and pytest with branch coverage. Real statements stay in
`statements/` (git-ignored); only redacted fixtures live in `tests/fixtures/`.
