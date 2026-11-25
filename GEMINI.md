# Scrooge — Agent & Contributor Guide

Scrooge parses bank, brokerage, and pension statements into normalized transactions, validates
and reconciles them against stated balances, and stores them in a local SQLite database with a
web UI.

For user documentation and CLI examples, see [README.md](README.md). For adding a new statement
source and SQL conventions, see [CONTRIBUTING.md](CONTRIBUTING.md). For Docker deployment, see
[deploy/README.md](deploy/README.md).

## Hard rules

- **Offline and deterministic**: no LLMs, no API keys, and no network during parsing,
  validation, or classification. PDFs with a text layer use `pdfplumber`; scanned pension
  certificates (AXA, Servisa) use `extractors/ocr.py` with local `tesseract` (raises if missing
  or unreadable; never guesses).
- **Single network boundary**: `scrooge.prices` is the only module allowed to touch the network
  (daily closes, splits, FX rates via `scrooge prices` / `scrooge.marketdata`, and TreasuryDirect
  T-bill maturity lookups on `/import`). Fetched market data is stored once and never refetched.
  Tests never use the network (`tests/fakes.py`, `tests/fixtures/yahoo/`,
  `tests/fixtures/treasury/`).
- **Transactional commits**: an import stages statements of any period; `commit` commits
  everything staged for an owner in a single SQLite transaction, only after every reconciliation
  check passes. Reconciliation failures raise specific exceptions, cannot be overridden from the
  web UI, and never block sibling files in the same import.
- **Reconciliation before staging**: per-account running/closing balance checks, balance chains
  across consecutive statements, holdings continuity from trades and splits, and credit-card
  invoice payments matched against bank card debits.
- **Auth & binding**: `scrooge serve` binds to `127.0.0.1` unless `serve` in `scrooge.yaml`
  names another host, which requires a scrypt password digest (`scrooge password`) and allowed
  hostnames. All static assets are vendored (no CDN).
- **Read-only replicas**: `serve: {read_only: true}` never creates, migrates, or writes the
  database; missing DBs or mismatched schemas are refused; mutating POSTs return `403`
  (`/import` returns `404`). `scrooge publish <folder>` snapshots and atomically renames the DB.
- **No sensitive data in git**: statements, databases, backups, and `users/` stay git-ignored.
  Fixtures (`tests/fixtures/`), starter configs (`src/scrooge/starter/`), demos, screenshots,
  docs, and commit messages must use **completely invented** names, account numbers, and amounts.

## Architecture & layout

- `src/scrooge/models.py`: `Transaction`, `SecurityTrade`, `Kind`, `BalanceCheck` — the contract
  between extractors and storage.
- `src/scrooge/extractors/`: one pure module per institution (`migros`, `revolut`,
  `revolut_savings`, `ibkr`, `tastytrade`, `swisscard`, `finpension`, `morganstanley`, `schwab`,
  plus scanned certificates `axa` and `servisa` via `ocr.py`). Every module ends with a `SOURCE`
  or `CertificateSource` declaration (`extractors/source.py`), registered in `router.py` and
  enforced by `tests/extractors/test_sources.py`.
- `src/scrooge/router.py`: identifies files strictly by content, never by filename alone.
- `src/scrooge/validation.py` & `pipeline.py`: statement validation, counterparty/transfer
  detection, rule-based classification (`classification.py`, `categories.py`).
- `src/scrooge/storage/`: SQLite via standard `sqlite3` (hand-written parameterized SQL, no ORM).
  - `migrations/NNNN_*.sql` & `*.py`: numbered schema/data migrations; `db.py` (`create`,
    `reading`, `migrate`, `pending`, `snapshot`). Only `scrooge init` creates a new database.
  - `money.py` (integer billionths `_nano`), `ids.py` (file-independent SHA-256 transaction IDs),
    `store.py`, `bills.py` (FIFO T-bill interest split), `holdings.py`, `originals.py`
    (content-addressed raw statements), `logos.py`.
- `src/scrooge/staging.py`, `importing.py`, `archives.py`, `suggestions.py`, `closing.py`:
  per-owner staging in chronological order, safe `.zip` unpacking with size/entry caps,
  commit/uncommit/discard, pre-operation DB copies (pruned to newest 10), account closing on
  zero-balance days, and merchant rule suggestions.
- `src/scrooge/dbcli.py`: CLI commands (`init`, `import`, `commit`, `statements`, `uncommit`,
  `discard`, `balance`, `position`, `close`, `keep`, `logo`, `prices`, `query`, `serve`,
  `password`, `publish`, `migrate-files`, `doctor`, `migrate`, `backup`).
- Domain & analytics services (read-only over committed data):
  - Transactions & cash flow: `query.py`, `months.py`, `cashflow.py`, `analysis.py`,
    `income.py`, `spending.py`, `savings.py`, `report.py`, `paidby.py`, `recurring.py`,
    `merchants.py`, `recap.py`, `attention.py`, `together.py`.
  - Net worth & investments: `networth.py`, `portfolio.py`, `overview.py`, `holds.py`,
    `performance.py`, `holding_returns.py`, `benchmark.py`, `costs.py`, `payouts.py`,
    `pensions.py`, `marketdata.py`, `prices.py`.
- Web & frontend:
  - `src/scrooge/web/`: Starlette app (`app.py` server-rendered Jinja pages + `api.py` /
    `settings_api.py` JSON endpoints under `/api` serialized via `wire.py`), `auth.py`,
    `forms.py`, `uploads.py`, `shapes.py`. Handlers stay thin and delegate to domain services.
  - `frontend/`: SvelteKit static SPA (`adapter-static`, TypeScript, EN/IT localization), built
    by `make web` into `src/scrooge/web/spa/` and served at `/app`.
- `src/scrooge/starter/`: invented starter YAML files copied by `scrooge init` and used in tests.
- `tests/`: pytest + Hypothesis invariants, golden extractor fixtures in `tests/fixtures/<name>/`,
  synthetic DB builders (`make_dev_db.py`, `make_demo_db.py`).

## Commands

Uses `./uv` when present (self-contained in `.local/`), otherwise `uv`:

- `make sync` (or `uv sync`) — install Python dependencies (`uv add [--dev] <pkg>` to add)
- `make check` — full Python gate: ruff lint + format check, `mypy --strict`, pytest + coverage
- `make fix` — auto-fix Python lint and formatting
- `make web-check` — Prettier check, `svelte-check`, and Vitest unit tests in `frontend/`
- `make web` / `make web-dev` — build SPA into `src/scrooge/web/spa/` / run Vite dev server
- `make ocr` — install local Tesseract into `.local/ocr` for OCR tests
- `make dev-db` / `make demo` — build synthetic dev (`output/dev`) or demo (`output/demo`) DBs
- `make hooks` — install pre-commit hooks (ruff, mypy, detect-secrets, statement file blocker)

## Code conventions

- **Money**: `decimal.Decimal` parsed from strings, never `float`. In SQLite: integer billionths
  (`_nano` columns) for amounts, text decimals for quantities and prices, never `REAL`.
- **Dates**: `datetime.date`; no naive `datetime` (enforced by ruff `DTZ`). Every transaction has
  `date` (real occurrence date, for queries/views) and `booking_date` (inside the statement
  period, for balance reconciliation).
- **Accounts & IDs**: an account is `(owner, name)` — extractors never know the owner. Transaction
  IDs hash statement fields only, never the filename, so re-exports preserve manual overrides.
- **Pure extractors**: no I/O, no network, no global state, no `print` (ruff `T20`).
- **Migrations & derived rows**: schema changes require a new numbered migration in
  `storage/migrations/` (never edit an existing migration). Derived rows (such as FIFO T-bill
  interest splits) are recomputed from stored statements on import, never patched manually.
- **Typing & style**: full type annotations (`mypy --strict`), max McCabe complexity 12, line
  length 100, parameterized SQL only (`?` placeholders; see [CONTRIBUTING.md](CONTRIBUTING.md)).
- **Frontend**: 2-space Prettier formatting (`cd frontend && npm run format`), localized UI copy
  (EN/IT).

## Definition of done

Every change goes through a focused pull request against `main`:

1. `make check` passes (90% global branch coverage floor; **100%** on `validation.py`,
   `staging.py`, `marketdata.py`, and `storage/`).
2. `make web-check` passes whenever `frontend/` is touched.
3. Never lower coverage floors, weaken ruff/mypy rules in `pyproject.toml`, skip/xfail/delete
   tests, swallow reconciliation errors, or add `# noqa` / `# type: ignore[...]` without an
   inline comment explaining why.
