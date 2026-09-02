# Fred Samples

Ready-to-run examples for the [Fred](https://fredk8.dev) agentic platform.

Each sample is self-contained: a Python agent pod that you start with `make run` and
talk to with `make chat`, paired with MCP servers when its workflow depends on them.

> **Documentation** → [fredk8.dev](https://fredk8.dev)

---

## What's in this repository

```
fred-samples/
├── agents/                         Agent pod — all sample agents in one service
├── apps/
│   ├── document-triage/            Sample application — no secrets, agents read only
│   └── progress-tracker/           Sample application — UI + API + agent capability
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

## Applications

Agents are not the only thing you can add to Fred. An **application** is your own
UI and service, rendered in Fred and reachable by its agents.

There are two, in `apps/`. They solve the same shape of problem and differ in
exactly one decision — **who writes the durable record** — which is what decides
whether the application needs a datastore and a secret of its own.

| | `apps/document-triage/` | `apps/progress-tracker/` |
|---|---|---|
| Who writes | the human, under their own bearer | agents, through the app's API |
| Storage | the team's Knowledge Flow workspace | SQLite owned by the app |
| Secrets | **none** | a shared service key |
| Start here | ✅ | only if you need agent writes |

**Deployment guide for both:** [apps/DEPLOYMENT.md](apps/DEPLOYMENT.md)

---

### Document Triage — sample application, no secrets

A team reviews the documents in one of its folders, marking each **reviewed** or
**needs work**. Agents read the corpus and propose triage in chat; only a person
records a decision.

```
Folder:   apps/document-triage/          (the folder name is also the app_id)
Pieces:   ui/ (static page) · api/ (stateless FastAPI) · capability/ (read-only tools)
Requires: a Fred deployment with Knowledge Flow
```

**Sample docs:** [README.md](apps/document-triage/README.md) ·
[DEPLOYMENT.md](apps/DEPLOYMENT.md)

Its capability is read-only and holds no credential: it reads
`document_folders`, `document_summarize` and `workspace_fs`, whose adapters keep
the runtime's token private. Agents may read team-shared files but may only
mutate inside their own subtree — *agents never share* — so the human commits
every record. That constraint is why this sample needs no secret at all, and it
is explained in
[Why there is no service key](apps/document-triage/README.md#why-there-is-no-service-key).

---

### Progress Tracker — sample application

The user records a long-running task in a small web UI, then advances it by
talking to agents; the application keeps the record — tasks, decisions, and the
conversations that touched them — so work survives across days and sessions.

```
Folder:   apps/progress-tracker/         (the folder name is also the app_id)
Pieces:   ui/ (static page) · api/ (FastAPI + SQLite) · capability/ (agent tools)
Requires: a Fred deployment to render the UI — but the API runs standalone
```

**Sample docs:** [README.md](apps/progress-tracker/README.md) ·
[DEPLOYMENT.md](apps/DEPLOYMENT.md)

Because its agents write shared state — which Fred does not support directly —
it keeps its own database and reaches it with a shared key Fred neither issues
nor validates. The README is explicit about what that costs; read it before
copying the pattern.

**Try the API on its own** (no cluster needed):

```bash
cd apps/progress-tracker/api
uv venv && uv pip install -r requirements.txt
PROGRESS_TRACKER_SERVICE_KEY=dev-key .venv/bin/uvicorn app:app --port 8000
```

---

Both capabilities are already wired into the agent pod above: `agents/` depends
on each package by path, so `make dev` installs them. Each stays inert —
contributing no tools — until its application is reachable.

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
make chat
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

---

## Learn more

- Platform documentation: [fredk8.dev](https://fredk8.dev)
- How to build your own agent from scratch: [fredk8.dev/docs/guides/how-to-use-fred](https://fredk8.dev/docs/guides/how-to-use-fred/)
- Fred on GitHub: [github.com/ThalesGroup/fred](https://github.com/ThalesGroup/fred)
