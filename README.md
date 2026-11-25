# scrooge

[![CI](https://github.com/angriman/scrooge/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/angriman/scrooge/actions/workflows/ci.yml)
[![coverage](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fangriman%2Fscrooge%2Fbadges%2Fcoverage.json)](https://github.com/angriman/scrooge/actions/workflows/coverage-badge.yml)
[![version](https://img.shields.io/github/v/tag/angriman/scrooge?sort=semver&label=version)](https://github.com/angriman/scrooge/tags)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)

Parses bank and broker statements, checks them against their own balances and keeps the
transactions in a local SQLite database for two people, with a small web app for net worth,
holdings, cash flow and transactions. Everything is offline except fetching daily prices.

Sources: Migros Bank, Revolut, Revolut Savings, IBKR, Tastytrade, Swisscard, Morgan Stanley
stock plan, Charles Schwab Equity Awards, finpension 3a, and the scanned pension certificates
of AXA and Servisa.

## Use

```sh
make setup                                                          # install, then answer a few questions
./uv run --no-dev scrooge import users/<name>/statements/           # recognise, reconcile, classify, stage
./uv run --no-dev scrooge import users/<name>/statements/ --review  # categorise unknown merchants; saved as rules
./uv run --no-dev scrooge commit                                    # commit everything staged for the person
./uv run --no-dev scrooge prices                                    # fetch the closes and rates not stored yet
./uv run --no-dev scrooge serve                                     # the web app
```

`make setup` installs the app with `./uv` and runs `scrooge init`, which asks your name, how
it is printed on your statements, who else shares the database and the currency to show, then
makes your setup in `users/<name>/` (git-ignored). Put your statement exports in its
`statements/` folder.

Files are recognised by their content, never by their name. A file that does not reconcile
is refused on its own, and the others are staged. Every command has `--help`.

## Configuration

| File in `users/<name>/` | Purpose |
|---|---|
| `scrooge.yaml` | database, owners, the currency shown, account labels, web app, where the rules and instruments are |
| `rules.yaml` | description → category rules; `--review` and the import page append here |
| `rules.<owner>.yaml` | one person's rules for rows naming either person (transfers between the two); read first |
| `categories.yaml` | categories added to the built-in taxonomy (`+Name` in `--review`) |
| `instruments.yaml` | asset tab for Tastytrade tickers, T-bill maturities, price symbols |
| `statements/` | the statement exports to import |
| `scrooge.db`, `originals/`, `backups/` | the database, every statement imported as it came, a copy before each commit and the daily backups |

A command reads `--config <path>`, else `scrooge.yaml` in the folder it runs in, else the
setup `--user <name>` (or `SCROOGE_USER`) names, or the only one in `users/`. Relative paths
in it are relative to the folder the command runs in: run from another folder, a command finds
no database or rules file there and refuses to go on, saying where it looked. Only `scrooge
init` makes a database (`scrooge init --db <path>` an empty one alone, for a config written by
hand).

```yaml
database: users/lukas/scrooge.db
owner: Lukas                         # whose statements these are; or pass --owner
owners:                              # how each person appears as a counterparty
  Lukas: {match: ["LUKAS MEIER"]}
  Anna: {match: ["ANNA MEIER", "CH00 0000 0000"]}
currency: CHF                        # EUR, CHF, USD, GBP or JPY; EUR without it
currencies: [CHF, USD, EUR]          # the month-end table's columns; these without it
unmatched: Uncategorized             # the category of rows no rule matches
account_labels: {U7654321: "IBKR 2"} # a second account at a broker, by its number
frozen: [Deposito, {Anna: Risparmio}]  # frozen like the pensions; {owner: account} for one alone
benchmarks: [VT, VOO]               # what investments are compared with, as Yahoo names them
```

`unmatched` lets every statement be committed without choosing categories (net worth needs
none). To track expenses, delete it and run `scrooge import --review`: it asks once per unknown
merchant and saves the answers as rules. Answer with a category's number or part of its name;
`+Loans` creates a category, `+Home:Garden` one under `Home`.

Rows whose counterparty is the other person are marked, so the combined view leaves them out.

Files written before 2026-11-24 may name categories in Italian (`Spesa`) and call the sides of
`categories.yaml` `uscite` and `entrate`. They are still read so, but `scrooge migrate-files`
writes them once with today's names: it says what it would change, and changes it with
`--write`, keeping each file as it was beside it (`rules.yaml.before-migrate-files`). Comments,
order and quotes stay as they were.

## The database

Statements are committed as they come, of any period: there are no months to close.

```
scrooge statements          # the statements stored, staged ones first, with their ids
scrooge uncommit <id>       # take a committed statement back: it is staged again
scrooge discard <id>        # remove a staged statement and everything it staged
scrooge keep <path>...      # keep the files of statements imported before originals/ existed
scrooge doctor              # the schema, the database file, the rules, the originals, the prices
scrooge migrate --dry-run   # what this version would do to the database, tried on a copy
scrooge backup              # a backup into backups/, the older ones pruned (7 days, 8 weeks, 12 months)
scrooge query --from 2026-09 --to 2026-09 --category Groceries --summary
```

- An import is for one person. Of each account, what is already committed stays as it is and
  the statement is staged from the day after; today's rows wait for the next import. A
  statement of the other person's account is refused.
- A commit is one transaction, refused while a staged row has no category or a balance does
  not follow from the previous statement's plus what was booked in between.
- The credit card is an account of its own: every payment its invoice lists must be a card
  debit on the bank within a week.
- A pension certificate is imported like any statement: it gives the fund's account the balance
  it states for its day, and the figure read is printed to compare with the paper. A figure
  that does not read cleanly is refused, never guessed.
- `scrooge query` lists committed transactions and their totals, one per currency, never
  converted. A name the database does not have is an error, not an empty result.

### Typed figures

For what has no statements, figures are typed. Each counts from its day until the next one;
`--remove` takes one back.

```sh
scrooge balance Sparkasse 2023-01-31 1234.56 EUR         # cash held that day
scrooge position Broker 2026-11-20 ACME 120 USD           # shares held, valued at the daily closes
scrooge balance "Pillar 2 AXA" 2026-11-21 60000 CHF      # a pension fund between its certificates
```

The Schwab export prints no balance and no holdings, so its shares and cash add up from its
first row. Export it from the first vest on, keep the name Schwab gives the file (its period
runs to the day in it), then type once what Schwab shows: a `position` and a `balance` of the
account `Schwab`. A later figure that does not follow from it and the rows in between is
refused. Don't also type those shares in another account: they would count twice.

### Closing an account

An account that is closed, or a pension fund left, is closed on a day it held nothing at the
end of. It keeps its history and is listed apart, under closed accounts, and it is never
behind. After that day nothing may be booked, stated or typed for it but zero.

```sh
scrooge balance "Pillar 2 AXA" 2026-06-30 0 CHF   # a fund left without a certificate of zero
scrooge close "Pillar 2 AXA" 2026-06-30
scrooge close Sparkasse --reopen                  # it is in use again
```

## Prices and net worth

`scrooge prices` is the one command that goes online: it fetches from Yahoo the daily closes,
splits and exchange rates not stored yet, and stores them for good, the closes of the
`benchmarks` included. Run it once a day, after the markets close.

Net worth is worked out for every day at that day's closes and rates, up to the newest close
stored; after a person's last statement, what they held then is carried at the later closes.
Options and futures are not assets; a pension fund is carried at its last stated balance.

## The web app

`scrooge serve` shows the pages on <http://127.0.0.1:8765>, for one person or both together.
It answers on this machine only unless `serve` in `scrooge.yaml` names another address; then
every page asks for a password (`scrooge password` prints the line to put there):

```yaml
serve:
  host: 0.0.0.0
  names: [nas.example.ts.net]   # every name or address typed in a browser to reach it
  password: scrypt$...
```

A machine that only shows the data has `serve: {read_only: true}` and a copy of the database,
sent there by `scrooge publish <folder>`.

## With Docker

The app runs as a container on any machine with Docker (a server, a NAS, a Raspberry Pi, a
Mac), over the one database: statements are imported on its import page, prices fetched
every day by a second container. The image is public, for x86-64 and 64-bit ARM:

```sh
mkdir -p ~/scrooge/data && cd ~/scrooge
curl -fsSLO https://raw.githubusercontent.com/angriman/scrooge/main/deploy/compose.yaml
curl -fsSL -o .env https://raw.githubusercontent.com/angriman/scrooge/main/deploy/.env.example
# in .env: SCROOGE_UID and SCROOGE_GID, as `id -u` and `id -g` print them
docker compose run --rm scrooge scrooge init --container   # who you are, a password
docker compose up -d                                       # http://localhost:8765
```

Everything else, from updates to reaching it from other devices and a Synology NAS:
[deploy/README.md](deploy/README.md). GitHub builds the image on every tag `v*` once
`make check` passes; `make release VERSION=v1.2.3` tags `main` and waits for it, and
`make deploy-update` pulls it where the app runs (`Makefile.local` says where), tries its
migrations on a copy of the database (`scrooge migrate --dry-run`) and only then restarts;
after it, `scrooge doctor` there says the database was brought up to date, and "A new
version" in deploy/README.md lists what else an update may ask for.

## Everything in this folder

`./uv` is uv kept in the project: its first run downloads uv into `.local/bin` (checked against
the SHA-256 digests in the script), and its cache, Python and tools stay in `.local/`, the
environment in `.venv/`. Nothing goes to `~/.cache`, `~/.local` or `~/.config`. With your data
in `users/`, deleting the folder deletes all of it. Use `./uv run --no-dev`: plain `uv run`
installs the development tools again.

`./tesseract` does the same for Tesseract, which reads the scanned pension certificates only:
`make ocr` installs it from conda-forge into `.local/`, and `./uv` and the Makefile put it
first on the PATH. A Tesseract installed elsewhere works too; CI and the image use Debian's.

## Development

`make check` runs ruff, `mypy --strict` and pytest with branch coverage. Real statements stay in
`users/<name>/statements/` (git-ignored); only redacted fixtures live in `tests/fixtures/`.
To add a bank or broker, see [CONTRIBUTING.md](CONTRIBUTING.md#adding-a-source).

`make dev-db` builds a database from those fixtures alone (two invented people, every source,
invented prices) in `output/dev/`, made again on every run, and prints how to serve it. It
never empties a folder it did not make, nor the real database's.

## License

Apache License 2.0: see [LICENSE](LICENSE). Contributions are accepted under the same
license.
