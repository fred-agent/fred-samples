---
name: live-knowledge-base-session
description: Start the fred-samples Knowledge Base pod against the real local stack — publish its declaration, then serve runs — and watch both its stdout and the Control Plane's live while the developer drives the UI by hand. Use for manual observability on the Knowledge Base contract, or to check "does publication / enablement / instance creation / scheduled run actually behave and log correctly".
user-invocable: true
argument-hint: "[optional: which Knowledge Base sample is in scope — knowledge-bases/local-folder, knowledge-bases/git-repository or knowledge-bases/webdav]"
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

## The one rule this skill exists to enforce: the pod's configuration, verified before starting

The agent pod's rule is "always `configuration_prod.yaml`", and a Knowledge Base pod now follows
the same mechanism rather than one of its own: **one `configuration.yaml` resolved from
`$CONFIG_FILE`, with the environment carrying secrets only.** A pod used to read the process
environment and nothing else; it no longer does, so a session that goes looking for
`FRED_CONTROL_PLANE_URL` or `FRED_KB_PREFIX` is reading a surface that was deleted.

Two files per sample, and both matter:

`knowledge-bases/<sample>/config/configuration.yaml` — committed, and already pointing at the
security-on `fred-deployment-factory` stack:

    knowledge_base:
      prefix: "fred.samples"                                    # the prefix owned, not a base
      control_plane_url: "http://localhost:8222/control-plane/v1"   # the API prefix is required
      knowledge_flow_url: "http://localhost:8111/knowledge-flow/v1" # only if the pod ingests
    security:
      m2m:
        enabled: true
        realm_url: "http://localhost:8080/realms/app"
        client_id: "kb-fred.samples"
        secret_env_var: "FRED_KB_CLIENT_SECRET"
    scheduler:
      temporal:
        host: "localhost:7233"

`knowledge-bases/<sample>/config/.env` — gitignored, copied from the committed `config/env.template`,
and carrying `CONFIG_FILE` plus the secrets. If it is missing, **say so and have the developer copy
the template**: it carries a secret, so it is not yours to fabricate.

Read both before starting anything. Three failure modes to recognise instantly rather than debug:

- **`control_plane_url` without `/control-plane/v1`** → every call 404s against a live, healthy
  Control Plane.
- **No secret in the variable `security.m2m.secret_env_var` names** → `RuntimeError: Missing
  Keycloak client secret in env: <VAR>`, raised before the first token call. The pod no longer
  degrades to unauthenticated calls and then collects 401s: it fails fast, and that line is the
  whole answer. Read it before theorising.
- **`$CONFIG_FILE` unset or pointing elsewhere** → `No Knowledge Base configuration: ... Set
  $CONFIG_FILE, or put one at ./config/configuration.yaml.` The default is resolved relative to
  the working directory, so a stale `.env` sends the pod at a file the developer is not reading.

`security.user` and `scheduler.temporal.task_queue` are absent on purpose, not missing: the pod
serves no user and opens no inbound port, and a run's queue is derived on both sides. Do not add
either.

The Makefile exports `.env` for you (`make publish`, `make run`). Running
`python -m <package> publish` by hand in a shell that has not sourced it will fail on missing
configuration, and that is not a bug.

## The trap that cannot be undone: first publication binds the namespace

A Knowledge Base is named under a prefix its contributor owns — `fred.samples.local-folder` — and
Fred claims that **prefix** for the client that publishes under it first. There is no rebinding path
in the code.

So a session that publishes from a convenient existing client — the evaluation worker, `agentic`,
anything — does not burn one definition: it burns the **whole namespace**, and every Knowledge Base
the image will ever publish under it. Recovering means deleting rows in Postgres by hand.

Never suggest "just try it with another client to see". If an identity experiment is genuinely
needed, use a throwaway prefix, and tell the developer that is what you are doing and why.

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

**Which Fred processes a run actually needs**, since this trips people up: the Control Plane API
(it creates the instance and registers the schedule) and the pod itself (`make run` — its worker
runs the synchronization workflow). The control-plane Temporal worker is **not** in this path; it
registers `LifecycleManagerWorkflow` on its own queue and knows nothing of
`FredKnowledgeBaseSynchronize`. Do not tell the developer to start it to make a run happen.

Fred's own backends run natively from `~/Fred/fred`, and this developer runs them with the
production profile against the real Postgres — never SQLite. Confirm which profile is active
rather than assuming; a Control Plane on one database and a worker on another is a failure mode
that looks like "the instance disappeared".

## What a session actually covers today

Publication, enablement **and execution** are built end to end: a team creates an instance, the
Control Plane registers a Temporal schedule for it, and runs are dispatched to the pod's queue on
that schedule. A session can therefore wait for a run and expect one to arrive.

| Step | Command | What to watch for |
|---|---|---|
| Publish | `make publish` (from the sample dir) | pod: `Published Knowledge Base <id> version <v>`; Control Plane: `[knowledge-base-publication] stored declaration for <id> version <v>` |
| Admin UI | developer, in the browser | the definition appears under `/admin/knowledge-bases`, with no online/health claim anywhere |
| Enable | developer, in the browser | enablement writes the ReBAC relation and stores **no** configuration |
| Create an instance | developer, from the team's Knowledge Bases screen | four effects, none of them a log line: the knowledge-flow library, the instance row, the pod's `editor` grant over that one library, and a Temporal schedule `control-plane-kb-<instance_id>`. Read them in the Temporal UI (8233) and the database, not in stdout |
| Serve | `make run` | pod: `Knowledge Base <id> serving runs on kb__<id>` — then silence until the schedule fires |
| A run | the schedule firing, or the developer triggering it from the Temporal UI — **Fred has no trigger button** | workflow `FredKnowledgeBaseSynchronize` on queue `kb__<definition id>`; then the **sample's own** lines, e.g. `published <path>` / `retracted <path>` and a closing summary |

The queue name is derived — `kb__` (two underscores) + definition id — identically on both sides,
from `knowledge_base_catalog_id` in `fred_core`. A worker announcing a queue that does not match
the definition id is a contract break worth stopping the session for.

**The SDK logs nothing per run.** Everything you see once a run starts comes from the sample's own
handler. Silence on the pod during a run that Temporal shows as completed is the observability gap
below, not a failed run — check the workflow's status in the Temporal UI before calling it broken.

A schedule outlives the instance that created it. If the developer deleted an instance by hand, or
a creation failed part-way (`[knowledge-base] could not undo ... it may need clearing by hand`), an
orphaned schedule keeps firing against a queue nobody serves. Report it; do not delete it yourself.

The queue carries the definition and **not** the provider, which is safe only because the prefix
claim above makes a definition id unforgeable: no second client can publish under a prefix another
one owns, so two providers cannot end up sharing a queue. If that claim is ever relaxed, this
derivation becomes ambiguous — worth remembering, not worth watching for today.

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

    ps -ef | grep -E 'fred_samples_(local_folder|git|webdav)_kb run' | grep -v grep
    ss -ltnp | grep -E ':8222|:7233'

A forgotten `make run` from an earlier session keeps polling the same derived queue. Two workers on
one queue means work lands in whichever process wins the race, and the session's logs will look
inexplicably empty. Only kill processes confirmed stale.

The WebDAV sample also brings up an Apache container to serve its test share
(`.claude/skills/webdav-share`). Check for it too, and remember it is the sample's, not the
developer's infra: `docker ps --filter name=fred-samples-webdav-share`.

## Ending the session

Stop whichever background processes and Monitors this session started. Do not leave a worker
polling silently across an unrelated task.
