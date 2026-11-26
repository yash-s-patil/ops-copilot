.DEFAULT_GOAL := help
.PHONY: help install env dev token smoke dev-down dev-reset migrate seed lint format test test-integration test-web secrets

WEB := pnpm --dir apps/web
TF_DIR := infra/terraform
TF_ROOTS := $(TF_DIR)/state-bootstrap
CHECKOV_VERSION := 3.4.10
MCP_URL := http://127.0.0.1:8000

GITLEAKS_IMAGE := ghcr.io/gitleaks/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f

help: ## List the available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-10s %s\n", $$1, $$2}'

install: ## Install every workspace package and tool at the locked versions
	uv sync --locked --all-packages
	uv run pre-commit install
	$(WEB) install --frozen-lockfile

.env:
	@rm -f .env.new
	@umask 077 && uv run python infra/local/make_env.py > .env.new && mv .env.new .env
	@echo "Created .env with a generated local database password, storage key and signing key."

env: .env ## Create .env for local development if it does not exist

dev: .env ## Build and start PostgreSQL, Azurite and the MCP server, and wait until all are healthy
	@uv run --no-project python infra/local/check_env.py
	docker compose up --build --detach --wait

token: .env ## Print a local sign-in token for a demo user: make token WHO=priya
	@uv run --no-project python infra/local/check_env.py
	@uv run --env-file .env python infra/local/dev_token.py $(WHO)

smoke: .env ## Check that the local MCP server is up, reaches its database and knows who is calling
	@uv run --no-project python infra/local/check_env.py
	curl --fail --silent --show-error --max-time 5 $(MCP_URL)/healthz
	curl --fail --silent --show-error --max-time 5 $(MCP_URL)/readyz
	uv run --env-file .env python infra/local/smoke.py $(MCP_URL)

dev-down: ## Stop the local backends (data volumes are kept)
	docker compose down

dev-reset: ## Delete the local data volumes and .env, then start again from empty
	docker compose down --volumes
	rm -f .env
	$(MAKE) dev

migrate: .env ## Apply database migrations to the local meridian database
	uv run --env-file $(CURDIR)/.env --directory services/mcp alembic upgrade head

seed: migrate ## Replace the local meridian data with the demo data and upload the policies
	uv run --env-file .env --env-file infra/local/seed.env python -m opscp_seed

lint: ## Formatting, lint rules, repository hygiene and strict type checking
	uv run pre-commit run --all-files
	uv run pyright
	$(WEB) run lint
	$(WEB) run typecheck
	$(WEB) run format:check
	terraform fmt -check -recursive $(TF_DIR)
	@for root in $(TF_ROOTS); do \
		terraform -chdir=$$root init -backend=false -input=false -lockfile=readonly > /dev/null && \
		terraform -chdir=$$root validate || exit 1; \
	done
	tflint --chdir=$(TF_DIR) --init > /dev/null
	tflint --chdir=$(TF_DIR) --recursive --config=$(CURDIR)/$(TF_DIR)/.tflint.hcl
	uvx --from checkov==$(CHECKOV_VERSION) checkov --directory $(TF_DIR) --framework terraform --quiet --compact
	uvx --from checkov==$(CHECKOV_VERSION) checkov --file services/mcp/Dockerfile --framework dockerfile --quiet --compact

format: ## Apply formatting and safe lint fixes
	uv run ruff format .
	uv run ruff check --fix .
	$(WEB) run format
	terraform fmt -recursive $(TF_DIR)

test: ## Unit and contract tests with coverage (no backends needed)
	uv run pytest --cov -m "not integration"

test-integration: ## Tests against the local PostgreSQL and Azurite started by `make dev`
	uv run pytest -m integration

test-web: ## Web unit tests
	$(WEB) run test

secrets: ## Scan every commit for secrets
	docker run --rm --volume "$(CURDIR)":/repo:ro \
		--env GIT_CONFIG_COUNT=1 --env GIT_CONFIG_KEY_0=safe.directory --env GIT_CONFIG_VALUE_0=/repo \
		$(GITLEAKS_IMAGE) git /repo --redact --no-banner
