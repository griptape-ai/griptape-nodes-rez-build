.PHONY: install
install: ## Install all dependencies.
	@uv sync --all-groups

.PHONY: format
format: ## Format project.
	@uv run ruff format

.PHONY: fix
fix: ## Fix project.
	@make format
	@uv run ruff check --fix --unsafe-fixes

.PHONY: check
check: check/format check/lint check/types check/spell ## Run all checks.

.PHONY: check/format
check/format:
	@uv run ruff format --check

.PHONY: check/lint
check/lint:
	@uv run ruff check .

.PHONY: check/types
check/types:
	@uv run pyright .

.PHONY: check/spell
check/spell:
	@uv run typos

.PHONY: test/unit
test/unit: ## Run unit tests.
	@uv run pytest -n auto tests/unit

.PHONY: help
help: ## Show this help.
	@grep -E '^[a-zA-Z_/-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "%-20s %s\n", $$1, $$2}'
