.DEFAULT_GOAL := help
.PHONY: help install env dev dev-down lint format test secrets

GITLEAKS_IMAGE := ghcr.io/gitleaks/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f

help: ## List the available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-10s %s\n", $$1, $$2}'

install: ## Install every workspace package and tool at the locked versions
	uv sync --locked --all-packages
	uv run pre-commit install

.env:
	@printf 'POSTGRES_PASSWORD=%s\n' "$$(openssl rand -hex 24)" > .env
	@chmod 600 .env
	@echo "Created .env with a generated local database password."

env: .env ## Create .env for local development if it does not exist

dev: .env ## Start local PostgreSQL and Azurite and wait until both are healthy
	docker compose up --detach --wait

dev-down: ## Stop the local backends (data volumes are kept)
	docker compose down

lint: ## Formatting, lint rules, repository hygiene and strict type checking
	uv run pre-commit run --all-files
	uv run pyright

format: ## Apply formatting and safe lint fixes
	uv run ruff format .
	uv run ruff check --fix .

test: ## Unit and contract tests with coverage (no cloud needed)
	uv run pytest --cov

secrets: ## Scan every commit for secrets
	docker run --rm --volume "$(CURDIR)":/repo:ro \
		--env GIT_CONFIG_COUNT=1 --env GIT_CONFIG_KEY_0=safe.directory --env GIT_CONFIG_VALUE_0=/repo \
		$(GITLEAKS_IMAGE) git /repo --redact --no-banner
