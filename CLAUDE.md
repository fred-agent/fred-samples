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

Six areas, and **four different validation regimes**. Knowing which regime an area is in is
the difference between a change that gets checked and one that does not.

| Path | What it holds | `make test` / `make code-quality` from the root | pre-commit |
|---|---|---|---|
| `agents/` | The sample agents pod: one package `fred_samples_agents` | yes | yes |
| `knowledge-bases/` | Three independent Knowledge Base pods | yes | yes |
| `servers/mcp/python/` | Five sample MCP servers | **no** — they ship no test suite | **no** |
| `apps/` | Three sample applications | **no** — see below | **no** |
| `dockerfiles/` | `Dockerfile` (agents pod), `Dockerfile.knowledge-base` (the three Knowledge Base pods), `Dockerfile.webdav-share` (a test fixture) | — | — |
| `charts/` | Helm charts for deployable samples | **no** — validated by Helm in GitHub Actions | **no** |

The root `Makefile` fans out to exactly four packages: `agents`, `knowledge-bases/local-folder`,
`knowledge-bases/git-repository`, `knowledge-bases/webdav`. `.pre-commit-config.yaml` matches
`^(agents|knowledge-bases)/`.

A new package with its own venv and baselines must be added to **both**, or nothing will ever
check it.

### `agents/`

One Python package, `fred_samples_agents`, holding six sample groups — `document_review`, `bank_transfer`,
`general_assistant`, `hello_graph`, `postal_tracking`, `team_of_3_agents_sample` — registered in
`registry.py`. It owns its venv, its `.baseline/` files and its `tests/`.

Its two useful targets are `make run` (start the pod) and `make cli` (open the interactive chat
client against a running pod). There is no `make chat` target.

It currently requires `fred-sdk>=4.0.0` and `fred-runtime>=4.0.0`.

### `knowledge-bases/`

Three standalone pods — `local-folder`, `git-repository`, `webdav` — each a package in its own
right with its own Makefile, venv, baselines and `config/`. A Knowledge Base pod publishes a
declaration to Fred and then serves runs; it opens no inbound port.

### Published images

`.github/workflows/Build-and-push-docker.yml` publishes the Knowledge
Base images in its matrix — today only `fred-samples-webdav-kb` — to
`ghcr.io/fred-agent/fred-samples/`, after `UV_NO_SOURCES=1 make test`, a build and
`make docker-smoke`. A git tag `code/v1.2.3` publishes image tag `v1.2.3` and creates a GitHub
release; a push to `swift` publishes `swift-dev` and `swift-dev-<short sha>`. Tags and the release
command are documented in `dockerfiles/README.md`. Do not create a release tag unless asked: it
publishes an image other people deploy.

### `apps/`

A sample application is **not** a sample agent: it ships its own container images and is
deployed alongside Fred rather than loaded into the agent pod.

Each application has `api/`, `ui/`, `deploy.yaml`, tests and a README. There is
no application Makefile, so root validation does not cover these services. Run
the app-local tests with that application's dependencies as documented in its README.

Applications ship no Python capability packages. The agent pod has no path dependency
on `apps/`. Progress Tracker and Review Board expose MCP endpoints; Document Triage
uses Fred's platform document tools for agent reads. Preserve that separation.

Each folder name is also its `app_id`; coordinate changes with routes, deployment
manifests and catalog entries. Each backend validates caller identity and team access.

| Path | Who writes the record | Storage |
|---|---|---|
| `apps/document-triage/` | the human, under their bearer | Knowledge Flow workspace |
| `apps/progress-tracker/` | humans and agents through REST/MCP | app-owned SQLite |
| `apps/review-board/` | staged agent workflow through REST/MCP | app-owned OpenSearch |
| `apps/DEPLOYMENT.md` | shared integration guide | — |

These services need their own backend security credentials. They do not use a shared
agent service key: application tools validate the caller's bearer or configured
delegated identity. Read the application README before changing its admission rules.

Helm validation/publication lives in `.github/workflows/Build-and-push-helm.yml`;
the Docker workflow does not cover the entire repository.

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
`fred_pod`, the same YAML keys, and environment variables carrying **secrets only**. A sample that
configures itself differently teaches a third-party contributor the wrong thing, and a contributor
who learns a second configuration model is the cost this rule exists to avoid.

Before adding or touching any configuration:

- **Reuse the model, never a parallel one.** `fred_pod.security.structure` already has
  `SecurityConfiguration`, `M2MSecurity`, `UserSecurity`, `RebacConfiguration`;
  `fred_pod.common.structures` has `TemporalSchedulerConfig`, `ModelConfiguration`, the store and
  KPI sink configs. Keycloak is `security.m2m` with `realm_url`, `client_id`, `secret_env_var` —
  never a hand-rolled trio of environment variables.
- **Keep the keys identical.** `security.m2m.realm_url`, `scheduler.temporal.host`, and so on. A
  renamed key is a difference of style, and none is accepted.
- **Load it the shared way** — `fred_pod.common.config_loader`, resolved from `CONFIG_FILE` — so
  local development and a Kubernetes Deployment differ only in where the file is mounted, never in
  mechanism.
- **Environment carries secrets and nothing else.** A non-secret value in an environment variable
  is a bug, because it cannot be reviewed in a ConfigMap with the rest.

### What a Knowledge Base pod's configuration looks like

A Knowledge Base pod loads `configuration.yaml` through `$CONFIG_FILE` like every other component;
`security.m2m` is parsed by `M2MSecurity` and `scheduler.temporal` by `TemporalSchedulerConfig` —
the same model the Control Plane parses. The environment carries the client secret alone.

A setting that belongs to one sample's operator rather than to a team's form goes in a top-level
section named after that sample — `webdav:` in `knowledge-bases/webdav/` — parsed by a subclass of the
SDK's `PodConfiguration` so it comes from the same file through the same loader, and checked when the
pod starts. Never an environment variable, and never a new key under `knowledge_base:`, which is the
SDK's.

Two deliberate absences, both worth knowing before editing a sample's config:

- **`security.user` is absent on purpose.** A Knowledge Base pod serves no user, opens no inbound
  port and validates no user token. Do not add a block it would never read.
- **`scheduler.temporal.task_queue` is absent on purpose.** A run's queue is derived from the
  definition id on both sides, so a pod and the Control Plane cannot disagree about it. Making it
  configurable would be the bug.

A test must never pick up the configuration a developer happens to have: `./config/configuration.yaml`
is the default a pod resolves, so every sample's `tests/conftest.py` points `$CONFIG_FILE` and
`$ENV_FILE` at nonexistent paths. Keep that fixture when adding a suite.

**A pod's ledger never lives in this repository.** A sample that remembers what it already published
keeps one small JSON file per instance, and it belongs outside the working tree — the default already
is (`${XDG_STATE_HOME:-~/.local/state}/<package>/`), which is why nothing about it appears in
`.gitignore` and nothing should. The per-sample `FRED_SAMPLES_*_STATE_DIR` variable moves it, to a
volume on a deployed pod and never to a path inside this checkout: it is per-machine, per-instance
state, and a ledger committed by accident makes the next run believe a library already holds
documents it does not. The full rule, with the reasoning, is in
[`knowledge-bases/README.md`](knowledge-bases/README.md).

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
