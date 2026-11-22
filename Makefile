# Gate for this project: `make check` must pass before a task is done.
# See CLAUDE.md (Definition of done) and docs/parser-spec.md.

# The uv of this folder (./uv, README "Everything in this folder") once it is there, else uv.
UV ?= $(if $(wildcard .local/bin/uv),./uv,uv)
# The Tesseract of ./tesseract (make ocr), once installed, whichever uv runs: the OCR tests and
# make dev-db read scans with it. .local/ocr holds that one program.
export PATH := $(CURDIR)/.local/ocr:$(PATH)
# A Host of ~/.ssh/config: the NAS's name, port and user (deploy/README.md).
NAS ?= nas
NAS_DIR ?= /volume1/docker/scrooge
# By its path: a command run by ssh has no login shell, and DSM's docker is not on its PATH.
NAS_DOCKER ?= /usr/local/bin/docker
DEV_DB ?= output/dev

.DEFAULT_GOAL := help
.PHONY: all help setup ocr check fix lint type test strict-cover hooks hooks-run sync clean release nas-update dev-db icons web web-dev web-check

help: ## Show available targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## First run: install with ./uv, then make your setup in users/<name>/ (scrooge init)
	./uv sync --no-dev
	./uv run --no-dev scrooge init

ocr: ## Install Tesseract into .local/ for the scanned pension certificates (./tesseract)
	./tesseract --version

sync: ## Install/refresh the project environment
	$(UV) sync

all: check ## Default full run (alias for check)

check: lint type test strict-cover ## Full gate: lint, format check, mypy --strict, tests + coverage

lint: ## ruff lint + format check
	$(UV) run ruff check src tests
	$(UV) run ruff format --check src tests

type: ## mypy --strict over src and tests
	$(UV) run mypy

test: ## pytest with branch coverage (90% global floor)
	$(UV) run pytest --cov=scrooge --cov-report=term-missing

strict-cover: ## 100% coverage floor on validation.py and everything that writes to the database
	@inc=$$( { ls src/scrooge/validation.py src/scrooge/staging.py src/scrooge/marketdata.py; find src/scrooge/validation src/scrooge/storage -name '*.py'; } 2>/dev/null | paste -sd, -); \
	if [ -z "$$inc" ]; then \
		echo "strict-cover: no validation.py or storage/ modules yet, skipping"; \
	else \
		$(UV) run coverage report --include="$$inc" --fail-under=100 --no-skip-covered; \
	fi

fix: ## Auto-fix lint and formatting
	$(UV) run ruff check --fix src tests
	$(UV) run ruff format src tests

hooks: ## Install the git pre-commit hooks (through ./uv once it is there: hooks/install)
	sh hooks/install $(UV)

hooks-run: ## Run every pre-commit hook over all files
	$(UV) run pre-commit run --all-files

# The frontend of its own (frontend/, SvelteKit), served by `scrooge serve` at /app once built.
web: ## Build the frontend into src/scrooge/web/spa, where `scrooge serve` finds it (needs npm)
	cd frontend && npm ci && npm run build

web-dev: ## The frontend with reloading at http://localhost:5173/app, over `scrooge serve` on 8765
	cd frontend && npm run dev

web-check: ## Type-check the frontend
	cd frontend && npm ci && npm run check

ICONS := src/scrooge/web/static/icons

# A phone rounds a home screen icon itself and turns clear corners black: its tile is square.
icons: ## Draw the PNG icons from icons/icon.svg (needs rsvg-convert: brew install librsvg)
	rsvg-convert -w 32 -h 32 $(ICONS)/icon.svg -o $(ICONS)/icon-32.png
	sed 's/ rx="14"//' $(ICONS)/icon.svg | rsvg-convert -w 180 -h 180 -o $(ICONS)/apple-touch-icon.png

# GitHub builds the image of a tag v* once `make check` passes (.github/workflows/image.yml).
release: ## Tag what is pushed to main as VERSION (make release VERSION=v1.2.3), wait for its image
	@case "$(VERSION)" in v*) ;; *) echo "release: VERSION=v1.2.3 is missing"; exit 1;; esac
	@test -z "$$(git status --porcelain)" || { echo "release: commit or stash first"; exit 1; }
	git fetch --quiet origin main
	@test "$$(git rev-parse HEAD)" = "$$(git rev-parse origin/main)" \
		|| { echo "release: this is not what main on GitHub is: push main first"; exit 1; }
	git tag -a $(VERSION) -m $(VERSION) && git push origin $(VERSION)
	@for n in $$(seq 30); do \
		run=$$(gh run list --workflow image.yml --branch $(VERSION) -L 1 --json databaseId -q '.[0].databaseId'); \
		[ -n "$$run" ] && exec gh run watch --exit-status "$$run"; sleep 2; \
	done; echo "release: no image run for $(VERSION) yet: see GitHub > Actions"; exit 1

nas-update: ## Run the newest image on the NAS: pull it and restart (sudo asks the DSM password)
	ssh -t $(NAS) 'cd $(NAS_DIR) && sudo $(NAS_DOCKER) compose pull && sudo $(NAS_DOCKER) compose up -d && sudo $(NAS_DOCKER) image prune -f'

dev-db: ## (Re)build output/dev/scrooge.db from the fixtures, for two invented people
	$(UV) run python -m tests.make_dev_db $(DEV_DB)

clean: ## Remove caches and coverage artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +
