# Sample application — progress tracker

A minimal, end-to-end example of a Fred **application**: the user records a task,
then advances it by **talking to agents**, and the application keeps the record
so the work survives and separate conversations.

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
| The service authorizes itself | every handler asks the Control Plane; the gateway does not |
| Agents advance durable state | a capability with tools, not an app-driven pipeline |
| A conversation finds its record | the session id is pinned on first resolution |
| Human-in-the-loop | the decision is a record the user made and agents later trust |

## The three pieces

```
ui/           static page, served at /apps/progress-tracker/
api/          FastAPI service, reached at /app-services/progress-tracker/
capability/   the agent tools, installed into an agents pod
```

All three live here, together, because they are one application owned by one
team. A capability is just a pip-installable package, so where it lives is a
statement about ownership, not a technical requirement.

Every path and identifier is derived from a single `app_id`. To turn this into
a different application, copy the directory and change `APP_ID` (an environment
variable read by `api/app.py`), the nginx prefix in `ui/Dockerfile`, and the
names in `deploy.yaml`.

## Run the API on its own

The service needs no cluster and no external datastore, so the quickest way to
see it work is to run it directly. The service key below stands in for the
agents pod, which is what lets you exercise it with no Control Plane:

```bash
cd apps/progress-tracker/api
uv venv && uv pip install -r requirements.txt
PROGRESS_TRACKER_SERVICE_KEY=dev-key .venv/bin/uvicorn app:app --port 8000
```

```bash
curl -H 'X-Service-Key: dev-key' -H 'content-type: application/json' \
  -d '{"title":"Try the sample locally"}' \
  http://127.0.0.1:8000/teams/demo/tasks
curl -H 'X-Service-Key: dev-key' http://127.0.0.1:8000/teams/demo/tasks
```

Without that header the same call is a `401`, and with a user bearer instead it
is a `403` until `CONTROL_PLANE_BASE` points at a real Control Plane — the
service fails closed in both directions by design.

The **UI cannot run standalone**. It holds no API address and makes no `fetch`
call: every request goes to the host frame over `postMessage`, so outside Fred
it stops at "connecting…". That is the design, not a gap.

## How a conversation finds its task

The agent never has to be told which record it is on, and the user never types
an id.

1. `find_task` asks the API for the task pinned to **this conversation**. The
   session id comes from `ctx.identity.session_id`, supplied by the runtime.
2. On a new conversation there is no pin, so the agent calls `list_open_tasks`
   and asks which one the user means — once.
3. `pin_task` links the conversation to the record. Every later turn in that
   session resolves with no question, indefinitely.

The session id is **never a tool parameter**. The SDK keeps identity out of the
schema the model sees, so a prompt cannot redirect an agent onto another
conversation's task.

Over weeks a record accumulates the conversations that touched it, which the UI
shows as a conversation count.

## Why the decision record is trustworthy

`record_decision` writes through the API, not straight to storage. That matters:
the API verifies the caller before writing, so "the user decided X" is backed by
an authenticated request rather than by text an agent read in a chat and chose
to believe. An agent writing the record directly would be trusting its own
output.

## Storage

SQLite, one file, named by `PROGRESS_TRACKER_DB` (default `./progress-tracker.db`).

It is chosen so the sample runs with no external service, and so the parts worth
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

## The service key is this sample's own, not a Fred mechanism

The capability calls this application's API as *itself*, authenticating with a
shared secret, `PROGRESS_TRACKER_SERVICE_KEY`, that the operator creates and
wires into both the API and the agents pod.

**Fred knows nothing about that key.** It appears only in this directory. The
control plane does not issue, validate or rotate it, the gateway never sees it,
and no platform document mentions it. Copying this sample gives you a pattern,
not an integration.

It exists because there is currently no alternative. `fred-sdk` declares a
`TokenProviderPort` and exposes it to capabilities as
`ctx.services.token_provider`, but nothing in the platform implements or injects
it. An earlier version of this capability read that port, found `None` every
time, and sent no credential at all.

Two reasons not to standardise on it before that changes:

- The key is a **full bypass** of the entitlement check. That path returns the
  team without asking the control plane anything, which is considerable
  authority for a static environment variable with no rotation story.
- Every application doing this invents a different secret, and nothing makes
  them consistent, revocable together, or auditable.

The platform answer is a delegated-downstream-auth design that is not
implemented yet; it names the same root cause, that a pod holds one
fixed-lifetime credential and cannot renew it for downstream calls. When that
lands, this key should be **deleted rather than generalised**.

## Install the capability into an agents pod

Installing the package *is* the registration: a pod discovers it at boot through
the `fred.capabilities` entry point in `capability/pyproject.toml`. Nothing edits
Fred's own Dockerfile or dependency list.

**In this repository it is already wired in.** `agents/pyproject.toml` depends on
`fred-capability-progress-tracker` by path, so:

```bash
cd agents && make dev
```

installs it into the sample pod. Confirm it the way that actually proves it —
by importing, since a broken install still advertises its entry point:

```bash
cd agents
.venv/bin/python -c "import fred_capability_progress_tracker.capability as m; print(m.ProgressTrackerCapability)"
```

Until `PROGRESS_TRACKER_API_BASE` is set the capability logs a warning and
contributes **no tools**, so a pod that merely has it installed behaves exactly
as before. Point it at the API and give it the key to switch the tools on:

```bash
export PROGRESS_TRACKER_API_BASE=http://127.0.0.1:8000
export PROGRESS_TRACKER_SERVICE_KEY=dev-key
```

For any other pod, install it into that pod's environment the same way a fork
would install its own — `uv pip install -e apps/progress-tracker/capability`, or
a line in that pod's dependencies.

## Deploy

```bash
docker build -t progress-tracker-ui:sample  apps/progress-tracker/ui
docker build -t progress-tracker-api:sample apps/progress-tracker/api
kubectl apply -f apps/progress-tracker/deploy.yaml
```

Edit the two addresses marked `EDIT` in `deploy.yaml` first, and create the
`progress-tracker-service` secret in both namespaces (see below).

The full walkthrough — building, registering, granting, verifying, and the
troubleshooting table — is [../DEPLOYMENT.md](../DEPLOYMENT.md), which covers
this sample and [document-triage](../document-triage/README.md) side by side.

## Register it

Two halves, one `app_id`, and **nothing cross-checks them**. Both are edits to
your Fred deployment's own values, not to anything in this repository.

Control plane — owns what teams see, and the capability that authorization is
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

Then a platform administrator grants `app__progress-tracker` to a team.
Registration alone grants nothing.

The API needs `CONTROL_PLANE_BASE` for the entitlement check. The capability
needs `PROGRESS_TRACKER_API_BASE`; without it, it contributes no tools rather
than failing at call time.

## Before you ship anything modelled on this

Create a user who is **not** in the granted team, get a token, and call the API
directly:

```
GET /app-services/progress-tracker/teams/<granted-team>/tasks   as the outside user
expected 403 — a 200 means your data is readable by the whole realm
```

Repeat for a user who *is* in a team that was never granted the app. Forgetting
the entitlement check produces no error and no log line, because from inside a
granted team everything looks correct. That test is the only way to see it.

Before trusting a green result, confirm your negative user is actually negative:
a capability left `default_on` makes every team entitled, so the test passes for
the wrong reason.

## Handing a task to a conversation

An application cannot route the user anywhere it likes: `fred:navigate` is
bounded to its own subtree and an escaping path is silently dropped. The one
exception is `fred:open-chat`, which names no destination — the host decides
where it lands, and honours a session id only after matching it against the
viewer's own conversations.

"Discuss in chat" records a short-lived pending pin, then asks the host to open
a conversation — resuming the one this task was last discussed in when that
conversation belongs to the viewer, and starting a fresh one otherwise. The
first tool call of an unlinked session claims the pin and links it, so there is
nothing for the user to type. A conversation that is already linked drops the
pin instead of claiming it, so a spent intent cannot capture the next chat.

The pin is keyed on team plus the caller's subject and expires, so a click that
is never followed up simply lapses. Claiming happens inside one write
transaction, so two conversations starting at once cannot both take it. The
user's identity comes from the runtime on the agent side and from the bearer on
the app side — the model never names a task or a session, so a prompt cannot
redirect the pin to someone else's work.
