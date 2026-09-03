---
name: live-observability-session
description: Start the fred-samples agent pod (and any MCP demo servers a sample agent depends on) and watch its logs live while the developer drives the `fred-agents-cli` chat client or an API call by hand. Use for manual observability sessions on this reference agent pod, or to check "does X actually log/emit correctly" against fred_core's stdout(+KPI) model.
user-invocable: true
argument-hint: [optional: which MCP demo servers to include alongside the pod — bank_core/risk_guard/postal-service/iot-tracking, depending on which sample agent is in scope]
---

# Live Observability Session (fred-samples)

A **collaborative** working mode, not an automated test run: the developer drives the
`fred-agents-cli` chat client (`make chat`) or hits the API by hand; you start the pod, tail its
stdout, and report what you see. You never call a business endpoint or drive a scenario yourself
— see "The protocol" below. This mirrors `~/Fred/fred/.claude/skills/live-observability-session/SKILL.md`
(the canonical version, same protocol) — adapted here because fred-samples ships exactly **one**
agent pod, no frontend, and a handful of standalone demo MCP servers instead of the shared
platform's core APIs.

**Scope: this targets the fred-samples repo (`~/Fred/fred-samples`), on top of the same
`fred-deployment-factory` local dev stack fred's own skill uses.** If the developer is on a
different deployment target (e.g. the `fred-agents-cli`-only quick start with no Keycloak/Postgres
at all), ask before assuming any of the below still holds.

## The one rule this skill exists to enforce: always `configuration_prod.yaml`

**A live observability session is a security-on, `fred-deployment-factory`-backed session, full
stop — never the standalone `configuration.yaml`.** The two profiles genuinely serve different
purposes here and must not be conflated:

- `configuration.yaml` (security off) is the repo's own **5-minute quick start** — no Keycloak,
  no Postgres, `make chat` against a bare pod. Good for a developer exploring the sample code
  itself. **Not** what this skill is for.
- `configuration_prod.yaml` (security on, Keycloak/OpenFGA/Postgres/OpenSearch) is what a real
  observability session needs — it's the only profile where the pod behaves the way it would in
  the actual platform (auth enforced, KPIs flowing to Prometheus/OpenSearch, ReBAC checked).

Before starting anything, open `agents/config/.env` (copied from `agents/config/env.template`,
which — as committed — defaults to `CONFIG_FILE="./config/configuration.yaml"`, intentionally, for
the quick-start persona) and confirm it instead reads:

    CONFIG_FILE="./config/configuration_prod.yaml"

If it doesn't, **fix it and say so** rather than asking permission — `.env` is a local, gitignored
file meant to be edited per session; there is nothing to preserve by leaving it on the wrong
profile. `make run` (not a separate "prod" target) is enough once `.env`'s `CONFIG_FILE` is
correct — the app reads `CONFIG_FILE` from `config/.env` regardless of which Make target launched
it.

**One more thing to check before trusting `configuration_prod.yaml` at face value**: as committed
in git, this file points `storage.postgres.host` / `storage.opensearch.host` /
`security.rebac.api_url` / `platform.control_plane_url` at plain `localhost` — the same
native-process-plus-dockerized-infra topology every other Fred repo uses (the pod runs natively via
`make run`; only the infra runs in `fred-deployment-factory`'s docker compose, reachable via
`localhost:<mapped-port>`). If you find these instead pointing at container hostnames
(`app-postgres`, `app-keycloak`, `openfga`, `host.docker.internal`), that reflects a **different,
uncommitted local edit** assuming the pod itself also runs inside Docker — confirm with the
developer which topology is actually in effect for this session before relying on either.

## Preconditions — infra is the developer's job, not yours

Keycloak, OpenFGA, Postgres, OpenSearch run via docker compose in `~/Fred/fred-deployment-factory`,
the same shared infra `fred`'s own skill uses. **Do not start, stop, or wipe this infra yourself**
— confirm with the developer that it's up before starting the pod. Known ports: Keycloak 8080,
OpenSearch 9200 (HTTPS + basic auth, see fred's own skill for the exact curl incantation), Postgres
5432, OpenFGA 9080.

## Starting the pod (and its MCP dependencies)

| App | Command | Port | Notes |
|---|---|---|---|
| `agents/` (`fred_samples_agents`) | `make run` (from `agents/`) | 8010 | Base path `/samples/agents/v1`. Registers 5 sample agents: `assistant`, `hello_graph`, `bank_transfer.graph`, `postal_tracking.graph`, `team_of_3.router`. |

Some sample agents call out to the demo MCP servers under `servers/mcp/python/` — start only the
ones relevant to the sample agent in scope, each from its own directory (`make run` or equivalent,
check each server's own README — they are plain standalone servers, not Fred pods, no
`configuration.yaml`/`.env` split to worry about):

| MCP server | Port | Used by |
|---|---|---|
| `bank_core_mcp_server` | 9801 | `bank_transfer` sample |
| `risk_guard_mcp_server` | 9802 | `bank_transfer` sample |
| `postal-service-mcp-server` | 9797 | `postal_tracking` sample |
| `iot-tracking-mcp-server` | 9798 | `postal_tracking` sample |
| `minimal-mcp-server` | (template, no fixed port) | reference only |

Launch whichever subset is in scope in parallel — independent Bash calls in one message, each
`run_in_background: true`. Ask the developer which sample agent (and therefore which MCP servers,
if any) is in scope before starting things speculatively.

There is **no frontend** in this repo — the developer drives the pod via `make chat`
(`fred-agents-cli`, an interactive terminal client) or a direct API call, not a browser.

`make run` installs deps first if needed — the first launch after a clean checkout will be slower;
don't mistake that for a hang.

## Watching, don't polling

Use the **Monitor** tool against the pod's (and any MCP server's) background shell to stream
stdout live — every line becomes a notification — rather than periodically re-reading a log file.
This lets you correlate "developer just sent X in `make chat`" with the log line it produced in
near real time. Stop every Monitor you started once the developer is done or the session's
question is answered.

## Metrics

`agents/config/configuration_prod.yaml` explicitly sets its Prometheus exporter to **port 9010**
(`app.port` 8010 + 1000, the same convention `fred`'s and `fred-rags`'s pods use) —
`curl http://127.0.0.1:9010/metrics` returns real Prometheus exposition text. This is a deliberate
override: `fred_core`'s shared `KpiPrometheusSinkConfig` default is `enabled: True, port: 9000`,
which would collide with anything else on the box already using 9000 if a pod relied on the
default instead of overriding it — confirmed correctly overridden here, but if you ever see a pod
boot with `Prometheus metrics exporter ready at 127.0.0.1:9000`, that pod's config is missing its
own explicit port override.

## The three streams — what "checking observability" actually means

Same model as `fred`'s own skill (this pod depends on published `fred-core`/`fred-runtime`, which
ship the same `StoreEmitHandler`/`AUDIT_LOGGER_NAME` mechanism):

1. **stdout** — the pod's console handler; also where the audit logger writes exclusively, as
   structured JSON, `propagate=False`. Audit records must appear here and **only** here.
2. **OpenSearch** (`curl -sk -u admin:<pass> https://localhost:9200/app-logs-index/_search`) — the
   generic durable app-log store, fed by the same root logger as stdout, when
   `observability.kpi.opensearch.enabled: true` (prod config only — standalone has it off).
3. **KPIs/metrics** — `127.0.0.1:9010/metrics` in prod config only (standalone has Prometheus
   explicitly disabled).

## The protocol

- The developer drives `make chat` or the API. You do not call a business endpoint or run a
  scenario against the live pod yourself. If a specific action would help diagnose something,
  propose it and let the developer perform it, or ask before running it yourself.
- When the developer reports something, diagnose from the logs **first** — don't guess at a root
  cause before reading what actually happened.
- Report every finding with all four of: **reproduction**, **extract** (quote the actual log
  line, don't paraphrase), **channel** (stdout / OpenSearch / KPIs / audit), and
  **classification** (bug, config gap, expected-but-underdocumented behavior, or false alarm).
- Fix the root cause, not a patch over the symptom, per this repo's own `CLAUDE.md` (didactic
  sample governance — keep changes minimal and don't blur the "this is a teaching reference"
  scope of this repo while fixing a real bug).

## Ending the session

Stop whichever background processes and Monitors this session started when the developer is done.
Don't leave them running silently across an unrelated task.

## Before starting anything — check for stale processes

    ss -ltnp | grep -E ':8010|:9010|:9801|:9802|:9797|:9798'

If a port is already held, find the owning PID and check whether its parent is a **still-running**
process before touching it. Only kill processes confirmed stale.
