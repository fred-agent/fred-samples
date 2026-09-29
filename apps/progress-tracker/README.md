# Sample application — progress tracker

A minimal, end-to-end example of a Fred **application**: the user records a
task, and the application keeps the record — the notes, the decisions taken
along the way, and the conversations the task was discussed in — so the work
survives separate conversations.

It exists to show the mechanics, not the business case. Everything here is
deliberately small enough to read in one sitting.

> **Fred does not build this.** An application is its owner's images: the two
> Dockerfiles in this directory are built and deployed by you, and nothing in
> Fred's own source imports this tree. That separation is the point — the day
> a Fred build target has to know about your application, it has stopped being
> an application and become part of the platform.

---

## What it demonstrates

| Concern | How |
| --- | --- |
| An application is its owner's images | `ui/` and `api/`, built and deployed independently |
| The frame holds no credential | the UI asks the host; the host attaches the bearer |
| The service authorizes itself | the backend validates the caller's bearer locally, then checks team membership and `app:progress-tracker` in OpenFGA; the gateway does neither |
| An application is a ReBAC **reader** | `create_store_if_needed=false` and `sync_schema_on_init=false` — the control plane owns the store and the authorization model |
| A conversation finds its record | the session id is pinned on first resolution |
| Human-in-the-loop | the decision is a record the user made, written through an authorized request |
| One service, two entry points | the API serves the iframe's REST routes and an authenticated MCP endpoint at `/mcp` |
| No shared agent key | the MCP mount accepts the signed-in user's bearer, or the runtime's own workload bearer plus a grant naming the person |

## The two pieces

```
ui/    static page, served at /apps/progress-tracker/
       index.html, package.json, package-lock.json, Dockerfile
api/   FastAPI REST service + streamable HTTP MCP endpoint at /mcp
```

The page has no framework and no bundler, but it is not hand-styled either. Its
palette and its side of the Fred protocol are installed from Fred's published
packages, `@fred-oss/design-tokens` and `@fred-oss/iframe-sdk`, pinned in
`ui/package.json`. The image build installs them and copies out two files: a
stylesheet and a self-contained ES module. So the frame tracks Fred's own
colours in both themes, follows the theme the user actually chose rather than
guessing at it from the operating system, and speaks the protocol through Fred's
client rather than a copy of it — which is also what gives it strict origin
checking on every message. Building the UI image therefore needs the npm registry; running it does
not.

There is deliberately no `capability/` directory. The application ships no
pip-installable Python capability package and no `fred.capabilities` entry
point, and none of its credentials is copied into an agents namespace.

To turn this into a different application, copy the directory and change
`APP_ID` (an environment variable read by `api/app.py`, and the object the
backend checks in OpenFGA), `MCP_SERVER_ID` together with the MCP catalog
entry's `id`, the nginx prefix and the two copy destinations in `ui/Dockerfile`,
and the names in `deploy.yaml`. `MCP_SERVER_ID` carries its own default and is
not derived from `APP_ID`, so a copy that leaves it alone checks the original
application's capability. In `ui/index.html`, change the `<base href>` and the
`applicationId` given to the client: the first is what makes the asset URLs
resolve under the new prefix, and the host rejects a context naming a different
application than the one the client claims.

## Configuration

The API is a first-party backend, so it needs real Fred security settings even
for a local run. Startup fails unless every required variable is set and the
SDK can resolve Fred's existing OpenFGA store — a misconfigured backend never
serves a request.

| Variable | Required | Purpose |
| --- | --- | --- |
| `KEYCLOAK_REALM_URL` | yes | exact `iss` of the incoming user bearer; also used for JWKS discovery |
| `KEYCLOAK_USER_AUDIENCE` | yes | exact value the same bearer must carry in `aud`: `app`, Fred's login client |
| `KEYCLOAK_M2M_CLIENT_ID` | yes | this backend's own confidential client |
| `PROGRESS_TRACKER_M2M_CLIENT_SECRET` | yes | that client's secret; startup fails without it |
| `OPENFGA_API_URL` | yes | Fred's existing OpenFGA |
| `PROGRESS_TRACKER_OPENFGA_API_TOKEN` | yes | this backend's independently revocable OpenFGA token |
| `KEYCLOAK_M2M_REALM_URL` | no — the user realm | `iss` a workload bearer must match, when workloads are minted elsewhere |
| `FRED_DELEGATION` | no — off | JSON delegation block: `accept_delegated_calls`, `service_accounts_only`, `audience`, `caller_role` |
| `OPENFGA_STORE_NAME` | no — `fred` | the store the control plane owns |
| `OPENFGA_AUTHORIZATION_MODEL_ID` | no | set it when the deployment pins an existing model |
| `APP_ID` | no — `progress-tracker` | the application id; the backend checks `app:<APP_ID>` |
| `MCP_SERVER_ID` | no — `mcp-progress-tracker` | the capability the write tools check; must equal the MCP catalog id |
| `PROGRESS_TRACKER_DB` | no — `./progress-tracker.db` | SQLite file; the API image sets `/data/progress-tracker.db` |
| `RUNTIME_BASE` | no | agent runtime used to read back pinned conversations; unset, that route reports `runtime_not_configured` |
| `LOG_LEVEL` | no — `INFO` | level for the shared logging setup, which carries the audit stream |

The M2M client is this backend's own identity, required by the `c3` profile;
it validates no caller. A delegating workload's bearer is addressed to the
delegation audience instead, and the M2M realm is the `iss` it must match.
The one outbound call this API makes forwards the caller's own bearer. The
secret is read at startup and is not the OpenFGA credential; rotate the two
independently.

The dependency is `fred-core>=4.4.0,<5`, so the image build needs a package
index serving it. The delegated tool mount additionally needs the tool-server
package: `api/requirements.txt` pins `fastapi-mcp` directly, and installing
`fred-core[mcp]` brings the same pin. The SDK checks `app:progress-tracker`;
`app__progress-tracker` remains the catalog/admin id.

## Run the API on its own

```bash
cd apps/progress-tracker/api
uv venv && uv pip install -r requirements.txt

export KEYCLOAK_REALM_URL=https://fred.example.com/realms/fred
export KEYCLOAK_USER_AUDIENCE=app
export KEYCLOAK_M2M_CLIENT_ID=progress-tracker-service
export PROGRESS_TRACKER_M2M_CLIENT_SECRET='<dedicated-client-secret>'
export OPENFGA_API_URL=http://127.0.0.1:9080
export OPENFGA_STORE_NAME=fred
export PROGRESS_TRACKER_OPENFGA_API_TOKEN='<dedicated-openfga-token>'

.venv/bin/uvicorn app:app --port 8000
```

Every team route is a `401` without a bearer, and a `403` for a valid caller
whose team was never granted the application — the service fails closed in both
directions by design. `/healthz` is the only route outside that gate, and it
returns nothing about the team's data.

The **UI cannot run standalone**. It holds no API address and makes no `fetch`
call: every request goes to the host frame over `postMessage`, so outside Fred
it stops at "connecting…". That is the design, not a gap.

## How a conversation finds its task

The record keeps the link, so the user never types an id:

1. `POST /teams/{team}/tasks/{handle}/discuss` records a short-lived pending
   pin for the caller, keyed on the team plus the caller's validated subject.
2. `POST /teams/{team}/discuss/claim` attaches that pin to a conversation,
   inside one write transaction.
3. `POST /teams/{team}/tasks/{handle}/sessions` links a conversation to a task
   directly, when the task is already known.
4. `GET /teams/{team}/tasks?session_id=...` resolves the task for a
   conversation, so every later turn needs no question.

All four run the same bearer validation and the same `app:progress-tracker`
check. The acting subject always comes from the validated token, never from a
request body, so a caller cannot claim or drop someone else's pin.

Over weeks a record accumulates the conversations that touched it, which the UI
shows as a conversation count.

An agent reaches those same routes through the MCP server this API mounts at
`/mcp`, in one of two catalog modes. Under `auth_mode: user_token` Fred forwards
the signed-in user's bearer. Under `auth_mode: delegated` Fred sends the agent
runtime's own workload bearer and names the person in a grant on the endpoint's
query string — outside the tool arguments, so nothing the model writes can name
someone else, and the grant is stripped from the tool schemas it sees. Either
way the person is resolved at the mount and every check above runs on them.
There is no shared agent key and no credential of this application reaches the
agents namespace.

Six routes carry the `ProgressTracker` tag and become tools; the rest stay
reachable by the UI and invisible to an agent. Tagging is the whole allowlist.

| Tool | What an agent uses it for |
| --- | --- |
| `claim_pinned_task` | take up the task the user opened the chat from, and attach it to this conversation |
| `list_tasks` | list the team's tasks, or resolve the one a conversation is already about |
| `read_task` | read one task with its notes, decisions and conversations |
| `link_conversation_to_task` | link a conversation to a task that is already known |
| `record_task_note` | append an observation to the timeline |
| `record_task_decision` | checkpoint a settled question and its answer |

The conversation identifier is never something the model has to know. Fred's
runtime fills `session_id` in on any tool whose schema declares it, so an agent
calls `claim_pinned_task` with nothing and the right conversation is linked.

The four write tools additionally check the team holds `mcp-progress-tracker`.
Entitlement to open the page is therefore not entitlement for an agent to write
the record: a team can have the application and still not let agents touch it.

## Why the decision record is trustworthy

`POST /teams/{team}/tasks/{handle}/decisions` writes through the API, not
straight to storage. That matters: the API validates the caller's bearer and
its team's grant before writing, so "the user decided X" is backed by an
authenticated request rather than by text an agent read in a chat and chose to
believe. An agent writing the record directly would be trusting its own output.

## Storage

SQLite, one file, named by `PROGRESS_TRACKER_DB` (default `./progress-tracker.db`).

It is chosen so the sample needs no external datastore, and so the parts worth
copying stay honest rather than hand-rolled:

- `(team_id, handle)` is a real primary key, so a colliding handle is an error
  the writer retries — not a second row that silently shadows the first on every
  later read.
- Claiming a pending pin is **one write transaction**, so two conversations
  starting at once cannot both take it. No compare-and-swap to get right.

**What it is not** is a store for more than one writer. `deploy.yaml` runs a
single replica with an `emptyDir`, so records live exactly as long as the pod.
Point `PROGRESS_TRACKER_DB` at a PersistentVolumeClaim to keep them across
restarts, and move to your own datastore before you scale past one replica.
Every statement lives in `api/app.py`, which is what keeps that swap small.

## Deploy

Edit every value marked `EDIT` in `deploy.yaml` first. Provision this backend's
own Keycloak client — confidential, with its service account enabled — and its
OpenFGA token, then create the Secret they are read from:

```bash
kubectl create namespace progress-tracker --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic progress-tracker-security -n progress-tracker \
  --from-literal=m2m-client-secret="$PROGRESS_TRACKER_M2M_CLIENT_SECRET" \
  --from-literal=openfga-api-token="$PROGRESS_TRACKER_OPENFGA_API_TOKEN"
```

```bash
docker build -t progress-tracker-ui:sample  apps/progress-tracker/ui
docker build -t progress-tracker-api:sample apps/progress-tracker/api
kubectl apply -f apps/progress-tracker/deploy.yaml
```

Do **not** copy that Secret into the Fred or agents namespace. Both entries are
backend-only process credentials; nothing outside this API pod needs either.

Building the images, registering the application, verifying it end to end and
the troubleshooting table are in [../DEPLOYMENT.md](../DEPLOYMENT.md). It covers
neither the Keycloak client and OpenFGA token this backend needs nor the
`mcp-progress-tracker` grant the write tools check; both are described here.

## Register it

Two halves, one `app_id`, and **nothing cross-checks them**. Both are edits to
your Fred deployment's own values, not to anything in this repository.

Control plane — owns what teams see, and the application that authorization is
granted against:

```yaml
platform:
  frontend:
    feature_flags:
      enableApplications: true
  application_sources:
    - app_id: progress-tracker
      ui_prefix: /apps/progress-tracker
      version: 0.1.0
      icon: checklist
      display_name:
        en: "Progress Tracker"
      description:
        en: "Track long-running work and the decisions taken along the way."
      enabled: true
```

Frontend gateway — owns the server-side addresses:

```
FRONTEND_APPLICATIONS_JSON: |
  [
    {
      "app_id": "progress-tracker",
      "ui_upstream": "http://progress-tracker-ui.<namespace>.svc.cluster.local:80",
      "service_upstream": "http://progress-tracker-api.<namespace>.svc.cluster.local:8000",
      "service_required": true
    }
  ]
```

**Both upstreams must be fully qualified.** Fred proxies applications through a
variable `proxy_pass`, which makes nginx resolve the host at request time
through its own resolver — and that path does not apply the pod's DNS search
list. A bare Service name yields `could not be resolved` and a 502, while the
same name works from a shell in the very same pod.

### Register the MCP server

A third half, for the agent path. Add this to the agent runtime's
`mcp_catalog.yaml`:

```yaml
- id: "mcp-progress-tracker"
  name: "Progress Tracker"
  description: "Resolve the task a user asked to discuss, and record its notes and decisions."
  prompt_group_title: "progress tracking"
  enabled: true
  transport: "streamable_http"
  url: "http://progress-tracker-api.<namespace>.svc.cluster.local:8000/mcp"
  sse_read_timeout: 2000
  auth_mode: "user_token"
```

`auth_mode` is the line that matters, and there is no service-key fallback under
either value. Use `user_token` when the agent runtime does not run its agents
under a workload credential: Fred's MCP client sends the current user's bearer
on each interactive turn. Use `delegated` when it does — such a runtime refuses
to activate a server declared any other way, rather than quietly sending the
wrong credential. The id must match the API's `MCP_SERVER_ID`, because the write
tools check that exact capability.

Under `delegated`, tell this backend to believe the person a workload names:

```
FRED_DELEGATION: |
  {"accept_delegated_calls": true, "service_accounts_only": true}
```

A workload is believed only when its bearer comes from the realm
`KEYCLOAK_M2M_REALM_URL` names, is addressed to the delegation audience
(`fred-delegation` by default) and carries the delegation caller role
(`delegation_caller`); `service_accounts_only` also requires a client's own
service-account token. A grant from any other caller names nobody, so the
request is authorized as that caller itself and denied. With delegation on, a
suspended person's account refuses the request, and an unanswered account-status
check answers `503`.

### Grant it to a team

A platform administrator then grants `app__progress-tracker` to a team, and
`mcp-progress-tracker` alongside it for agents in that team to use the tools.
Both are separate grants on purpose: a team can hold the application, so its
people see the page, while no agent may write to the record.

Registration alone grants nothing, and the backend reads that grant straight
from OpenFGA rather than asking the control plane. The grant is read with high
consistency, so revoking it denies the next request rather than waiting for a
cache to expire.

### Put the audience in the browser token

`KEYCLOAK_USER_AUDIENCE` is checked strictly: the access token Fred forwards
must carry it in `aud`. Set it to `app`, the audience of Fred's login client.
For a realm whose browser tokens lack `app` in `aud`, the audience mapper
commands are in the sibling sample's
[audience section](../review-board/README.md#4-put-the-audience-in-the-browser-token).
Sign out and back in after adding it: tokens minted before the mapper keep
their old `aud`.

## Before you ship anything modelled on this

Create a user who is **not** in the granted team, get a token, and call the API
directly:

```
GET /app-services/progress-tracker/teams/<granted-team>/tasks   as the outside user
expected 403 — a 200 means your data is readable by the whole realm
```

Repeat for a user who *is* in a team that was never granted the app, and once
more with no bearer at all — `401`. Forgetting the authorization check produces
no error and no log line, because from inside a granted team everything looks
correct. That test is the only way to see it.

Before trusting a green result, confirm your negative user is actually
negative: an application granted to every team makes the test pass for the
wrong reason. Then revoke the grant and repeat the first call — it must become
a `403` on the next request.

## Handing a task to a conversation

An application cannot route the user anywhere it likes: `fred:navigate` is
bounded to its own subtree and an escaping path is silently dropped. The one
exception is `fred:open-chat`, which names no destination — the host decides
where it lands, and honours a session id only after matching it against the
viewer's own conversations.

"Discuss in chat" records a short-lived pending pin, then asks the host to open
a conversation — resuming the one this task was last discussed in when that
conversation belongs to the viewer, and starting a fresh one otherwise. An
unlinked conversation claims the pin and links itself, so there is nothing for
the user to type. A conversation that is already linked drops the pin instead
of claiming it, so a spent intent cannot capture the next chat.

The pin is keyed on the team plus the caller's subject and expires, so a click
that is never followed up simply lapses. Claiming happens inside one write
transaction, so two conversations starting at once cannot both take it. The
subject is read from the validated bearer and nowhere else, so nothing a caller
puts in a request body can redirect the pin to someone else's work.
