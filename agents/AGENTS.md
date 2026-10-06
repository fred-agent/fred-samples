# AGENTS.md — `agents/`

The sample agents pod: one Python package, `fred_samples_agents`, holding the samples registered in
`fred_samples_agents/registry.py`.

Root `CLAUDE.md` remains the primary development workflow and governance guide. This file only adds
what is specific to this directory.

---

## Required read order

Before changing files here, read:

1. `../CLAUDE.md`
2. `../AGENTS.md`
3. This file
4. Local `README.md`, `Makefile`, and `pyproject.toml`

---

## Local rules

- Keep sample agents didactic and easy to copy.
- Prefer existing patterns in `fred_samples_agents/`.
- Register a new agent in `fred_samples_agents/registry.py` — an unregistered agent is invisible to
  the pod, and `tests/test_registry.py` is what notices.
- Do not modify unrelated agents.
- Do not add production dependencies unless necessary.
- Keep runtime integration aligned with the public `fred-sdk` and `fred-runtime` APIs.
- Inspect the actual filesystem before changing imports, paths, configuration, or registry entries.

## Things about this package that surprise people

- **`make cli`, not `make chat`.** `make run` starts the pod; `make cli` opens the interactive chat
  client against a running one.
- **Two configuration profiles live in `config/`**, and this is the only package with two. Read
  `config/.env`'s `CONFIG_FILE` before starting anything — see the profile section in
  root `CLAUDE.md`.
- **Applications expose tools through MCP.** No `apps/*/capability` package is installed
  into this pod. Keep the dependency boundary: nothing under `fred_samples_agents/`
  imports application service code. Review Board's workflow definitions live here,
  while its service and MCP tools live under `apps/review-board/`.
- **`fred-sdk` and `fred-runtime` resolve from a sibling monorepo checkout by default**, also through
  `[tool.uv.sources]`. `make dev-pypi` forces PyPI resolution. Check what is actually installed in
  `.venv` before concluding anything about SDK behaviour.
- **MCP-backed samples need their servers running.** `config/mcp_catalog.yaml` lists the servers and
  ports; the servers themselves are under `../servers/mcp/python/`. Keep the catalog and the folder
  names in step.

---

## Validation

From this directory:

```bash
make code-quality
make test
```

Do not claim validation succeeded unless the commands were actually run successfully.
