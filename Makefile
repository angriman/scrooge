# Gate for this project: `make check` must pass before a task is done.
# See CLAUDE.md (Definition of done) and docs/parser-spec.md.

UV ?= uv

.DEFAULT_GOAL := help
.PHONY: all help check fix lint type test strict-cover hooks hooks-run sync clean

help: ## Show available targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'

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
	$(UV) run pytest --cov=finanze --cov-report=term-missing

strict-cover: ## 100% coverage floor on validation.py and writers/
	@inc=$$( { ls src/finanze/validation.py; find src/finanze/validation src/finanze/writers -name '*.py'; } 2>/dev/null | paste -sd, -); \
	if [ -z "$$inc" ]; then \
		echo "strict-cover: no validation.py or writers/ modules yet, skipping"; \
	else \
		$(UV) run coverage report --include="$$inc" --fail-under=100 --no-skip-covered; \
	fi

fix: ## Auto-fix lint and formatting
	$(UV) run ruff check --fix src tests
	$(UV) run ruff format src tests

hooks: ## Install the git pre-commit hooks
	$(UV) run pre-commit install

hooks-run: ## Run every pre-commit hook over all files
	$(UV) run pre-commit run --all-files

clean: ## Remove caches and coverage artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +
