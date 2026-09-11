---
name: live-knowledge-base-session
description: Start the fred-samples Knowledge Base pod against the real local stack — publish its declaration, then serve runs — and watch both its stdout and the Control Plane's live while the developer drives the Platform Admin UI by hand. Use for manual observability on the Knowledge Base contract, or to check "does publication / enablement / worker registration actually behave and log correctly".
user-invocable: true
argument-hint: "[optional: which Knowledge Base sample is in scope — today only knowledge-bases/local-folder exists]"
---

# Live Knowledge Base Session (fred-samples)

A **collaborative** working mode, not an automated test run: the developer runs the commands and
drives the Platform Admin UI; you start what you are asked to start, tail stdout, and report what
you see. You never publish, enable, or mutate anything yourself — see "The protocol".

Sibling of `.claude/skills/live-observability-session/SKILL.md` in this repo (the agent pod's
version, same protocol and same three-stream vocabulary). Two things genuinely differ, and
conflating them wastes a session:

- **A Knowledge Base session is two-sided.** The pod publishes and polls; the Control Plane
  receives and stores. Half the evidence is in `~/Fred/fred/apps/control-plane-backend`'s stdout,
  not the pod's. Tail both or you will be guessing.
- **The pod has no inbound port.** It is a Temporal worker, not an HTTP server. There is nothing
  to `curl`, no `/metrics`, no base path. Absence of a port is the design, not a misconfiguration.

## The one rule this skill exists to enforce: the pod's environment, verified before starting

The agent pod's equivalent rule is "always `configuration_prod.yaml`". **A Knowledge Base pod has
no YAML configuration at all** — `fred_sdk.knowledge_base` reads the process environment and
nothing else. Do not go looking for a config profile, and do not invent one.

Its equivalent is `knowledge-bases/<sample>/config/.env`, copied from the committed
`config/env.template`, and it must point at the security-on `fred-deployment-factory` stack:

    FRED_CONTROL_PLANE_URL="http://localhost:8222/control-plane/v1"   # the API prefix is required
    FRED_KEYCLOAK_REALM_URL="http://localhost:8080/realms/app"
    FRED_KB_CLIENT_ID="knowledge-base-local-folder"
    FRED_KB_CLIENT_SECRET="..."
    FRED_TEMPORAL_HOST="localhost:7233"                                # `run` only

Before starting anything, read that file and confirm all five. If `.env` is missing, **say so and
have the developer copy the template** — it is gitignored and per-session, but it carries a
secret, so it is not yours to fabricate. Two failure modes to recognise instantly rather than
debug:

- **`FRED_CONTROL_PLANE_URL` without `/control-plane/v1`** → every call 404s against a live,
  healthy Control Plane.
- **No `FRED_KB_CLIENT_SECRET`** → the SDK logs `No client secret set: calling Fred
  unauthenticated` and proceeds. Against a security-on Control Plane every call is then rejected.
  That warning line is the answer; read it before theorising.

The Makefile exports this file for you (`make publish`, `make run`). Running
`python -m <package> publish` by hand in a shell that has not sourced it will fail on missing
environment, and that is not a bug.

## The trap that cannot be undone: first publication binds the identity

Fred binds a definition to the client that publishes it first, and **there is no rebinding path in
the code**. A session that publishes `local-folder` from a convenient existing client — the
evaluation worker, `agentic`, anything — permanently burns that definition id, and recovering it
means deleting the row in Postgres by hand.

So: never suggest "just try it with another client to see". If an identity experiment is genuinely
needed, use a throwaway definition id, and tell the developer that is what you are doing and why.

## Preconditions — infra is the developer's job, not yours

Keycloak, Postgres, OpenFGA and Temporal run via docker compose in `~/Fred/fred-deployment-factory`.
**Do not start, stop, or wipe this infra yourself.** Confirm with the developer that it is up.
Known ports: Keycloak 8080, Control Plane 8222 (run natively), Temporal 7233, Postgres 5432,
OpenFGA 9080.

One precondition is specific to this skill: the pod's confidential Keycloak client must exist, with
the `app:service_agent` client role and no realm-management role. It is provisioned by
`make keycloak-post-install` in `fred-deployment-factory`, which is idempotent and does not require
tearing the stack down. A 401 on `/realms/app/protocol/openid-connect/token` means that step has
not run — not that the pod is misconfigured.

## What a session actually covers today

Publication and enablement are built end to end. Execution is not: the Control Plane endpoints a
run uses to fetch its context and report its result do not exist yet, and neither do team
instances. **Nothing will ever be dispatched to the worker's queue.** Say this at the start of a
session rather than letting the developer wait for a run that cannot come.

| Step | Command | What to watch for |
|---|---|---|
| Publish | `make publish` (from the sample dir) | pod: `Published Knowledge Base <id> version <v>`; Control Plane: `[knowledge-base-publication] stored declaration for <id> version <v>` |
| Admin UI | developer, in the browser | the definition appears under `/admin/knowledge-bases`, with no online/health claim anywhere |
| Enable | developer, in the browser | enablement writes the ReBAC relation and stores **no** configuration |
| Serve | `make run` | pod: `Knowledge Base <id> serving runs on kb-<id>` — then silence, which is correct |

The queue name is derived (`kb-` + definition id), identically on both sides. A worker announcing a
queue that does not match the definition id is a contract break worth stopping the session for.

## Watching, not polling

Start what you are asked to start with `run_in_background: true`, then use the **Monitor** tool
against each background shell to stream stdout live, rather than re-reading a log file on a timer.
Tail the pod and the Control Plane together so a publish can be correlated across both sides in
near real time. Stop every Monitor you started once the developer is done.

## The streams — and the gap to report

The agent pod has three streams (stdout, OpenSearch app-logs, Prometheus KPIs). **A Knowledge Base
pod has one.** `fred_sdk.knowledge_base` sets up logging with a plain `logging.basicConfig` and
wires none of `fred_core`'s observability: no structured audit logger, no KPI sink, no OpenSearch
handler, no metrics exporter. Everything you will see is unstructured stdout.

Treat that as a finding to surface, not a limitation to work around: publication and enablement are
security-relevant events on a durable platform object, and today the pod side of them is invisible
to every durable stream. The Control Plane's own logging is unaffected — that half is normal.

## The protocol

- The developer runs `make publish`, `make run`, and drives the browser. You do not publish,
  enable, disable, or call any endpoint yourself. If an action would help diagnose something,
  propose it and let the developer perform it.
- Report anomalies, not reassurance. No "all normal" pings — surface spurious or missing log lines,
  because noise is what makes a real incident hard to find later.
- When logs are insufficient to answer the question, **propose the exact log or KPI line to add**
  and gate it on a security/noise review together. Do not guess a root cause from silence.
- Report every finding with all four of: **reproduction**, **extract** (quote the real log line,
  never paraphrase), **side** (pod / Control Plane / Keycloak / Temporal), and **classification**
  (bug, config gap, expected-but-underdocumented, or false alarm).
- Fix root causes, not symptoms, and keep changes minimal per this repo's `CLAUDE.md` — this is a
  didactic sample, not a second Fred core.

## Before starting anything — check for a stale worker

    ps -ef | grep -F 'fred_samples_local_folder_kb run' | grep -v grep
    ss -ltnp | grep -E ':8222|:7233'

A forgotten `make run` from an earlier session keeps polling the same derived queue. Two workers on
one queue means work lands in whichever process wins the race, and the session's logs will look
inexplicably empty. Only kill processes confirmed stale.

## Ending the session

Stop whichever background processes and Monitors this session started. Do not leave a worker
polling silently across an unrelated task.
