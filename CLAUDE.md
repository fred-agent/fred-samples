# CLAUDE.md

Audience: AI coding assistants and developers working in this repository.

This file is the primary development workflow and governance guide for `fred-samples`.

The repository is a didactic companion package for Fred-based development: runnable samples
that a third party can read, copy and deploy alongside a Fred installation. It must stay easy
to read, easy to run, and safe to use as a starting point.

---

## Prime directive

Keep the repository simple, educational, and aligned with the current Fred runtime.

Do not turn this repository into a second Fred core repository.

Prefer small, targeted, reversible changes. Avoid architectural invention unless explicitly
requested.

---

## Only claim what this repository can prove

This file, every README and every skill here describes **what is observable in this
repository**: the code, the configuration, the Makefiles, the installed package versions.

- Do not cite OpenSpec changes, RFCs, design documents or issue numbers that live in another
  repository. A reader of `fred-samples` cannot open them, and they go stale silently.
- Do not assert what Fred does or does not support unless the claim can be checked here —
  by reading the installed `fred-sdk` / `fred-runtime`, or by running something.
- When behaviour depends on the Fred version, name the version floor from the package's own
  `pyproject.toml` rather than pointing at an upstream discussion.

A statement that cannot be verified from this tree is either removed or rewritten as
something that can.

---

## Repository map

Five areas, and **three different validation regimes**. Knowing which regime an area is in is
the difference between a change that gets checked and one that does not.

| Path | What it holds | `make test` / `make code-quality` from the root | pre-commit |
|---|---|---|---|
| `agents/` | The sample agents pod: one package `fred_samples_agents` | yes | yes |
| `knowledge-bases/` | Three independent Knowledge Base pods | yes | yes |
| `servers/mcp/python/` | Five sample MCP servers | **no** — they ship no test suite | **no** |
| `apps/` | Two sample applications | **no** — see below | **no** |
| `dockerfiles/` | `Dockerfile` (agents pod), `Dockerfile.webdav-share` | — | — |

The root `Makefile` fans out to exactly four packages: `agents`, `knowledge-bases/local-folder`,
`knowledge-bases/git-repository`, `knowledge-bases/webdav`. `.pre-commit-config.yaml` matches
`^(agents|knowledge-bases)/`.

A new package with its own venv and baselines must be added to **both**, or nothing will ever
check it.

### `agents/`

One Python package, `fred_samples_agents`, holding five samples — `bank_transfer`,
`general_assistant`, `hello_graph`, `postal_tracking`, `team_of_3_agents_sample` — registered in
`registry.py`. It owns its venv, its `.baseline/` files and its `tests/`.

Its two useful targets are `make run` (start the pod) and `make cli` (open the interactive chat
client against a running pod). There is no `make chat` target.

It currently requires `fred-sdk>=3.4.1` and `fred-runtime>=3.4.2`.

### `knowledge-bases/`

Three standalone pods — `local-folder`, `git-repository`, `webdav` — each a package in its own
right with its own Makefile, venv, baselines and `config/`. A Knowledge Base pod publishes a
declaration to Fred and then serves runs; it opens no inbound port.

### `apps/`

A sample application is **not** a sample agent: it ships its own container images and is
deployed alongside Fred rather than loaded into the agent pod.

Each application folder is `api/` + `capability/` + `ui/` + `deploy.yaml` + `README.md`. There is
no Makefile, so **no target exists for the root Makefile to call** — their absence from
repository-wide validation is a consequence of that, not an oversight to fix by adding them to a
list.

What is and is not checked:

- `capability/` **is** installed, editable, into the `agents` venv — `agents/pyproject.toml`
  depends on `fred-capability-document-triage` and `fred-capability-progress-tracker`, resolved
  through `[tool.uv.sources]` to `../apps/*/capability`. So a capability that does not import
  cleanly breaks `make dev` in `agents/`.
- `api/`, `ui/` and `deploy.yaml` are covered by **no automated check in this repository**.

The dependency is one-way and expressed through packaging only: no module under
`agents/fred_samples_agents/` imports from `apps/`. Keep it that way.

Each application folder name is also its `app_id`, and that id appears in the nginx prefix, the
deployment manifest, the control-plane catalog and the gateway routes. Renaming a folder means
changing all of them together.

| Path | Who writes the record | Needs a secret |
|---|---|---|
| `apps/document-triage/` | the human, under their own bearer | no |
| `apps/progress-tracker/` | agents, through the app's own API | yes |
| `apps/DEPLOYMENT.md` | shared guide covering both | — |

Prefer the `document-triage` shape when adding an application. Agents may read team-shared files
but may only mutate inside their own subtree ("agents never share"), so an application whose
agents write shared state has to keep that state outside Fred and reach it with a credential Fred
neither issues nor validates. `progress-tracker` documents that cost; do not repeat it without a
reason.

### `.claude/skills/`

Three skills drive live sessions against the local stack: `live-observability-session` (the
agents pod), `live-knowledge-base-session` (a Knowledge Base pod), and `webdav-share` (serve a
folder over WebDAV and synchronize it). They are the fastest way to bring a sample up; read the
relevant one before improvising a startup sequence.

---

## Relationship with Fred

This repository integrates with Fred through the public SDK while remaining a standalone sample
package.

1. Prefer the current public `fred-sdk` and `fred-runtime` APIs.
2. Do not copy internal Fred core code unless explicitly requested.
3. Do not modify Fred core code from this repository.
4. Keep examples didactic, readable, and minimal.

Before changing runtime integration code, check the versions of `fred-sdk` and `fred-runtime`
actually installed in the package's venv — not the floor in `pyproject.toml`, which is a minimum.

Packages resolve `fred-sdk` / `fred-runtime` from a sibling monorepo checkout by default, through
`[tool.uv.sources]`. `make dev-pypi` forces PyPI resolution instead.

---

## Configuration style — no component invents its own (mandatory)

**Every sample here is configured exactly the way a Fred backend is, with no stylistic difference
whatsoever.** One `configuration.yaml` loaded through `CONFIG_FILE`, the same Pydantic models from
`fred_core`, the same YAML keys, and environment variables carrying **secrets only**. A sample that
configures itself differently teaches a third-party contributor the wrong thing, and a contributor
who learns a second configuration model is the cost this rule exists to avoid.

Before adding or touching any configuration:

- **Reuse the model, never a parallel one.** `fred_core.security.structure` already has
  `SecurityConfiguration`, `M2MSecurity`, `UserSecurity`, `RebacConfiguration`;
  `fred_core.common.structures` has `TemporalSchedulerConfig`, `ModelConfiguration`, the store and
  KPI sink configs. Keycloak is `security.m2m` with `realm_url`, `client_id`, `secret_env_var` —
  never a hand-rolled trio of environment variables.
- **Keep the keys identical.** `security.m2m.realm_url`, `scheduler.temporal.host`, and so on. A
  renamed key is a difference of style, and none is accepted.
- **Load it the shared way** — `fred_core.common.config_loader`, resolved from `CONFIG_FILE` — so
  local development and a Kubernetes Deployment differ only in where the file is mounted, never in
  mechanism.
- **Environment carries secrets and nothing else.** A non-secret value in an environment variable
  is a bug, because it cannot be reviewed in a ConfigMap with the rest.

### What a Knowledge Base pod's configuration looks like

A Knowledge Base pod loads `configuration.yaml` through `$CONFIG_FILE` like every other component;
`security.m2m` is parsed by `M2MSecurity` and `scheduler.temporal` by `TemporalSchedulerConfig` —
the same model the Control Plane parses. The environment carries the client secret alone.

Two deliberate absences, both worth knowing before editing a sample's config:

- **`security.user` is absent on purpose.** A Knowledge Base pod serves no user, opens no inbound
  port and validates no user token. Do not add a block it would never read.
- **`scheduler.temporal.task_queue` is absent on purpose.** A run's queue is derived from the
  definition id on both sides, so a pod and the Control Plane cannot disagree about it. Making it
  configurable would be the bug.

A test must never pick up the configuration a developer happens to have: `./config/configuration.yaml`
is the default a pod resolves, so every sample's `tests/conftest.py` points `$CONFIG_FILE` and
`$ENV_FILE` at nonexistent paths. Keep that fixture when adding a suite.

---

## Two configuration profiles — `agents/` only

`agents/config/` ships **two** profiles, and it matters which one is active. Do not assume `.env`'s
current `CONFIG_FILE` value without reading it:

- `configuration.yaml` (security off, no infra) — the intentional quick start:
  `cp agents/config/env.template agents/config/.env`, then `make run` and `make cli`. Use this when
  the task is exploring or modifying sample agent code with no real platform behind it.
- `configuration_prod.yaml` (security on: Keycloak, OpenFGA, Postgres, OpenSearch) — required for
  anything that needs the pod to behave like it would in a real Fred deployment: manual
  observability sessions, KPI/metrics checks, or reproducing an auth-dependent bug. This profile
  expects the shared infra from the sibling `~/Fred/fred-deployment-factory` repo to be running.

If `.env` does not already point at the profile the task needs, fix it and say so rather than
proceeding on whatever it happened to be set to. The `live-observability-session` skill enforces
this and documents the exact ports and preconditions.

**The three Knowledge Base packages have one profile each**, not two: they authenticate to Fred on
every run, so a security-off profile would not describe anything they can actually do. Do not add a
`configuration_prod.yaml` to them to make the shapes match.

---

## Development rules

Before changing code or documentation:

1. Read this file.
2. Read root `AGENTS.md`.
3. Read any nested `AGENTS.md` or `CLAUDE.md` in the target directory.
4. Read the relevant package `README.md` and `Makefile`.
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

## Sample agent rules

Sample agents must be readable by new Fred developers, explicit about runtime integration, easy to
run locally, safe to use as templates, and documented with practical examples.

When adding one:

1. Place it under `agents/fred_samples_agents/`.
2. Register it in `agents/fred_samples_agents/registry.py`.
3. Add configuration only when required.
4. Add a local README when the workflow is non-trivial.
5. Update the root `README.md` if the sample should be discoverable by users.

Avoid modifying unrelated agents.

---

## MCP server rules

MCP servers under `servers/mcp/python/` are sample dependencies, not production platform services.
They ship no test suite, which is why repository-wide validation skips them — their correctness is
established by running them.

1. Keep them self-contained.
2. Keep mock data obvious and deterministic.
3. Do not require external services for default tests.
4. Document ports, startup commands, and the agents that depend on them.
5. Avoid production-only complexity.

A server's port and folder name appear in `agents/config/mcp_catalog.yaml`. When either changes,
update the catalog in the same commit — a wrong path there is invisible until someone tries to
start the server.

---

## Testing and validation

From the repository root, before reporting any change done:

```bash
make code-quality
make test
```

Both fan out to the four registered packages. To work on one package, run the same two targets from
that package's directory.

Default validation must not require external cloud services. If a sample requires a running MCP
server or another local dependency, document that requirement clearly instead of hiding it in code.

Do not claim validation succeeded unless the commands were actually run successfully. If validation
could not be run, say so and say why.

---

## Documentation rules

This repository is a teaching asset. Documentation must be practical and copy-paste friendly, and a
command written in a README must be a command that exists.

Update documentation when changing: sample names, package names, import paths, startup commands,
agent IDs, MCP dependencies, ports, configuration shape, runtime behavior, or validation commands.

Use diagrams only when they clarify the workflow.

---

## Code style

Follow the style already present in the package. Prefer typed Python, small functions, clear names,
explicit configuration, simple control flow, and readable examples over clever abstractions.

Avoid broad rewrites unless explicitly requested.

---

## Dependency rules

Do not add production dependencies unless necessary. Before adding one:

1. Check whether the standard library is enough.
2. Check whether `fred-sdk` or `fred-runtime` already provides the capability.
3. Explain why the dependency is needed.
4. Keep the change limited to the relevant package.

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
