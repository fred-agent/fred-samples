TARGET  := $(CURDIR)/target
VENV    := $(CURDIR)/.venv
PYTHON  := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
UV      := $(VENV)/bin/uv

DOCKER_IMAGE_NAME     ?= fred-samples-agents
DOCKER_IMAGE_TAG      ?= latest
DOCKER_IMAGE          ?= $(DOCKER_IMAGE_NAME):$(DOCKER_IMAGE_TAG)
DOCKER_CONTAINER_NAME ?= fred-samples-agents
DOCKERFILE_PATH       ?= ../dockerfiles/Dockerfile
DOCKER_CONTEXT        ?= ..
DOCKER_USER_NAME      ?= fred-user
DOCKER_USER_ID        ?= $(shell id -u)
DOCKER_GROUP_ID       ?= $(shell id -g)
HOST_PORT             ?= 8010
CONTAINER_PORT        ?= 8010
CONFIG_DIR            ?= $(CURDIR)/config
CONTAINER_CONFIG_DIR  ?= /app/config

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Bootstrap ──────────────────────────────────────────────────────────────────

$(TARGET)/.venv-created:
	@mkdir -p $(TARGET)
	python3 -m venv $(VENV)
	@touch $@

$(TARGET)/.uv-installed: $(TARGET)/.venv-created
	$(PIP) install --upgrade pip setuptools wheel
	$(PIP) install uv
	@touch $@

$(TARGET)/.compiled: pyproject.toml $(TARGET)/.uv-installed
	$(UV) sync --extra dev
	@touch $@

.PHONY: dev
dev: $(TARGET)/.compiled ## Install dependencies
	@echo "✅ Dependencies installed."

# ── Main targets ───────────────────────────────────────────────────────────────

.PHONY: run
run: dev ## Start the sample agents pod locally
	ENV_FILE=$(CURDIR)/config/.env VIRTUAL_ENV= $(UV) run python -m fred_samples_agents

.PHONY: chat
chat: dev ## Open the interactive chat client for a running pod
	VIRTUAL_ENV= $(UV) run fred-agent-chat

.PHONY: update
update: $(TARGET)/.uv-installed ## Re-resolve and update all dependencies
	$(UV) sync
	@touch $(TARGET)/.compiled

.PHONY: lint
lint: dev ## Run ruff linter
	$(UV) run ruff check fred_samples_agents/

# ── Docker ─────────────────────────────────────────────────────────────────────

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
docker-run: ## Run the agents pod container locally
	docker run --rm \
		--name $(DOCKER_CONTAINER_NAME) \
		-p $(HOST_PORT):$(CONTAINER_PORT) \
		-v $(realpath $(CONFIG_DIR)):$(CONTAINER_CONFIG_DIR):ro \
		-e CONFIG_FILE=$(CONTAINER_CONFIG_DIR)/configuration.yaml \
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

.PHONY: clean
clean: ## Remove the virtual environment and cached files
	rm -rf $(VENV) $(TARGET)
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
