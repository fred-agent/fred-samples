# Fred Samples

Ready-to-run examples for the [Fred](https://site.fredlab.dev) agentic platform.

Three kinds of samples, each self-contained:

- **Agents** — one Python agent pod you start with `make run` and talk to with
  `make cli`, paired with MCP servers when a workflow depends on them.
- **Applications** — your own UI and API, rendered in Fred and reachable by its agents.
- **Knowledge Bases** — pods that keep a team's library in sync with an external
  source (a folder, a Git branch, a WebDAV share).

> **Documentation** → [site.fredlab.dev](https://site.fredlab.dev)

---

## What's in this repository

```
fred-samples/
├── agents/                         Agent pod — all sample agents in one service
├── apps/
│   ├── document-triage/            Sample application — only a person writes the record
│   ├── progress-tracker/           Sample application — UI + API + MCP tools
│   └── review-board/               Sample application — UI + API + MCP tools, driven by agents
├── knowledge-bases/                Sample Knowledge Bases — see its README.md
│   ├── local-folder/               synchronize Markdown from a folder
│   ├── git-repository/             synchronize a GitHub or GitLab branch
│   └── webdav/                     synchronize a WebDAV share
└── servers/
    └── mcp/
        └── python/
            ├── bank_core_mcp_server/       Simulated bank ledger (port 9801)
            ├── risk_guard_mcp_server/      Simulated risk & KYC engine (port 9802)
            ├── postal-service-mcp-server/  Simulated postal core (port 9797)
            ├── iot-tracking-mcp-server/    Simulated IoT tracking (port 9798)
            └── minimal-mcp-server/         Bare-minimum MCP server template
```

---

## Samples

### General Assistant

A plain conversational agent with no tool dependencies. Good for verifying the
pod and model key work before starting any MCP server.

```
Agent ID: assistant
Requires: nothing — just a model API key
```

---

### Hello Graph — minimal graph agent

The smallest possible v2 graph agent: one classify step, one answer step, one
finalize step. No MCP server, no HITL gate. Read this before Bank Transfer or
Postal Tracking to see the shape of a graph agent with nothing else in the way.

```
Agent ID: fred.samples.hello_graph
Requires: nothing — just a model API key
```

**Workflow:**
1. `classify` reads your message and decides "greeting" or "question".
2. `greet` or `answer` produces one model reply on the matching branch.
3. `finalize` returns it.

**Try it:**
```
Hi!
What's the capital of France?
```

---

### Bank Transfer — HITL demo

A workflow agent that executes a fund transfer through two mandatory human
confirmation gates.

```
Agent ID: fred.samples.bank_transfer.graph
Requires: bank_core_mcp_server (port 9801) + risk_guard_mcp_server (port 9802)
```

**Workflow:**
1. The agent extracts source account, destination, and amount from your message.
2. It loads the source account and checks KYC compliance.
3. If risk is elevated it pauses and asks you whether to proceed (HITL gate 1).
4. It creates a pending transaction and shows you the details (HITL gate 2).
5. Only after your final confirmation does money move.

**Try it:**
```
Transfer 500 EUR from ACC-001 to ACC-002
Transfer 3000 EUR from ACC-001 to EXT-WIRE-99   ← triggers risk warning
```

**Mock accounts:** `ACC-001` (5 000 EUR) · `ACC-002` (150 EUR)  
External destinations: any `EXT-` prefixed ID.

---

### Postal Tracking — map + HITL reroute demo

A workflow agent that tracks parcels, renders a live map of the route and pickup
points, and optionally reroutes a parcel to a relay point after user confirmation.

```
Agent ID: fred.samples.postal_tracking.graph
Requires: postal-service-mcp-server (port 9797) + iot-tracking-mcp-server (port 9798)
```

**Try it:**
```
Seed a demo parcel
Where is my parcel?
Show me the map
Reroute it to a pickup point
```

---

### Team of 3 — TeamAgent route demo

A team-based sample that proves delegation and routing across three child agents
(1 Graph + 2 ReAct) behind one router agent.

**Sample docs:** [README_AGENT.md](agents/fred_samples_agents/team_of_3_agents_sample/README_AGENT.md) · [README_CLI.md](agents/fred_samples_agents/team_of_3_agents_sample/README_CLI.md)

```
Agent ID: fred.samples.team_of_3.router
Requires: no MCP server (pod-local child delegation only)
```

**Try it:**
```
Please approve this expense request for 120 EUR.
Convert 2.5 km to meters and add 120.
Rewrite this sentence in plain English: The rollout was postponed due to environmental contingencies.
```

---

### Document Review — application-driven agents

Four templates that run the Review Board application's workflow: a coordinator
calls a document analyst, a risk reviewer and an action planner in order, then
writes a final summary. Each reads the source document and the earlier stages'
saved results through the application's MCP server, under the signed-in user's
bearer.

**Sample docs:** [README.md](agents/fred_samples_agents/document_review/README.md)

```
Agent IDs: fred.samples.document_review.coordinator
           fred.samples.document_review.analyst
           fred.samples.document_review.risk_reviewer
           fred.samples.document_review.action_planner
Requires:  the review-board application deployed and granted to the team
```

Only the coordinator needs a managed instance; it invokes the three specialists
from this pod's own registry. Start from
[apps/review-board/README.md](apps/review-board/README.md).

---

## Applications

Agents are not the only thing you can add to Fred. An **application** is your own
UI and service, rendered in Fred and reachable by its agents.

There are three, in `apps/`. They solve the same shape of problem and differ in
one decision — **who writes the durable record** — which is what decides whether
the application needs a datastore of its own.

| | `apps/document-triage/` | `apps/progress-tracker/` | `apps/review-board/` |
|---|---|---|---|
| Who writes | only a person, under their own bearer | a person and agents | agents, stage by stage |
| Storage | the team's Knowledge Flow workspace | SQLite owned by the app | OpenSearch owned by the app |
| Agent path | none — agents read through Fred's platform capabilities | MCP at `/mcp` | MCP at `/mcp` |
| Start here | ✅ | when agents must write the record | when a multi-stage workflow drives it |

All three are first-party backends. Each validates the caller's bearer against
Keycloak and reads the team's application grant from Fred's OpenFGA, so each
needs its own Keycloak M2M client secret and its own OpenFGA API token in a
Secret of its own namespace. None of them holds a shared agent key: an agent
that calls an application's MCP server carries the signed-in user's bearer, and
every check the UI goes through runs again unchanged.

**Deployment guide for all three:** [apps/DEPLOYMENT.md](apps/DEPLOYMENT.md)

---

### Document Triage — sample application, no storage of its own

A team reviews the documents in one of its folders, marking each **reviewed** or
**needs work**. Agents read the corpus and propose triage in chat; only a person
records a decision.

```
Folder:   apps/document-triage/          (the folder name is also the app_id)
Pieces:   ui/ (static page) · api/ (stateless FastAPI)
Requires: a Fred deployment with Knowledge Flow, plus Keycloak and OpenFGA
```

**Sample docs:** [README.md](apps/document-triage/README.md) ·
[DEPLOYMENT.md](apps/DEPLOYMENT.md)

It ships no Python capability package and mounts no MCP server, so nothing has
to be installed into the agents image for it. Agents reach the corpus and the
team workspace through Fred's own platform capabilities: they may read
team-shared files but may only mutate inside their own subtree, so they can
propose triage in chat and cannot record it. The person commits every record,
under their own bearer.

---

### Progress Tracker — sample application

The user records a long-running task in a small web UI, then advances it by
talking to agents; the application keeps the record — tasks, decisions, and the
conversations that touched them — so work survives across days and sessions.

```
Folder:   apps/progress-tracker/         (the folder name is also the app_id)
Pieces:   ui/ (static page) · api/ (FastAPI + SQLite + MCP at /mcp)
Requires: a Fred deployment to render the UI; Keycloak and OpenFGA to start
```

**Sample docs:** [README.md](apps/progress-tracker/README.md) ·
[DEPLOYMENT.md](apps/DEPLOYMENT.md)

One service, two entry points: the same FastAPI app serves the iframe's REST
routes and a streamable HTTP MCP endpoint. Six tagged routes become agent tools,
four of which write; those four additionally require the team to hold the
`mcp-progress-tracker` capability, so entitlement to open the page is not
entitlement for an agent to write the record. The API's own README has the
environment it needs to run outside a cluster.

---

### Review Board — sample application, staged agent workflow

A person creates a review task against a corpus document; the Document Review
Coordinator then drives an analyst, a risk reviewer and an action planner
through it, each saving a structured result. The dashboard renders those stored
decisions and timings.

```
Folder:   apps/review-board/             (the folder name is also the app_id)
Pieces:   ui/ (static page) · api/ (FastAPI + OpenSearch + MCP at /mcp) · agents/ (deploy manifest)
Requires: a Fred deployment, Keycloak, OpenFGA, OpenSearch, and a configured model
```

**Sample docs:** [README.md](apps/review-board/README.md) ·
[DEPLOYMENT.md](apps/DEPLOYMENT.md)

Its agent templates live in the sample agents pod, not in the application:
`agents/fred_samples_agents/document_review/`. OpenSearch credentials stay
inside the API pod; neither the browser nor an agent receives them.

---

No application ships a pip-installable capability package or a
`fred.capabilities` entry point, so the agent pod installs nothing per
application. Agents reach an application only over its MCP endpoint, with the
signed-in user's bearer.

---

## Knowledge Bases

A **Knowledge Base** tells Fred where a team's documents come from and how to
keep them up to date. You declare one with `fred_sdk.knowledge_base`: an
identity, the configuration an operator fills in, and one async handler that
reconciles the source and reports what changed.

**Start here:** [knowledge-bases/README.md](knowledge-bases/README.md) — the
three samples, how to run one by hand, and how to drive one with the skills.

### Local Folder — sample Knowledge Base

The smallest believable implementation: it synchronizes Markdown files from a
folder on your own machine, keeps its own ledger of what it already published,
and reports honest `created` / `updated` / `removed` / `unchanged` counters.

```
Folder:   knowledge-bases/local-folder/
Requires: nothing — no model, no MCP server, no cluster
```

**Sample docs:** [README.md](knowledge-bases/local-folder/README.md)

The image is the whole integration: the SDK gives it two commands, `publish`
(declare this Knowledge Base to Fred, a deployment step) and `run` (serve runs).
An author writes no plumbing.

```bash
cd knowledge-bases/local-folder
make declaration                   # what `publish` would send to Fred
make sync ROOT=/path/to/your/notes # one run, with no Fred and no Temporal
```

A run writes documents through Knowledge Flow's REST API once the pod's
configuration names a `knowledge_flow_url`; without one, it logs what it
*would* publish, which is what `make sync` relies on.

### WebDAV share — sample Knowledge Base

Synchronizes a folder published over WebDAV — an Apache `mod_dav` share is the
case it is written and tested against. Every run re-lists the whole tree, so
unlike a source that only reports its own changes, this one can tell that a
file is really gone and retract its document.

```
Folder:   knowledge-bases/webdav/
Requires: a reachable WebDAV share — no model, no MCP server, no cluster
```

**Sample docs:** [README.md](knowledge-bases/webdav/README.md)

**Kubernetes deployment:**
[`charts/knowledge-base/`](charts/knowledge-base/) — published as
an OCI Helm chart on GHCR for deployment repositories to consume with their
own environment-specific values.

```bash
cd knowledge-bases/webdav
make share-run                            # a real Apache mod_dav share, in a container
make sync URL=http://localhost:8088/dav/  # one run against it, with no Fred
```

The `webdav-share` skill in `.claude/skills/` drives that from a sentence —
serve any folder, then synchronize it into a real library.

Two things it is worth reading that README for: why the walk is `Depth: 1` and
never `Depth: infinity`, and why a corporate `https://` share fails in Python
while `curl` against the same URL succeeds.

---

## Quick start

### 1. Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.12 |
| An OpenAI API key | — |

### 2. Configure the agents pod

```bash
cp agents/config/env.template agents/config/.env
# edit agents/config/.env and set your OPENAI_API_KEY
```

### 3. Start the MCP servers you need

Each MCP server is independent. Start only the ones your chosen sample requires.

```bash
# Bank Transfer sample
cd servers/mcp/python/bank_core_mcp_server  && make run   # port 9801
cd servers/mcp/python/risk_guard_mcp_server && make run   # port 9802

# Postal Tracking sample
cd servers/mcp/python/postal-service-mcp-server && make run  # port 9797
cd servers/mcp/python/iot-tracking-mcp-server   && make run  # port 9798
```

### 4. Start the agents pod

```bash
cd agents
make run     # installs deps, starts pod on port 8010
```

### 5. Chat

In a second terminal, from the `agents/` directory:

```bash
make cli
```

You will see:

```
[chat] pod url   : http://127.0.0.1:8010/samples/agents/v1
[chat] auth      : none (security.user not configured)
Connected to http://127.0.0.1:8010/samples/agents/v1
Current agent: assistant
```

Switch to a sample agent:

```
/agent fred.samples.hello_graph
/agent fred.samples.bank_transfer.graph
/agent fred.samples.postal_tracking.graph
/agent fred.samples.team_of_3.router
```

List all available agents:

```
/agents
```

---

## Validate the repository

The agent pod and the three Knowledge Bases each own a venv, quality baselines
and a Makefile; the root Makefile fans out to those four:

```bash
make test           # each package's offline test suite
make code-quality   # ruff, bandit, detect-secrets, basedpyright
```

Both are offline: no model key, no MCP server, no cluster.

The applications under `apps/` have no Makefile and are not in that fan-out.
Each carries its own suite, run against its API requirements from the
repository root:

```bash
python -m pytest apps/review-board/tests
```

---

## MCP servers at a glance

| Server | Port | Tools |
|--------|------|-------|
| `bank_core_mcp_server` | 9801 | `get_account_details`, `prepare_transfer`, `commit_transfer` |
| `risk_guard_mcp_server` | 9802 | `check_kyc_compliance`, `evaluate_transfer_risk` |
| `postal-service-mcp-server` | 9797 | `track_package`, `get_pickup_points_nearby`, `reroute_package_to_pickup_point`, `notify_customer` |
| `iot-tracking-mcp-server` | 9798 | `get_live_tracking_snapshot`, `get_route_geometry`, `seed_demo_tracking_incident` |
| `minimal-mcp-server` | — | Template — one echo tool, no business logic |

All MCP servers use the [Streamable HTTP](https://modelcontextprotocol.io/specification) transport at `/mcp`.

---

## Docker

For container build/run/push workflows, see:

- `dockerfiles/README.md`

Images are published to `ghcr.io/fred-agent/fred-samples/` by CI. A release is
a git tag `code/v1.2.3`, which publishes image tag `v1.2.3` — see
"Publishing images" in `dockerfiles/README.md`.

---

## Learn more

- Platform documentation: [site.fredlab.dev](https://site.fredlab.dev)
- How to build your own agent from scratch: [fredlab.dev/docs/guides/how-to-use-fred](https://site.fredlab.dev/docs/guides/how-to-use-fred/)
- Fred on GitHub: [github.com/ThalesGroup/fred](https://github.com/ThalesGroup/fred)
