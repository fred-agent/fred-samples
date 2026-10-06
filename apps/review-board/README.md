# Review Board — corpus review with specialist agents

Choose a corpus document, create a review task, then ask the Document Review
Coordinator in Fred chat to review that task. It calls a document analyst,
risk reviewer and action planner in order, then writes a final summary.
The dashboard reads their saved decisions and execution progress from
application-owned OpenSearch through the same backend that the agents use.

Fred forwards the signed-in user's bearer to this MCP server: there is no
application/agent shared key and no custom Python `AgentCapability` package.
The sibling `progress-tracker` sample authenticates the same way. What this one
adds is an ordered multi-agent workflow whose every decision the application
persists, and its own agent runtime deployed beside it.

> **This is a first-party application backend.** It validates every caller's
> bearer, checks the team and `app:review-board` grant through its own
> process-lifetime `RebacSdk`, and keeps OpenSearch credentials inside the API
> pod. The browser and agents never receive those credentials.

---

## Try the document-review flow

1. Complete the deployment/admission setup below and enroll the coordinator
   template in your collaborative team. Give it the `mcp-review-board`
   capability. It also needs a real configured model; mock success is never
   substituted when a model is missing.
2. Open Review Board in Fred, select a team corpus folder and processed file,
   and create a task. The API verifies the canonical document and its folder;
   a browser-supplied filename does not determine what gets analyzed.
3. Copy the task request shown by the page. Open Fred chat, select **Document
   Review Coordinator**, and send `Review task-<opaque-id>`. The frame can open
   Fred chat but cannot select an arbitrary agent or inject a prompt itself.
4. Keep the application page open to see the analyst, risk reviewer, action
   planner and summary stages complete. Each stage reads the source and earlier
   persisted decisions through MCP, then saves a structured result to the API.
5. Inspect decisions, short rationales, evidence, findings, recommended actions,
   timings and the final outcome. The page's charts use stored data, not mock
   values or time-driven progress estimates.

The four templates are registered in the existing samples runtime:

```text
fred.samples.document_review.coordinator
fred.samples.document_review.analyst
fred.samples.document_review.risk_reviewer
fred.samples.document_review.action_planner
```

They are served by the sample agents runtime in `agents/`, deployed beside this
application by `agents/deploy.yaml` in this folder. It must run as its own pod,
be listed in the Control Plane's `runtime_catalog_sources`, and have its
templates enabled for the team before Create Agent offers them.

Only the coordinator needs a managed instance for the combined workflow. The
experts are real runtime agents invoked from the same pod's registry, not inline
role-play model calls. Each declares the MCP server itself; child agents do not
inherit a different managed instance's tuning or the parent's tool selections.
See the [agent implementation guide](../../agents/fred_samples_agents/document_review/README.md).

```text
App UI ── Fred host ── app REST ────────────── OpenSearch
                           │                     ▲
                           └─ Knowledge Flow      │
                                                 │
Fred chat ── coordinator ── specialist agents ── app MCP
                           (sequential)            │
                                                  └─ Knowledge Flow
```

Both Knowledge Flow paths forward the authenticated user's bearer. The app
never reads Fred's internal document indexes directly. OpenSearch holds only
the app's own tasks, runs, stage decisions and progress.

## What the graphs measure

- Overall progress counts completed stages out of all four expected stages.
  Queued stages remain in the denominator. Running model calls are indeterminate.
- Specialist durations come from persisted start/end timestamps.
- Findings by severity count the risk review, avoiding double-counting when a
  later summary repeats a finding.
- Task states, outcomes and completion charts are derived from accessible saved
  tasks/latest runs. The backend explicitly labels the bounded statistics scope;
  it is not an all-time, all-team analytics service.
- Decisions are agent-generated assessments with evidence and limitations, not
  proof of factual correctness or professional certification.

## Persistence and failure behavior

The task index stores document references, bounded run history, stages, results
and events as one canonical record. Optimistic sequence/primary-term checks
prevent concurrent claims and stale writers from replacing another run. A
completed stage cannot silently be overwritten or counted twice. Ten runs per
task is the sample's history bound; create another task when it is reached.

Existing generic tasks and reporter records remain readable. Startup adds the
new mapping fields to existing strict indexes; it never deletes or recreates an
index. Generic reporter writes cannot bypass the review workflow for linked tasks.

The API rechecks current corpus authorization for task details, lists, statistics
and mutations: saved decisions cannot bypass a source-document revocation.
The application does not persist raw document bodies or bearers in OpenSearch.
Fred's own chat and graph-checkpoint retention policies still apply while agents
process source content; clearing completed agent state is not deletion of older
runtime checkpoints. A source hash detects a changed document before continuing
a run. Processed markdown is bounded to 120 KB. CSV/tabular previews, unavailable
or oversized content are explicitly rejected, not silently treated as a complete
document review.

Interrupted runs retain their completed results. Start a fresh authenticated Fred
chat turn to resume; use an explicit restart to review again. Cancellation stops
future stage transitions and rejects stale writes, but does not promise to abort
an already running provider call. A lost process is shown as interrupted after
the run lease expires, rather than remaining falsely active forever.

## What it demonstrates

| Concern | How |
| --- | --- |
| One service, two entry points | the API exposes iframe REST routes and authenticated MCP at `/mcp` |
| The same human identity reaches both | the frame host and Fred MCP client forward the current user's bearer |
| No shared agent key | MCP uses `auth_mode: delegated`; there is no `X-Service-Key` path |
| No custom capability package | Fred projects the MCP catalog entry into its Capabilities UI dynamically |
| Team isolation | the backend verifies the bearer, then checks membership and the app grant before every team read or write |
| Durable progress | task and per-reporter state live in application-owned OpenSearch indexes |
| Many agents, one registration | register and grant one MCP server, then select it on each agent |

## The pieces

```text
ui/    static iframe page, served at /apps/review-board/
api/   FastAPI REST service + streamable HTTP MCP endpoint at /mcp
```

The frame wears Fred's own design tokens, so it looks like the product it is
embedded in rather than a bolted-on page:

```text
ui/package.json     pins @fred-oss/design-tokens and @fred-oss/iframe-sdk,
                    both installed at image build
ui/styles.css       this application's styling, tokens only — no literal colours
ui/frame-bridge.mjs the host protocol, spoken through the published client
```

An iframe is a separate document and inherits nothing from the host — not the
palette, not the type scale — so the tokens have to be shipped, not imported.
They come from Fred's published `@fred-oss/design-tokens` package: the image
build installs it and copies out the stylesheet, the face declarations and the
woff2 files. The palette is therefore versioned rather than kept as a copy here,
it matches Fred exactly with no deviation to document, and the typeface ships
without a binary living in this repository. The frame's side of the host
protocol arrives the same way, from `@fred-oss/iframe-sdk`, so the message
contract is versioned rather than reimplemented here. Nothing is fetched from
Fred at runtime; only the build needs the npm registry.

Both palettes ship, and Fred keys them off `[data-theme]` on `<html>`. The host
sends the user's choice in its context and republishes when it changes, so the
frame follows Fred rather than guessing. It cannot follow it on the very first
paint, which happens before any message arrives, so the OS preference stands in
until then and remains the answer for a host too old to send one. Both live in
the single source in `index.html`; the dashboard only hands it each context it
receives.

There is deliberately no `capability/` directory. The one API service owns the
data path for both callers:

```text
iframe
  └─ fred:request ── bearer ──> Fred gateway ──> API REST ─┐
                                                          ├─ ReBAC ─> OpenSearch
Fred UI ─> agent runtime ─> MCP client ── bearer ─> /mcp ─┘
                            auth_mode: delegated
```

The gateway routes the iframe request but does not authorize the application's
data. Likewise, registering an MCP server does not make its backend trust the
caller. Both paths end in the same local JWT validation and direct OpenFGA
application-access check before OpenSearch is touched.

## What “no capability” means here

There is no hand-written, pip-installed Python capability and no secret copied
into an agents namespace. Fred still represents every registered MCP server as
a selectable capability so an administrator can decide which agents may see
its tools. In the Create/Edit Agent UI, `mcp-review-board` is therefore the
capability to select.

That distinction produces two independent grants:

| Grant | What it permits |
| --- | --- |
| `app__review-board` | a team member may open the application and the backend may authorize their team data |
| `mcp-review-board` | agents in that team may activate this MCP server's tools |

Granting one does not grant the other. Register the MCP server once, grant both
entries once per team, and select `mcp-review-board` on each agent that needs
its tools. One registration serves every agent; there is no MCP server and no
key per agent.

The API still has backend-only credentials: its dedicated Keycloak M2M secret,
its independently revocable OpenFGA API token, and, when OpenSearch requires
them, an OpenSearch username and password. The M2M identity is this
first-party process's independently revocable identity reserved for outbound
service calls; it is not used to validate the caller and it is not the
OpenFGA credential. None of these secrets replaces the caller's bearer or is
mounted into an agent pod.

## Identity and team scope

The exported MCP operations declare a top-level argument named `team_id`.
Before Fred calls the MCP server, the runtime overwrites that argument with the
active agent's team id. A prompt cannot use the model-generated value to switch
the call to another team. The API then checks that the authenticated user is a
member of that team and that the team has `app:review-board`.

The bearer proves the **human user**, not which agent instance acted. The
`agent_label` path value is display attribution supplied by the tool call. It
is useful for a progress board, but it is not a cryptographic audit identity.
The backend records the validated user's uid separately; do not use
`agent_label` for authorization or non-repudiation.

The write route also rechecks the `mcp-review-board` team grant. That means a
direct caller cannot bypass the MCP catalog's administration gate and use the
write operation with only the application grant.

## API and tools

Fred strips `/app-services/review-board` before proxying iframe requests, so
the service receives the paths below directly.

| Method and path | Caller | Purpose |
| --- | --- | --- |
| `GET /healthz` | probes | readiness |
| `POST /teams/{team_id}/tasks` | iframe REST | create a task and return its opaque id |
| `GET /teams/{team_id}/tasks` | iframe REST | list the team's accessible tasks, with the dashboard aggregates over them under `statistics` |
| `GET /teams/{team_id}/tasks/{task_id}` | iframe REST or MCP `read_task_progress` | read a task and all current reporter states |
| `PUT /teams/{team_id}/tasks/{task_id}/reporters/{agent_label}` | MCP `report_task_progress` | upsert one reporter's status, percentage, and detail |

The legacy generic-task API creates a task, shows its id, and polls the detail route. Give that
opaque id to the agents working on the task. Each agent reports a `status`, a
`progress_percent`, and optional `detail`; the latest state for each exact
reporter label is shown together.

Document-review additions (all beneath `/teams/{team_id}`):

| Route | Purpose / MCP operation |
| --- | --- |
| `GET /folders`, `GET /documents?tag_id=...` | authorized corpus picker |
| `GET /tasks/{task_id}/review/{run_id}/statistics` | one run's own numbers; untagged, so it is not a tool |
| `POST /tasks` with `{title, document: {tag_id, document_uid}}` | create a linked review task |
| `POST /tasks/{task_id}/review/claim` | `claim_document_review` — start, resume or restart |
| `GET /tasks/{task_id}/review/{run_id}/context` | `read_document_review_context` — source and saved decisions |
| `POST /tasks/{task_id}/review/{run_id}/stages/{stage_id}/start` | `start_document_review_stage` |
| `POST /tasks/{task_id}/review/{run_id}/stages/{stage_id}/result` | `save_document_review_stage` |
| `POST /tasks/{task_id}/review/{run_id}/heartbeat` | `heartbeat_document_review` — the claim holder is still working |
| `POST /tasks/{task_id}/review/{run_id}/interrupt` | `interrupt_document_review` |
| `POST /tasks/{task_id}/cancel` | cancel a review run |

Run mutations require the current claim id; the backend validates the fixed stage
order. Claim ids are concurrency markers, not replacements for bearer or document
authorization. The heartbeat keeps a silent stage from reading as abandoned, so
the run lease need not outlast the slowest model call. OpenAPI at `/docs`
describes exact request/result fields.

The iframe observes this state; it does not launch agents or pass tokens to
them. The user talks to each selected agent through the normal Fred UI. Fred's
runtime receives that UI turn's bearer and attaches it to the MCP request,
while the iframe independently polls its REST route through `fred:request`.

Every list read, and every detail read of a review task, rechecks corpus
access through Knowledge Flow, so the page keeps its polling small. Each poll
is one task-list request, which also carries the statistics. It polls every 6
seconds while a listed task or the selected task is running (for a generic
task, while one of its reporters is), and every 30 seconds otherwise. It does
not reread a selected review task that is not running and whose list entry
keeps the same status, progress and latest run id; a generic task is reread
on every poll. A task that leaves the list is deselected. A run's own
statistics are fetched once per run, status and completed-stage count.
**Refresh**, and returning to the page, always reread the selected task.

OpenSearch uses two application-owned indexes, configured with
`REVIEW_BOARD_TASKS_INDEX` and `REVIEW_BOARD_STATES_INDEX`. The browser
never queries OpenSearch directly. List and progress searches filter on the
team id that survived the authentication and ReBAC checks; an opaque task-id
lookup also verifies its stored team before returning or updating it.

## Run the API locally

The API creates its `RebacSdk`, connects to OpenSearch, and ensures its indexes
during startup. It therefore needs real first-party Fred security settings even
for a local run. It does not assemble that profile itself: `fred-core`'s
`security_configuration_from_env` reads the shared variable names and fixes the
hardened profile and reader-mode OpenFGA, so the application passes only the two
variable names that are its own.

```bash
cd apps/review-board/api
uv venv && uv pip install -r requirements.txt

export KEYCLOAK_REALM_URL=https://fred.example.com/realms/fred
export KEYCLOAK_USER_AUDIENCE=app
export KEYCLOAK_M2M_CLIENT_ID=review-board-service
export REVIEW_BOARD_M2M_CLIENT_SECRET='<dedicated-client-secret>'
export OPENFGA_API_URL=http://127.0.0.1:9080
export OPENFGA_STORE_NAME=fred
export REVIEW_BOARD_OPENFGA_API_TOKEN='<dedicated-openfga-token>'
export OPENSEARCH_URL=http://127.0.0.1:9200
export OPENSEARCH_VERIFY_CERTS=false
export KNOWLEDGE_FLOW_BASE=http://127.0.0.1:8111/knowledge-flow/v1

.venv/bin/uvicorn app:app --port 8000
```

Set `OPENFGA_AUTHORIZATION_MODEL_ID` when the deployment pins an existing
model. The API reads Fred's existing store and model; it never creates or
synchronizes them. If OpenSearch requires basic auth, also set
`REVIEW_BOARD_OPENSEARCH_USERNAME` and
`REVIEW_BOARD_OPENSEARCH_PASSWORD`. For a private CA, set
`OPENSEARCH_CA_CERTS` to the mounted PEM path and leave certificate
verification enabled.

Set `KEYCLOAK_M2M_REALM_URL` when the workload identity is minted at a different
address than the one people sign in at. `FRED_DELEGATION` holds the JSON
delegation block described under [Acting for a person](#acting-for-a-person);
unset means delegation is off.

`KEYCLOAK_REALM_URL` must exactly match the JWT `iss` and be reachable from the
API for JWKS discovery. The user token's `aud` must contain the exact
`KEYCLOAK_USER_AUDIENCE`, Fred's own `app` (see
[Put the audience in the browser token](#4-put-the-audience-in-the-browser-token)).

The dependency is `fred-core>=4.4.0,<5`. Use a package index that carries it,
or build the images from a local Fred checkout, from the repository root:

```bash
python dockerfiles/build_local.py --fred-root <fred-checkout> --tag sample \
  --component review-board
```

The SDK checks `app:review-board`; `app__review-board` remains the admin id.
Fred's authorization model must already carry that application: this backend
reads the model and never migrates it.

The UI cannot run usefully on its own. It holds neither a bearer nor an API
address: it uses `fred:request`, and the Fred host attaches the bearer and app
route.

The app-local tests use fake ReBAC/OpenSearch adapters while exercising the
real FastAPI and MCP transports:

```bash
uv pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q ../tests
```

The tests live beside the service rather than inside it, and put `api/` on the
path themselves, so they are named explicitly.

## Register all three integration points

These are deployment values in Fred, not resources created by this sample.

### 1. Register the application

```yaml
platform:
  frontend:
    feature_flags:
      enableApplications: true
  application_sources:
    - app_id: review-board
      ui_prefix: /apps/review-board
      version: 0.1.0
      icon: checklist
      display_name:
        en: "Review Board"
      description:
        en: "Review a corpus document with specialist agents and keep what they decided."
      enabled: true
```

### 2. Route the iframe and REST API

```text
FRONTEND_APPLICATIONS_JSON: |
  [
    {
      "app_id": "review-board",
      "ui_upstream": "http://review-board-ui.review-board.svc.cluster.local:80",
      "service_upstream": "http://review-board-api.review-board.svc.cluster.local:8000",
      "service_required": true
    }
  ]
```

Both upstreams are fully qualified because Fred's variable nginx proxy resolves
them without the pod DNS search suffix.

### 3. Register the MCP server

The runtime's `mcp_catalog.yaml` already carries this entry, in the ConfigMap in
`agents/deploy.yaml`:

```yaml
- id: "mcp-review-board"
  name: "Review Board"
  description: "Team-scoped review-board records, written with the caller's bearer"
  enabled: true
  transport: "streamable_http"
  url: "http://review-board-api.review-board.svc.cluster.local:8000/mcp"
  sse_read_timeout: 5000
  auth_mode: "delegated"
  team_scope: "admin_gated"
```

`auth_mode: delegated` is the critical line: on each interactive execution,
Fred's MCP client sends `Authorization: Bearer <current UI access token>`, and a
runtime acting for people activates only a server declared this way, sending
its workload bearer with the grant. There is no service-key fallback.
`team_scope: admin_gated` keeps activation an explicit team-administrator
decision. If you rename the catalog id, set the backend's `MCP_SERVER_ID` to
that exact same value because the write route checks that grant directly.

Once the runtime is up, grant both `app__review-board` and `mcp-review-board` to
the team. Register the runtime in the Control Plane's
`platform.runtime_catalog_sources`, with a reachable server-side URL and the
`/agents/review-board/v1` gateway route it serves. Discover its templates,
enroll the coordinator, and select `mcp-review-board` on that instance.
The three specialist definitions declare that MCP server explicitly. Other
generic reporter agents may still select it independently.

### 4. Put the audience in the browser token

`KEYCLOAK_USER_AUDIENCE` is checked strictly: the access token Fred forwards
must carry it in `aud`. Set it to `app`, the audience of Fred's login client.
The frame, and an agent runtime that does not act for people, send that same
user token, so the application accepts the platform's audience rather than one
of its own; Fred's `c3` backends require it too. Any token they accept
therefore authenticates here, and team membership with the application grant is
what admits it. A realm whose browser tokens lack `app` in `aud` needs an
audience mapper on the `app` client, once, from the Keycloak pod (set `REALM`
to yours):

```bash
KC=/opt/keycloak/bin/kcadm.sh REALM=app
$KC config credentials --server http://localhost:8080 --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" \
  --password "$KC_BOOTSTRAP_ADMIN_PASSWORD"
CID=$($KC get clients -r "$REALM" -q clientId=app --fields id \
  --format csv --noquotes)
$KC create "clients/$CID/protocol-mappers/models" -r "$REALM" \
  -s name=fred-app-audience -s protocol=openid-connect \
  -s protocolMapper=oidc-audience-mapper \
  -s 'config."included.client.audience"=app' \
  -s 'config."access.token.claim"=true' -s 'config."id.token.claim"=false'
```

Sign out and back in after adding it: tokens minted before the mapper keep
their old `aud`. Without it the frame shows "Your session needs attention", the
API answers `401` with detail `Invalid token`, and its log carries
`[AUTH] Invalid JWT token`. Neither states the reason, so check the token's
`aud` rather than expecting the cause in the response.

## Deploy

Edit every value marked `EDIT` in `deploy.yaml`. Provision the backend's own
Keycloak client and OpenFGA key first, then create the referenced Secret. The
OpenSearch fields are optional when that cluster accepts unauthenticated
in-namespace traffic:

```bash
kubectl create namespace review-board --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic review-board-security -n review-board \
  --from-literal=m2m-client-secret="$REVIEW_BOARD_M2M_CLIENT_SECRET" \
  --from-literal=openfga-api-token="$REVIEW_BOARD_OPENFGA_API_TOKEN" \
  --from-literal=opensearch-username="$REVIEW_BOARD_OPENSEARCH_USERNAME" \
  --from-literal=opensearch-password="$REVIEW_BOARD_OPENSEARCH_PASSWORD"
```

Omit the last two arguments when OpenSearch has no basic authentication; their
Secret references are optional. For a private OpenSearch CA, mount the PEM into
the API container and set `OPENSEARCH_CA_CERTS` to that file's path.

```bash
docker build -t review-board-ui:sample  apps/review-board/ui
docker build -t review-board-api:sample apps/review-board/api
kubectl apply -f apps/review-board/deploy.yaml
```

The agent runtime is a second manifest in the same namespace,
`apps/review-board/agents/deploy.yaml`. It expects a `review-board-agents-env`
Secret holding the runtime's own Keycloak, Postgres and OpenFGA credentials, and
the image name the manifest names:

```bash
cd agents && make docker-build \
  DOCKER_IMAGE_NAME=review-board-agents DOCKER_IMAGE_TAG=local
```

It ships with `replicas: 1`, so its agents are selectable once it is deployed.
Each runtime is a full Fred runtime with its own model client and connection
pools; scale the others down if the cluster cannot hold one per application.

Do **not** copy the API's Secret into the runtime. The runtime carries its own,
and its agents send only the signed-in user's bearer; the API's OpenFGA and
OpenSearch credentials stay with the application backend.

The API manifest deliberately runs one replica with the `Recreate` deployment
strategy. `fastapi-mcp==0.4.0` keeps streamable-HTTP session state in memory,
so requests for one MCP session cannot be spread safely across replicas; the
`Recreate` strategy also prevents a rolling update from temporarily running an
old and a new API pod at the same time. Task progress remains durable in
OpenSearch. Before scaling the API or restoring rolling updates, provide MCP
session affinity or a stateless/shared-session design and verify it end to end.

## Verify the boundary

Check the layers independently before relying on the progress board. Default
API and agent tests use offline adapters; a passing unit suite is not proof of
a deployed LLM/corpus/OpenSearch flow:

```sh
# From the repository root, with the API requirements and pytest installed:
python -m pytest apps/review-board/tests
node --test apps/review-board/tests/*.test.mjs
# From agents/:
make code-quality import-order test
```

1. Call `/mcp` without `Authorization`; MCP initialization must return `401`.
2. As a valid user outside the task's team, call
   `/teams/<team>/tasks`; it must return `403` and reveal no task ids.
3. As a member of a team without `app__review-board`, repeat the REST call;
   it must also return `403`.
4. Grant both entries and run a linked document review using the coordinator.
   To test legacy reporters separately, create an unlinked task through REST
   with `{ "title": "Legacy reporter check" }` and ask an agent to call
   `report_task_progress` with its task id and a stable `agent_label` such as
   `worker-1`. Generic reporter writes must be rejected for linked review tasks.
5. Revoke `mcp-review-board`. Fred must stop offering the tools, and a direct
   call to the write route with an otherwise valid user bearer must return
   `403` without changing OpenSearch.

A `403` is a decision about the caller. A `5xx` from these calls means the
authorization dependency did not answer — an outage to investigate, never a
denial to record.

For the review workflow, create a linked task from an authorized corpus document,
execute the coordinator, and compare the four saved stage results with the page.
Verify the API can restart without losing them; retry and cancellation do not
duplicate/regress completion; and corpus revocation also hides saved decisions
and statistics. Compare the chart counts/durations with persisted results.

## Acting for a person

Without delegation, an **interactive Fred UI turn** carries the signed-in user's
bearer, and the runtime deliberately retains no refresh token for the agent
execution. One unusually long turn can therefore outlive the bearer and its next
MCP call will fail authentication; start a new UI turn to obtain a fresh token.

A runtime that acts for people (`act_for_people`) uses the delegation grant
instead, for every run it makes for a person, interactive or not.
`FRED_DELEGATION` set to
`{"accept_delegated_calls": true, "service_accounts_only": true}` lets a
workload speak for a person; unset, nothing is believed. A workload whose bearer
carries the delegation audience and caller role presents it plus `person`,
`run` and `agent` parameters, and the API then runs the same team, application
and corpus checks it would run for a browser turn. Knowledge Flow calls carry
the grant too, so a revoked document is still refused.

The parameters ride the endpoint, never the tool-call body the model influences,
and a workload without the caller role acts for nobody. That is what keeps this
different from a shared application key: the workload is named and revocable,
and it never holds the person's credential.

## Production hardening

The manifest uses a cluster-local HTTP URL so it remains readable and easy to
adapt. That URL carries a user bearer. In production, protect it with HTTPS or
service-mesh mTLS, and apply a NetworkPolicy that admits port 8000 only from
the Fred agent runtime and frontend/gateway. Kubernetes NetworkPolicy cannot
distinguish `/mcp` from REST on the same port; use an L7 mesh policy or split
the endpoints if path-level isolation is required. Restrict API egress to
Keycloak/JWKS, OpenFGA, Knowledge Flow and OpenSearch.

Also:

- never log, store, or return the `Authorization` header;
- use a dedicated, least-privilege OpenSearch identity limited to these two
  indexes;
- keep JWT issuer/audience checks strict and test expired/wrong-audience tokens;
- test a valid user outside the team and a member of a team without the app
  grant — both must receive `403`, with no OpenSearch write;
- treat `agent_label` as display metadata, never as a security principal;
- rotate the M2M, OpenFGA, and OpenSearch credentials independently.
