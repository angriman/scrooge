# finanze-parser

Parses bank and broker statements, reconciles every account against the statement's own
balances, and stores the transactions in a local SQLite database for two people, shown by a
small web app: net worth day by day, holdings, cash flow, transactions, and an import page.
Statements are read offline; the only thing fetched is daily prices (see "Prices and net
worth").

Sources: Migros Bank (CSV), Revolut accounts and Revolut Savings (TSV/CSV), IBKR activity
statement (CSV), Tastytrade transactions (CSV), Swisscard invoice (PDF), Morgan Stanley stock
plan quarterly statement (PDF), finpension pillar 3a transactions (CSV), and the scanned second-pillar certificates of AXA and Servisa (PDF, read
with the local `tesseract`).

## Use

```sh
make setup                                   # first run: install, then answer a few questions
```

`make setup` installs the app with `./uv` (see "Everything in this folder") and runs
`finanze init`. That asks your name, how it is printed on your statements, whether someone
else shares the database, and the currency to show figures in, then makes your setup in `users/<name>/` (git-ignored): the config,
the rules, the categories, the instruments and an empty `statements/` folder. Put your
statement exports there, then, from this folder:

```sh
./uv run --no-dev finanze import users/<name>/statements/            # recognise, reconcile, classify and stage
./uv run --no-dev finanze import users/<name>/statements/ --review   # answer once per unknown merchant; saved as rules
./uv run --no-dev finanze commit             # commit everything staged for the person
./uv run --no-dev finanze prices             # fetch the closes and rates not stored yet
./uv run --no-dev finanze serve              # the web app
```

Files are recognised by their content, never by their name. A file that does not reconcile
is refused on its own, and the others are staged. See "The database" below.

## Configuration

Everything personal is in one folder, a setup: `users/<name>/`, which `finanze init` makes from
the starter files in `src/finanze/starter/`.

| File in `users/<name>/` | Purpose |
|---|---|
| `finanze.yaml` | database, owners, the currency the pages show, account labels, web app, where the rules and instruments are |
| `rules.yaml` | description → category rules, generic words and your own (shops, chains, employer); `--review` and the import page append here |
| `rules.<owner>.yaml` | one person's rules for rows naming either person (transfers between the two, own top-ups); read first; `--review` saves those answers here |
| `categories.yaml` | categories added to the built-in taxonomy; `--review` appends here on `+Name` |
| `instruments.yaml` | asset tab for Tastytrade tickers, maturity of T-bills bought there, price symbols |
| `statements/` | where to put the statement exports to import |
| `finanze.db`, `originals/`, `backups/` | the database, a copy of every statement imported, the copies made before each commit |

Which config a command reads: `--config <path>`; else `finanze.yaml` in the folder the
command runs in, as before `users/` existed; else the setup `--user <name>` (or
`FINANZE_USER`) names, or the only one in `users/` (several are an error that names them).
`--user` also picks a setup when there is a `finanze.yaml` here. A name is the folder's, in
any case, or the name given to `finanze init`.

Relative paths in `finanze.yaml` are relative to the folder the command runs in, as they
always were: the config `finanze init` writes names `users/<name>/finanze.db`,
`users/<name>/rules.yaml` and `users/<name>/instruments.yaml`, so the commands run from this
folder. It can name other files: `rules: [path, ...]` (first wins; default `rules.yaml`) and
`instruments: path` (default `instruments.yaml`), for `import`, `serve` and `prices`; `--rules`
and `--instruments` win over both. The first rules file is the one both people share:
`rules.<owner>.yaml` and `categories.yaml` are read from its folder and written there.

`unmatched: Non classificato` gives that category to every expense and income no rule
matches, so that every statement can be committed without choosing categories: net worth
needs none. `finanze init` writes it. Rows an extractor leaves without a category on purpose,
such as a deposit at a broker, still go to review. To track expenses, delete the line and run
`finanze import --review`, which asks once per unknown merchant and saves the answers as rules.

`currency: CHF` (EUR, CHF, USD, GBP or JPY; EUR without it) is what the pages show figures in
until another is chosen in their header.

Keys of older configs that nothing reads any more (`locale`, `accounts`) are ignored.

## The database

Statements are stored in a local SQLite database, one row per transaction. They are
committed as they come, of any period: there are no months to close.

```
finanze import <path>...    # reconcile, classify, then stage the statements
finanze commit              # commit what is staged; a copy of the database is saved first
finanze statements          # the statements stored, staged ones first, with their ids
finanze uncommit <id>       # take a committed statement back: it is staged again
finanze discard <id>        # remove a staged statement and everything it staged
finanze query --from 2026   # find committed transactions (see "Querying it" below)
```

`finanze import --review` asks for the category of unmatched rows.
Answer with a category's number, its name or a part of it (`rist` is enough for `Ristoranti e bar`;
when several match you are asked which one); `?` lists the categories again.
Answer `+Prestiti` to create a category that does not exist yet and use it (`+Casa:Giardino`
puts an expense under the top-level category `Casa`; money received goes to the Entrate, which
have no groups). It is saved in `categories.yaml`.

An import is for one person. Of each account a statement covers, what is already committed is
left as it is, and the statement is staged from the day after; what it books on the day of the
import waits for the next one, since that day is not over. A statement of an account that
belongs to the other person is refused. A commit is refused while any staged row lacks a
category, or when a statement's balance does not follow from the previous statement's balance
plus what was booked in between; it is one transaction, all or nothing.

Every file staged is kept as it came in `originals/`, next to the database, under its SHA-256;
`finanze keep <path>...` adds the files of statements imported before that.

A second-pillar pension certificate (the scan) is imported like any statement: it gives the
pension fund's account the balance it states for its day, and the figure read is printed, to
compare with the paper. A figure that does not read cleanly is refused, never guessed.

Each transaction has two dates: `date`, the day it happened, and `booking_date`, the day it
counts on for its statement (a card purchase made in August and billed in September has
both). Amounts are whole billionths of the currency unit (`amount_nano`).

`finanze.yaml` has:

```yaml
database: users/lukas/finanze.db   # relative to the folder the commands run in, or absolute
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
It reads committed statements only, and both people's unless `--owner` names one. The database
is the one in `finanze.yaml`, or `--db`.

```sh
finanze query --from 2026-09 --to 2026-09             # one month
finanze query --from 2025 --category Spesa            # a category since January 2025
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

## Prices and net worth

`finanze prices` is the one command that goes online: it fetches from Yahoo the daily closes,
splits and exchange rates not stored yet, and stores them for good (nothing is fetched twice).
Run it once a day, after the markets close.

Net worth is worked out for every day from the committed statements, at the close and the
exchange rate of each day, in EUR, CHF, USD, GBP or JPY. It runs to the newest close stored:
after the last day a person's statements cover, what they held then is valued at the later
closes, and the pages say up to which day the statements go. Options and futures are not
counted as assets; a pension fund is carried at what its last certificate states.

## The web app

`finanze serve` shows the pages on <http://127.0.0.1:8765>: the overview (net worth, its chart
and what it is made of), holdings, cash flow, transactions (with CSV), months, statements and
import (upload, review the categories, commit). The view is one person's or both together.

It answers on this machine only, unless `serve` in `finanze.yaml` names another address; then
it asks for a password on every page (`finanze password` prints the line to put there):

```yaml
serve:
  host: 0.0.0.0
  names: [nas.example.ts.net]   # every name or address typed in a browser to reach it
  password: scrypt$...
```

A machine that only shows the data has `serve: {read_only: true}` and a copy of the database,
sent there by `finanze publish <folder>`.

## On the NAS

The app runs as a container on a Synology NAS, over the one database; statements are imported
on its import page and prices are fetched there by a daily task. Setting it up the first time,
the commands that run inside the container and the prices task: `deploy/README.md`.

GitHub builds the image: every tag `v*` runs `make check` and, when it passes, pushes the
image to the repository's private registry (`ghcr.io/<owner>/finanze`, `.github/workflows/image.yml`).
The NAS pulls it; no code is kept there.

### Updating it

Once the new version is merged into `main` and pushed:

```sh
make release VERSION=v0.2     # tag main, push the tag, wait for GitHub to build the image
make nas-update               # on the NAS: pull the image, restart, drop the old one
```

- `make release` refuses uncommitted changes, and a `main` that is not what GitHub has. It
  waits for the build and fails when `make check` or the build does; the NAS then keeps the
  version it runs. It needs `gh`, signed in.
- `make nas-update` reaches the NAS by `ssh nas`: a `Host nas` in `~/.ssh/config` (name, port,
  user, key), or `make nas-update NAS=<host>`. `sudo` there asks for the DSM password.
- A new schema is migrated as the app starts, after a copy of the database goes to
  `data/backups/`. Everyone signs in again.
- Going back: in `compose.yaml` on the NAS, `image: ghcr.io/<owner>/finanze:<older tag>` in
  place of `:latest`, then `make nas-update`.

## Everything in this folder

`./uv` is uv kept in the project, for a machine that should hold nothing of it elsewhere. Its
first run downloads uv into `.local/bin` (the version of the Dockerfile, checked against the
SHA-256 digests in the script); from then on uv's cache, its Python and its tools are in
`.local/` too, and the environment in `.venv/`. Nothing goes to `~/.cache`, `~/.local` or
`~/.config`, and no uv installed elsewhere is needed. Your data is in the folder too, in
`users/` (see "Configuration"). Deleting the folder deletes all of it.

```sh
./uv sync --no-dev                  # the app only; plain `./uv sync` adds the development tools
./uv run --no-dev finanze serve     # `uv run` alone would install them again
./uv cache clean                    # the downloads, once installed
```

`make` uses `./uv` once `.local/bin/uv` is there, and `make hooks` then installs a git hook that
runs through it, so pre-commit's environments are in `.local/` as well. Tesseract is the one thing outside it: it
comes from Homebrew (`brew install tesseract`), for the scanned pension certificates only.

## Development

`make check` runs ruff, `mypy --strict` and pytest with branch coverage. Real statements stay in
`users/<name>/statements/` (git-ignored); only redacted fixtures live in `tests/fixtures/`.

`make dev-db` builds a database to develop against from those fixtures alone: two invented
people, every source, the pension certificates when Tesseract is installed, and invented
prices, so that every page has something to show. It is made again from nothing on every run,
in `output/dev/` with the config and rules it was built with, and it prints how to serve it:

```
uv run finanze serve --config output/dev/finanze.yaml --rules output/dev/rules.yaml \
    --instruments output/dev/instruments.yaml
```

It is not in `users/`, so that it never becomes a second setup that your own commands would
have to tell apart with `--user`.

It never empties a folder it did not make, nor the real database's.

`deploy/README.md` says how the web app runs as a container on the NAS. The image is built by
GitHub, not by `make check`.
