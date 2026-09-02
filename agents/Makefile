TARGET  := $(CURDIR)/target
VENV    := $(CURDIR)/.venv
PYTHON  := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
UV      := $(VENV)/bin/uv
UV_CACHE_DIR := $(TARGET)/.uv-cache
UV_RUN  := UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV)

PY_PACKAGE := fred_samples_agents

BASELINE_DIR ?= $(CURDIR)/.baseline
BANDIT_BASELINE_FILE ?= $(BASELINE_DIR)/bandit-baseline.json
DETECT_SECRET_BASELINE_FILE ?= $(BASELINE_DIR)/detect-secret-baseline.json
DETECT_SECRET_EXCLUDE_FILES ?= detect-secret-baseline\.json
BASEDPYRIGHT_BASELINE_FILE ?= $(BASELINE_DIR)/basedpyright-baseline.json
BANDIT_IGNORED_RULES ?= B101,B108

DOCKER_IMAGE_NAME     ?= fred-samples-agents
DOCKER_IMAGE_TAG      ?= latest
DOCKER_IMAGE          ?= $(DOCKER_IMAGE_NAME):$(DOCKER_IMAGE_TAG)
DOCKER_CONTAINER_NAME ?= fred-samples-agents
DOCKERFILE_PATH       ?= ../dockerfiles/Dockerfile
DOCKER_CONTEXT        ?= ..
DOCKER_NETWORK        ?= fred-shared-network
DOCKER_USER_NAME      ?= fred-user
DOCKER_USER_ID        ?= $(shell id -u)
DOCKER_GROUP_ID       ?= $(shell id -g)
HOST_PORT             ?= 8010
CONTAINER_PORT        ?= 8010
CONFIG_DIR            ?= $(CURDIR)/config
CONTAINER_CONFIG_DIR  ?= /app/config
CONFIG_FILE           ?= config/configuration.yaml

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ── Bootstrap ──────────────────────────────────────────────────────────────────

$(TARGET)/.venv-created:
	@mkdir -p $(TARGET)
	python3 -m venv $(VENV)
	@touch $@

$(TARGET)/.uv-installed: $(TARGET)/.venv-created
	$(PIP) install --upgrade pip setuptools wheel
	$(PIP) install uv
	@touch $@

# pyproject.toml's [tool.uv.sources] points fred-sdk/fred-runtime at
# ../../fred/libs (local monorepo checkout, sibling to this repo) by
# default — same convention as fred/libs/fred-sdk's own [tool.uv.sources].
# Use `make dev-pypi` to resolve strictly from the published packages
# instead (what an external consumer of this repo would get).
$(TARGET)/.compiled: pyproject.toml $(TARGET)/.uv-installed
	VIRTUAL_ENV= $(UV_RUN) sync --extra dev
	@touch $@

.PHONY: dev
dev: $(TARGET)/.compiled ## Install dependencies (local fred-sdk/fred-runtime by default, see [tool.uv.sources])
	@echo "Dependencies installed."

.PHONY: dev-pypi
dev-pypi: $(TARGET)/.uv-installed ## Install strictly from PyPI, ignoring [tool.uv.sources] local overrides
	VIRTUAL_ENV= $(UV_RUN) sync --extra dev --no-sources
	@touch $(TARGET)/.compiled
	@echo "Dependencies installed from PyPI only (--no-sources)."

.PHONY: update
update: $(TARGET)/.uv-installed ## Re-resolve and update all dependencies
	VIRTUAL_ENV= $(UV_RUN) sync --extra dev
	@touch $(TARGET)/.compiled

# ── Main targets ───────────────────────────────────────────────────────────────

.PHONY: run
run: dev ## Start the sample agents pod locally
	ENV_FILE=$(CURDIR)/config/.env VIRTUAL_ENV= $(UV_RUN) run python -m fred_samples_agents

.PHONY: cli
cli: dev ## Open the interactive chat client for a running pod
	VIRTUAL_ENV= $(UV_RUN) run fred-agents-cli

# ── Code quality ───────────────────────────────────────────────────────────────
# Same tool set as ../../fred (scripts/makefiles/python-code-quality.mk):
# ruff (lint + format), bandit (SAST), detect-secrets (leak scan), basedpyright
# (type-check) — each gated on its own baseline in .baseline/, exactly like
# every backend/lib in the main fred repo. See .pre-commit-config.yaml for the
# same checks wired as git hooks.

.PHONY: lint
lint: dev ## Run the linter (ruff)
	VIRTUAL_ENV= $(UV_RUN) run ruff check

.PHONY: lint-fix
lint-fix: dev ## Run the linter (ruff) and fix what's auto-fixable
	VIRTUAL_ENV= $(UV_RUN) run ruff check --fix

.PHONY: import-order
import-order: dev ## Check import order (ruff)
	VIRTUAL_ENV= $(UV_RUN) run ruff check --select I

.PHONY: import-order-fix
import-order-fix: dev ## Fix import order (ruff)
	VIRTUAL_ENV= $(UV_RUN) run ruff check --select I --fix

.PHONY: format
format: dev ## Check formatting (ruff)
	VIRTUAL_ENV= $(UV_RUN) run ruff format --check

.PHONY: format-fix
format-fix: dev ## Fix formatting (ruff)
	VIRTUAL_ENV= $(UV_RUN) run ruff format

.PHONY: sast
sast: dev ## Run bandit (SAST), gated on .baseline/bandit-baseline.json
	VIRTUAL_ENV= $(UV_RUN) run bandit \
		-r $(PY_PACKAGE) \
		-s $(BANDIT_IGNORED_RULES) \
		--baseline $(BANDIT_BASELINE_FILE)

.PHONY: detect-secret
detect-secret: dev ## Run detect-secrets, gated on .baseline/detect-secret-baseline.json
	git ls-files -z . | xargs -0 env UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run detect-secrets-hook \
		--baseline $(DETECT_SECRET_BASELINE_FILE) \
		--exclude-files '$(DETECT_SECRET_EXCLUDE_FILES)'

.PHONY: type-check
type-check: dev ## Run basedpyright, gated on .baseline/basedpyright-baseline.json
	VIRTUAL_ENV= $(UV_RUN) run basedpyright --baseline-file $(BASEDPYRIGHT_BASELINE_FILE)

.PHONY: code-quality
code-quality: dev lint format sast detect-secret type-check ## Run all code quality checks

.PHONY: code-quality-fix
code-quality-fix: dev lint-fix import-order-fix format-fix ## Auto-fix formatting/imports/linting

$(BASELINE_DIR):
	mkdir -p $(BASELINE_DIR)

.PHONY: baseline-sast
baseline-sast: $(BASELINE_DIR) dev ## (Re)generate the bandit baseline
	VIRTUAL_ENV= $(UV_RUN) run bandit \
		-r $(PY_PACKAGE) \
		-s $(BANDIT_IGNORED_RULES) \
		-f json \
		-o $(BANDIT_BASELINE_FILE)

.PHONY: baseline-detect-secret
baseline-detect-secret: $(BASELINE_DIR) dev ## (Re)generate the detect-secrets baseline
	$(UV_RUN) run detect-secrets scan . > $(DETECT_SECRET_BASELINE_FILE)

.PHONY: baseline-type-check
baseline-type-check: $(BASELINE_DIR) dev ## (Re)generate the basedpyright baseline
	VIRTUAL_ENV= $(UV_RUN) run basedpyright --baseline-file $(BASEDPYRIGHT_BASELINE_FILE) --writebaseline

.PHONY: baseline
baseline: format-fix ## Format code and (re)generate all quality baselines
	-$(MAKE) baseline-sast
	-$(MAKE) baseline-detect-secret
	-$(MAKE) baseline-type-check

# ── Tests ──────────────────────────────────────────────────────────────────────

PYTEST_OPTS ?=

.PHONY: test
test: dev ## Run the offline test suite with coverage (no external services)
	@status=0; \
		VIRTUAL_ENV= $(UV_RUN) run pytest $(PYTEST_OPTS) -m "not integration" \
			--disable-socket --allow-unix-socket \
			--cov=. --cov-config=.coveragerc --cov-report=html || status=$$?; \
		if [ "$$status" -eq 5 ]; then \
			echo "No tests collected; treating offline validation as successful."; \
		elif [ "$$status" -ne 0 ]; then \
			exit "$$status"; \
		fi
	@echo "Coverage report: htmlcov/index.html"

.PHONY: test-one
test-one: dev ## Run a specific test: make test-one TEST=path::to::test
	@if [ -z "$(TEST)" ]; then \
		echo "Provide a test path: make test-one TEST=path::to::test"; \
		exit 1; \
	fi
	VIRTUAL_ENV= $(UV_RUN) run pytest $(PYTEST_OPTS) -v $(TEST)

.PHONY: clean
clean: ## Remove the virtual environment and cached files
	rm -rf $(VENV) $(TARGET)
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

# ── Docker ─────────────────────────────────────────────────────────────────────
# Joins fred-shared-network (created by fred-deployment-factory's `make
# docker-up`) so this pod's container can reach infra by container hostname
# (app-keycloak, app-postgres, app-opensearch, openfga — see
# configuration_prod.yaml). knowledge-flow-backend / control-plane-backend
# run on the host (`make run`, not docker-compose), reached via
# host.docker.internal.

.PHONY: docker-build
docker-build: ## Build the Docker image for the agents pod
	docker build \
		-f $(DOCKERFILE_PATH) \
		--build-arg USER_NAME=$(DOCKER_USER_NAME) \
		--build-arg USER_ID=$(DOCKER_USER_ID) \
		--build-arg GROUP_ID=$(DOCKER_GROUP_ID) \
		-t $(DOCKER_IMAGE) \
		$(DOCKER_CONTEXT)

.PHONY: docker-push
docker-push: ## Push the Docker image
	docker push $(DOCKER_IMAGE)

.PHONY: docker-run
docker-run: ## Run the agents pod container locally, joined to fred-shared-network
	docker run --rm \
		--name $(DOCKER_CONTAINER_NAME) \
		--network $(DOCKER_NETWORK) \
		--add-host=host.docker.internal:host-gateway \
		-p $(HOST_PORT):$(CONTAINER_PORT) \
		-v $(realpath $(CONFIG_DIR)):$(CONTAINER_CONFIG_DIR):ro \
		-e CONFIG_FILE=$(CONTAINER_CONFIG_DIR)/$(notdir $(CONFIG_FILE)) \
		-e ENV_FILE=$(CONTAINER_CONFIG_DIR)/.env \
		$(DOCKER_IMAGE)

.PHONY: run-docker
run-docker: docker-run ## Backward-compatible alias for docker-run

.PHONY: docker-stop
docker-stop: ## Stop the running agents container by name
	-@docker stop $(DOCKER_CONTAINER_NAME)

.PHONY: docker-clean
docker-clean: ## Remove agents Docker container/image only
	-@docker rm -f $(DOCKER_CONTAINER_NAME) 2>/dev/null || true
	-@docker rmi $(DOCKER_IMAGE) 2>/dev/null || true
