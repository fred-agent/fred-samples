TARGET  := $(CURDIR)/target
VENV    := $(CURDIR)/.venv
PYTHON  := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
UV      := $(VENV)/bin/uv
UV_CACHE_DIR := $(TARGET)/.uv-cache
UV_RUN  := UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV)

PY_PACKAGE := fred_samples_webdav_kb
CLI        := fred-samples-webdav-kb-dev

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
# (local monorepo checkout, sibling to this repo) by default, exactly as the
# local-folder sample does — so a change to the SDK is tried here before it is
# released. `make dev-pypi` resolves the published SDK instead, which is what
# the deployable image and an outside consumer of this repository get.
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

# ── A share to try it against ──────────────────────────────────────────────────
# Apache mod_dav + mod_dav_fs in a container — the same pair a production share
# runs, so what passes here is not passing against a stand-in that agrees with
# us. Same image/container/network variables as agents/Makefile, and it joins
# fred-shared-network (created by fred-deployment-factory's `make docker-up`)
# so a containerised Fred reaches it as http://$(SHARE_CONTAINER_NAME)/dav/
# while the host reaches it as $(SHARE_URL).
#
# The targets are share-* rather than docker-*: this package's own image is the
# Knowledge Base pod, and those names belong to it.

SHARE_IMAGE_NAME     ?= fred-samples-webdav-share
SHARE_IMAGE_TAG      ?= latest
SHARE_IMAGE          ?= $(SHARE_IMAGE_NAME):$(SHARE_IMAGE_TAG)
SHARE_CONTAINER_NAME ?= fred-samples-webdav-share
SHARE_DOCKERFILE     ?= ../../dockerfiles/Dockerfile.webdav-share
SHARE_CONTEXT        ?= ../..
SHARE_NETWORK        ?= fred-shared-network
SHARE_HOST_PORT      ?= 8088
SHARE_HTTPS_PORT     ?= 8443
SHARE_TLS_DIR        ?=
SHARE_DIR            ?= $(CURDIR)/local-webdav/documents
SHARE_URL            ?= http://localhost:$(SHARE_HOST_PORT)/dav/

.PHONY: share-build
share-build: ## Build the WebDAV share image
	docker build \
		-f $(SHARE_DOCKERFILE) \
		-t $(SHARE_IMAGE) \
		$(SHARE_CONTEXT)

.PHONY: share-run
share-run: ## Serve a folder over WebDAV: make share-run [SHARE_DIR=/path/to/documents]
	@test -d "$(SHARE_DIR)" || { echo "No such folder: $(SHARE_DIR)"; exit 1; }
	@if [ -n "$(SHARE_TLS_DIR)" ]; then \
		test -f "$(SHARE_TLS_DIR)/server.crt" && test -f "$(SHARE_TLS_DIR)/server.key" \
		|| { echo "SHARE_TLS_DIR must contain server.crt and server.key"; exit 1; }; \
	fi
	@docker image inspect $(SHARE_IMAGE) >/dev/null 2>&1 || $(MAKE) --no-print-directory share-build
	-@docker rm -f $(SHARE_CONTAINER_NAME) >/dev/null 2>&1 || true
	@docker network inspect $(SHARE_NETWORK) >/dev/null 2>&1 \
		|| docker network create $(SHARE_NETWORK) >/dev/null
	docker run -d \
		--name $(SHARE_CONTAINER_NAME) \
		--network $(SHARE_NETWORK) \
		-p $(SHARE_HOST_PORT):80 \
		-v "$(SHARE_DIR)":/srv/share:ro \
		$(if $(SHARE_TLS_DIR),-p $(SHARE_HTTPS_PORT):443 -v "$(abspath $(SHARE_TLS_DIR))":/run/webdav-tls:ro,) \
		$(SHARE_IMAGE) $(if $(SHARE_TLS_DIR),httpd-foreground -D WEBDAV_TLS,)
	@echo ""
	@echo "Serving $(SHARE_DIR)"
	@echo "  from the host      : $(SHARE_URL)"
	@if [ -n "$(SHARE_TLS_DIR)" ]; then echo "  HTTPS              : https://localhost:$(SHARE_HTTPS_PORT)/dav/"; fi
	@echo "  from a container   : http://$(SHARE_CONTAINER_NAME)/dav/"
	@echo ""
	@echo "  make sync URL=$(SHARE_URL)"

.PHONY: share-stop
share-stop: ## Stop the WebDAV share
	-@docker rm -f $(SHARE_CONTAINER_NAME) 2>/dev/null || true

.PHONY: share-logs
share-logs: ## Follow the share's access log
	docker logs -f $(SHARE_CONTAINER_NAME)

.PHONY: share-clean
share-clean: ## Remove the share's container and image
	-@docker rm -f $(SHARE_CONTAINER_NAME) 2>/dev/null || true
	-@docker rmi $(SHARE_IMAGE) 2>/dev/null || true

# ── The deployable image ───────────────────────────────────────────────────────
# Same shape as agents/Makefile, pointed at the shared Knowledge Base
# Dockerfile. The context is the repository root because the Dockerfile copies
# knowledge-bases/$(KB)/, so these are run from here and reach two levels up.
#
# The image resolves fred-sdk from PyPI (`--no-sources` in the Dockerfile), so
# it builds from a standalone checkout of this repository with no sibling
# monorepo — which is what a CI runner has.

KB                    ?= webdav
KB_PACKAGE            ?= fred_samples_webdav_kb
DOCKER_REGISTRY       ?= ghcr.io/fred-agent/fred-samples
DOCKER_IMAGE_NAME     ?= $(DOCKER_REGISTRY)/fred-samples-webdav-kb
DOCKER_IMAGE_TAG      ?= latest
DOCKER_IMAGE          ?= $(DOCKER_IMAGE_NAME):$(DOCKER_IMAGE_TAG)
DOCKER_CONTAINER_NAME ?= fred-samples-webdav-kb
DOCKERFILE_PATH       ?= ../../dockerfiles/Dockerfile.knowledge-base
DOCKER_CONTEXT        ?= ../..
DOCKER_NETWORK        ?= fred-shared-network
DOCKER_USER_NAME      ?= fred-user
# Fixed, unlike the agents pod's: an image pushed to a registry must not carry
# whichever uid the developer who built it happens to have.
DOCKER_USER_ID        ?= 1000
DOCKER_GROUP_ID       ?= 1000
CONFIG_DIR            ?= $(CURDIR)/config
CONTAINER_CONFIG_DIR  ?= /app/config

.PHONY: docker-build
docker-build: ## Build the deployable image for this Knowledge Base pod
	docker build \
		-f $(DOCKERFILE_PATH) \
		--build-arg KB=$(KB) \
		--build-arg KB_PACKAGE=$(KB_PACKAGE) \
		--build-arg USER_NAME=$(DOCKER_USER_NAME) \
		--build-arg USER_ID=$(DOCKER_USER_ID) \
		--build-arg GROUP_ID=$(DOCKER_GROUP_ID) \
		-t $(DOCKER_IMAGE) \
		$(DOCKER_CONTEXT)

# What CI runs between building the image and pushing it. With no network at
# all: the SDK's entry point loads this Knowledge Base, the declaration it would
# publish can be projected, the `run` path imports its workflow engine, and no
# secrets file reached the image.
.PHONY: docker-smoke
docker-smoke: ## Check the built image offline before pushing it
	docker run --rm --network none $(DOCKER_IMAGE) --help >/dev/null
	docker run --rm --network none --entrypoint $(CLI) $(DOCKER_IMAGE) declaration >/dev/null
	docker run --rm --network none --entrypoint python $(DOCKER_IMAGE) \
		-c "import $(PY_PACKAGE).document_boundary, temporalio"
	docker run --rm --network none --entrypoint sh $(DOCKER_IMAGE) \
		-c 'test ! -e /app/config/.env || { echo "config/.env is in the image"; exit 1; }'
	@echo "Image $(DOCKER_IMAGE) passed the offline smoke test."

.PHONY: docker-push
docker-push: ## Push the image (docker login ghcr.io first)
	docker push $(DOCKER_IMAGE)

.PHONY: docker-publish
docker-publish: ## Declare this Knowledge Base to Fred, from the image
	docker run --rm \
		--network $(DOCKER_NETWORK) \
		--add-host=host.docker.internal:host-gateway \
		-v $(realpath $(CONFIG_DIR)):$(CONTAINER_CONFIG_DIR):ro \
		-e ENV_FILE=$(CONTAINER_CONFIG_DIR)/.env \
		$(DOCKER_IMAGE) publish

.PHONY: docker-run
docker-run: ## Serve runs from the image, joined to fred-shared-network
	docker run --rm \
		--name $(DOCKER_CONTAINER_NAME) \
		--network $(DOCKER_NETWORK) \
		--add-host=host.docker.internal:host-gateway \
		-v $(realpath $(CONFIG_DIR)):$(CONTAINER_CONFIG_DIR):ro \
		-e ENV_FILE=$(CONTAINER_CONFIG_DIR)/.env \
		$(DOCKER_IMAGE)

.PHONY: docker-sync
docker-sync: ## Dry-run a share from inside the image: make docker-sync URL=https://host/folder/
	@if [ -z "$(URL)" ]; then \
		echo "Provide a share: make docker-sync URL=https://host/folder/"; \
		exit 1; \
	fi
	docker run --rm \
		--entrypoint fred-samples-webdav-kb-dev \
		$(DOCKER_IMAGE) sync --url '$(URL)'

.PHONY: docker-stop
docker-stop: ## Stop the running container by name
	-@docker stop $(DOCKER_CONTAINER_NAME) 2>/dev/null || true

.PHONY: docker-clean
docker-clean: ## Remove this container and image only
	-@docker rm -f $(DOCKER_CONTAINER_NAME) 2>/dev/null || true
	-@docker rmi $(DOCKER_IMAGE) 2>/dev/null || true

# ── Offline developer tool ─────────────────────────────────────────────────────

.PHONY: declaration
declaration: dev ## Print what the image's `publish` command would send to Fred
	VIRTUAL_ENV= $(UV_RUN) run $(CLI) declaration

.PHONY: sync
sync: dev ## Synchronize a share once: make sync URL=https://host/folder/ [LIBRARY=... INCLUDE=...]
	@if [ -z "$(URL)" ]; then \
		echo "Provide a share: make sync URL=https://host/folder/"; \
		exit 1; \
	fi
	ENV_FILE=$(ENV_FILE) VIRTUAL_ENV= $(UV_RUN) run $(CLI) sync $(SYNC_FLAGS)

.PHONY: watch
watch: dev ## Synchronize over and over: make watch URL=... LIBRARY=... [INTERVAL=30]
	@if [ -z "$(URL)" ]; then \
		echo "Provide a share: make watch URL=http://localhost:8088/dav/"; \
		exit 1; \
	fi
	ENV_FILE=$(ENV_FILE) VIRTUAL_ENV= $(UV_RUN) run $(CLI) watch $(SYNC_FLAGS) \
		$(if $(INTERVAL),--interval $(INTERVAL),)

# Shared by sync and watch: one list, so the two can never drift apart.
# WEBDAV_USER, never USER: make imports the environment, and $USER is always set
# to the developer's login — so `--username <login>` would be sent to every
# share without anyone asking for it.
SYNC_FLAGS = --url '$(URL)' \
	$(if $(WEBDAV_USER),--username '$(WEBDAV_USER)',) \
	$(if $(INCLUDE),--include '$(INCLUDE)',) \
	$(if $(EXCLUDE),--exclude '$(EXCLUDE)',) \
	$(if $(MAX_FILES),--max-files $(MAX_FILES),) \
	$(if $(PROFILE),--profile '$(PROFILE)',) \
	$(if $(TRUST_ANY_CERTIFICATE),--trust-any-certificate,) \
	$(if $(LIBRARY),--library '$(LIBRARY)',)

# ── Code quality ───────────────────────────────────────────────────────────────
# Same tool set and baselines as the sibling packages: ruff (lint + format),
# bandit (SAST), detect-secrets (leak scan), basedpyright (type-check).

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
