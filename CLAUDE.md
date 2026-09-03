# CLAUDE.md

Audience: AI coding assistants and developers working in this repository.

This file is the primary development workflow and governance guide for this repository.

The repository is intended to be a didactic companion package for Fred-based agent development. It should remain easy to read, easy to run, and suitable as a starting point for new agent implementations.

---

## Repository parameters

Edit this section after cloning or renaming the repository. Treat these values as the single source of truth for repository-specific names used by AI assistants.

| Parameter | Value to adapt after cloning | Meaning |
|---|---|---|
| `REPOSITORY_NAME` | `<repository-name>` | The root repository or project folder name. |
| `REPOSITORY_PURPOSE` | `Didactic Fred agent sample package` | The purpose of this repository. |
| `AGENT_PACKAGE_DIR` | `<agent-package-dir>` | Directory containing the runnable agent package. Example: `agents`. |
| `PYTHON_PACKAGE_DIR` | `<python-package-dir>` | Directory containing the Python import package inside `AGENT_PACKAGE_DIR`. |
| `PYTHON_IMPORT_NAME` | `<python_import_name>` | Python import name of the sample agent package. |
| `CONFIG_DIR` | `<config-dir>` | Directory containing runtime or agent configuration, if any. |
| `MCP_SERVERS_DIR` | `<mcp-servers-dir>` | Directory containing sample MCP servers, if any. |
| `APPS_DIR` | `<apps-dir>` | Directory containing sample Fred applications (UI + service + capability), if any. |
| `DOCKERFILES_DIR` | `<dockerfiles-dir>` | Directory containing container build assets, if any. |
| `REFERENCE_FRED_REPOSITORY` | `<fred-repository-path-or-url>` | Upstream Fred repository used as implementation reference. |
| `REFERENCE_AGENT_PACKAGE` | `<fred-agent-package-path>` | Upstream Fred agent package used as style/integration reference. |
| `QUALITY_COMMAND` | `make code-quality` | Command to run quality checks from `AGENT_PACKAGE_DIR`. |
| `TEST_COMMAND` | `make test` | Command to run tests from `AGENT_PACKAGE_DIR`. |

Do not hardcode repository-specific names elsewhere in this file. When the repository or package layout changes, update this parameter table first.

---

## Prime directive

Keep the repository simple, educational, and aligned with the current Fred runtime.

Do not turn this repository into a second Fred core repository.

Prefer small, targeted, reversible changes. Avoid architectural invention unless explicitly requested.

---

## Relationship with Fred

This repository should integrate with Fred in the same spirit as `REFERENCE_AGENT_PACKAGE`, while remaining a standalone sample package.

When implementing or updating agents:

1. Prefer the current public `fred-sdk` and `fred-runtime` APIs.
2. Follow the runtime integration style used by `REFERENCE_AGENT_PACKAGE` where relevant.
3. Do not copy internal Fred core code unless explicitly requested.
4. Do not modify Fred core code from this repository.
5. Keep examples didactic, readable, and minimal.

Before changing runtime integration code, check the installed versions of:

- `fred-sdk`
- `fred-runtime`

---

## Local runtime & observability — `configuration.yaml` vs `configuration_prod.yaml`

This repository ships **two** config profiles per app (currently: `agents/config/configuration.yaml`
and `agents/config/configuration_prod.yaml`), and it matters which one is active — do not treat
them as interchangeable or assume `.env`'s current `CONFIG_FILE` value without checking it:

- `configuration.yaml` (security off, no infra) — the intentional 5-minute quick start: `cp
  agents/config/env.template agents/config/.env`, `make chat`. Use this when the task is exploring
  or modifying sample agent code with no real platform behind it.
- `configuration_prod.yaml` (security on: Keycloak, OpenFGA, Postgres, OpenSearch) — required for
  anything that needs the pod to behave like it would in a real Fred deployment: manual
  observability/log-watching sessions, KPI/metrics checks, or reproducing an auth-dependent bug.
  This profile expects the shared infra from the sibling `~/Fred/fred-deployment-factory` repo to
  be running.

For a real observability session, use the `.claude/skills/live-observability-session` skill in
this repo — it enforces switching `agents/config/.env`'s `CONFIG_FILE` to
`configuration_prod.yaml` before starting anything, and documents the exact ports/preconditions.
Never leave this ambiguous: if `.env` doesn't already point at the profile the task actually needs,
fix it and say so rather than proceeding on whatever it happened to be set to.

---

## Repository structure

Use the repository parameters above instead of assuming fixed folder names.

Typical areas may include:

```text
<AGENT_PACKAGE_DIR>/                  Runnable agent package
<AGENT_PACKAGE_DIR>/<PYTHON_PACKAGE_DIR>/  Python sample agents
<AGENT_PACKAGE_DIR>/<CONFIG_DIR>/     Agent or runtime configuration, if present
<MCP_SERVERS_DIR>/                    Sample MCP servers, if present
<APPS_DIR>/                           Sample Fred applications, if present
<DOCKERFILES_DIR>/                    Container build assets, if present
README.md                            User-facing repository guide
```

A sample application is not a sample agent: it ships its own container images
and is deployed alongside Fred rather than loaded into the agent pod. Its only
link to `AGENT_PACKAGE_DIR` is an optional capability package that the pod
depends on. Keep that dependency one-way — the agent package must never import
from `APPS_DIR`.

Each application folder name is also its `app_id`, and that id appears in the
nginx prefix, the deployment manifest, the control-plane catalog and the gateway
routes. Renaming a folder means changing all of them together.

`APPS_DIR` currently holds two applications and one shared deployment guide:

| Path | Who writes the record | Needs a secret |
|---|---|---|
| `<APPS_DIR>/document-triage/` | the human, under their own bearer | no |
| `<APPS_DIR>/progress-tracker/` | agents, through the app's own API | yes |
| `<APPS_DIR>/DEPLOYMENT.md` | shared guide covering both | — |

Prefer the `document-triage` shape when adding a new application. Agents may
read team-shared files but may only mutate inside their own subtree (FILES-04,
"agents never share"), so an application whose agents write shared state has to
keep that state outside Fred and reach it with a credential Fred neither issues
nor validates. `progress-tracker` documents that cost; do not repeat it without
a reason.

Not every repository clone must contain every optional area. Inspect the actual tree before changing files.

---

## Development rules

Before changing code or documentation:

1. Read this file.
2. Read root `AGENTS.md`.
3. Read any nested `AGENTS.md`, `AGENTS.override.md`, or `CLAUDE.md` files in the target directory.
4. Read the relevant package `README.md`.
5. Inspect nearby code before creating new abstractions.
6. Reuse existing sample patterns.
7. Keep changes minimal.

Do not introduce:

- unnecessary framework layers
- duplicate runtime wrappers
- custom execution protocols
- large generic abstractions
- hidden side effects
- undocumented environment assumptions
- production-only complexity in didactic samples

---

## Agent implementation rules

Sample agents must be:

- readable by new Fred developers
- explicit about runtime integration
- easy to run locally
- safe to use as templates
- documented with practical examples

When adding a new sample agent:

1. Place it under the configured `PYTHON_PACKAGE_DIR` inside `AGENT_PACKAGE_DIR`, unless the local package uses a different documented convention.
2. Add or update the local registry if the package has one.
3. Add configuration only when required.
4. Add a local README when the workflow is non-trivial.
5. Update the root `README.md` if the sample should be discoverable by users.

Avoid modifying unrelated agents.

---

## MCP server rules

MCP servers are sample dependencies, not production platform services.

When changing MCP servers:

1. Keep them self-contained.
2. Keep mock data obvious and deterministic.
3. Do not require external services for default tests.
4. Document ports, startup commands, and required agents.
5. Avoid production-only complexity.

---

## Testing and validation

For changes under `AGENT_PACKAGE_DIR`, run the configured commands from that directory:

```bash
cd <AGENT_PACKAGE_DIR>
<QUALITY_COMMAND>
<TEST_COMMAND>
```

Default validation must not require external cloud services.

If a sample requires a running MCP server or another local dependency, document that requirement clearly instead of hiding it in code.

Do not claim validation succeeded unless the commands were actually run successfully.

---

## Documentation rules

This repository is a teaching asset.

Documentation must be practical and copy-paste friendly.

Update documentation when changing:

- sample names
- package names
- import paths
- startup commands
- agent IDs
- MCP dependencies
- ports
- configuration shape
- runtime behavior
- validation commands

Use diagrams only when they clarify the workflow.

---

## Code style

Follow the style already present in the package.

Prefer:

- typed Python
- small functions
- clear names
- explicit configuration
- simple control flow
- readable examples over clever abstractions

Avoid broad rewrites unless explicitly requested.

---

## Dependency rules

Do not add production dependencies unless necessary.

Before adding a dependency:

1. Check whether the standard library is enough.
2. Check whether `fred-sdk` or `fred-runtime` already provides the capability.
3. Explain why the dependency is needed.
4. Keep dependency changes limited to the relevant package.

---

## Portability rule

This repository may be cloned and renamed. The agent package directory and Python import package may also be renamed.

AI assistants must not assume fixed names such as a specific repository folder, package directory, or Python import name. Use the repository parameters at the top of this file and inspect the actual filesystem before changing code.

---

## Close-out format

At the end of every implementation task, report:

```md
## Task close-out
- Code: <what changed>
- Tests: <commands run and result>
- Docs updated: <files updated, or "none">
- Scope control: <confirmation that unrelated code was not changed>
```

If validation could not be run, explain why.
