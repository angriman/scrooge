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
