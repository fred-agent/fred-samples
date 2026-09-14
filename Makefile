# Repository-wide entry point. Each package below owns its own venv, baselines
# and Makefile; this one only fans out to them so `make test` from the root
# validates the whole repository in one command.
#
# MCP servers under servers/mcp/python/ are deliberately absent: their
# Makefiles install and run a server, they ship no test suite.

PACKAGES := agents knowledge-bases/local-folder knowledge-bases/git-repository knowledge-bases/webdav

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Packages: $(PACKAGES)"

define for_each_package
	@set -e; for package in $(PACKAGES); do \
		echo ""; \
		echo "── $$package ── $(1)"; \
		$(MAKE) --no-print-directory -C $$package $(1); \
	done
endef

.PHONY: dev
dev: ## Install every package's dependencies
	$(call for_each_package,dev)

.PHONY: test
test: ## Run every package's offline test suite
	$(call for_each_package,test)

.PHONY: code-quality
code-quality: ## Run every package's quality checks (ruff, bandit, detect-secrets, basedpyright)
	$(call for_each_package,code-quality)

.PHONY: code-quality-fix
code-quality-fix: ## Auto-fix formatting, imports and linting in every package
	$(call for_each_package,code-quality-fix)

.PHONY: clean
clean: ## Remove every package's virtual environment and cached files
	$(call for_each_package,clean)
