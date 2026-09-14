TARGET  := $(CURDIR)/target
VENV    := $(CURDIR)/.venv
PYTHON  := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
UV      := $(VENV)/bin/uv
UV_CACHE_DIR := $(TARGET)/.uv-cache
UV_RUN  := UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV)

PY_PACKAGE := fred_samples_local_folder_kb
CLI        := fred-samples-local-folder-kb-dev

ENV_FILE ?= $(CURDIR)/config/.env

BASELINE_DIR ?= $(CURDIR)/.baseline
BANDIT_BASELINE_FILE ?= $(BASELINE_DIR)/bandit-baseline.json
DETECT_SECRET_BASELINE_FILE ?= $(BASELINE_DIR)/detect-secret-baseline.json
DETECT_SECRET_EXCLUDE_FILES ?= detect-secret-baseline\.json
BASEDPYRIGHT_BASELINE_FILE ?= $(BASELINE_DIR)/basedpyright-baseline.json
BANDIT_IGNORED_RULES ?= B101,B108

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

# pyproject.toml's [tool.uv.sources] points fred-sdk at ../../../fred/libs
# (local monorepo checkout, sibling to this repo) by default. The Knowledge
# Base authoring surface is not published yet, so `make dev-pypi` cannot work
# for this sample until fred-sdk 3.7.0 is on PyPI.
$(TARGET)/.compiled: pyproject.toml $(TARGET)/.uv-installed
	VIRTUAL_ENV= $(UV_RUN) sync --extra dev
	@touch $@

.PHONY: dev
dev: $(TARGET)/.compiled ## Install dependencies (local fred-sdk, see [tool.uv.sources])
	@echo "Dependencies installed."

.PHONY: dev-pypi
dev-pypi: $(TARGET)/.uv-installed ## Install strictly from PyPI, ignoring [tool.uv.sources]
	VIRTUAL_ENV= $(UV_RUN) sync --extra dev --no-sources
	@touch $(TARGET)/.compiled
	@echo "Dependencies installed from PyPI only (--no-sources)."

.PHONY: update
update: $(TARGET)/.uv-installed ## Re-resolve and update all dependencies
	VIRTUAL_ENV= $(UV_RUN) sync --extra dev
	@touch $(TARGET)/.compiled

# ── Main targets ───────────────────────────────────────────────────────────────

# ── The image's two commands ───────────────────────────────────────────────────
# Same shape as the agents pod: $ENV_FILE names the secrets file, which names
# $CONFIG_FILE in turn. A deployed pod mounts a Secret and a ConfigMap and sets
# the same two variables in its Deployment spec.

.PHONY: publish
publish: dev ## Declare this Knowledge Base to Fred (the deployment step)
	@test -f $(ENV_FILE) || { echo "Missing $(ENV_FILE) — copy config/env.template"; exit 1; }
	ENV_FILE=$(ENV_FILE) VIRTUAL_ENV= $(UV_RUN) run python -m $(PY_PACKAGE) publish

.PHONY: run
run: dev ## Serve runs until stopped (needs Temporal and the Control Plane)
	@test -f $(ENV_FILE) || { echo "Missing $(ENV_FILE) — copy config/env.template"; exit 1; }
	ENV_FILE=$(ENV_FILE) VIRTUAL_ENV= $(UV_RUN) run python -m $(PY_PACKAGE) run

# ── Offline developer tool ─────────────────────────────────────────────────────

.PHONY: declaration
declaration: dev ## Print what the image's `publish` command would send to Fred
	VIRTUAL_ENV= $(UV_RUN) run $(CLI) declaration

.PHONY: sync
sync: dev ## Synchronize a folder once: make sync ROOT=/path/to/notes
	@if [ -z "$(ROOT)" ]; then \
		echo "Provide a folder: make sync ROOT=/path/to/notes"; \
		exit 1; \
	fi
	VIRTUAL_ENV= $(UV_RUN) run $(CLI) sync --root-path $(ROOT)

.PHONY: watch
watch: dev ## Synchronize a folder over and over: make watch ROOT=/path/to/notes
	@if [ -z "$(ROOT)" ]; then \
		echo "Provide a folder: make watch ROOT=/path/to/notes"; \
		exit 1; \
	fi
	@test -f $(ENV_FILE) || { echo "Missing $(ENV_FILE) — copy config/env.template"; exit 1; }
	set -a; . $(ENV_FILE); set +a; VIRTUAL_ENV= $(UV_RUN) run $(CLI) watch \
		--root-path $(ROOT) $(if $(INTERVAL),--interval $(INTERVAL),)

# ── Code quality ───────────────────────────────────────────────────────────────
# Same tool set and baselines as ../../agents: ruff (lint + format), bandit
# (SAST), detect-secrets (leak scan), basedpyright (type-check).

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
