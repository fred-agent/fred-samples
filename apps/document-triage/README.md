# Sample application — document triage

A team reviews the documents in one of its folders: each one gets marked
**reviewed** or **needs work**, with a note. Agents help by reading the corpus
and proposing triage in chat — but only a person records a decision.

It exists to show the mechanics, not the business case. Everything here is
deliberately small enough to read in one sitting.

> **This application holds no credential of its own.** There is no service key,
> no database, no secret in `deploy.yaml`. Every outbound call carries the
> caller's own bearer, forwarded verbatim, and Knowledge Flow authorizes it per
> user. That is the whole point of the sample — see [Why there is no service
> key](#why-there-is-no-service-key).

---

## What it demonstrates

| Concern | How |
| --- | --- |
| An application is its owner's images | `ui/` and `api/`, built and deployed independently |
| The frame holds no credential | the UI asks the host; the host attaches the bearer |
| The service authorizes itself | every handler asks the Control Plane; the gateway does not |
| Two independent gates | the Control Plane gates the *team*; Knowledge Flow gates the *user* |
| Agents read platform data | `document_folders` + `document_summarize`, tokens held by the adapter |
| Agents don't write shared state | the agent proposes; the human commits under their own identity |
| The app stores nothing | records live in the team's workspace, not in a database the app owns |

## The three pieces

```
ui/           static page, served at /apps/fred.samples.document-triage/
api/          FastAPI service, reached at /app-services/document-triage/
capability/   the agent tools — read-only, no credential
```

Every path and identifier is derived from a single `app_id`. To turn this into
a different application, copy the directory and change `APP_ID` (an environment
variable read by `api/app.py`), the nginx prefix in `ui/Dockerfile`, and the
names in `deploy.yaml`.

## How it fits together

```
browser ──postMessage──> Fred host ──bearer──> gateway ──bearer──> api/
                                                                    │
                                            caller's own bearer ────┤
                                                                    ▼
                                                            Knowledge Flow
                                                       (documents + workspace)
                                                                    ▲
                                       runtime-held token ──────────┤
                                                                    │
agents pod ── ctx.services.document_folders / document_summarize / workspace_fs
```

Both sides reach the same Knowledge Flow. Neither authenticates *as itself*:
the API borrows the user's token, and the capability uses ports whose adapters
keep the runtime's token private. That is why no shared secret is needed.

**Where the records live.** One JSON file per document, under the team's shared
workspace:

```
teams/{team}/shared/triage/{slug}.json
```

`{slug}` is derived from the document name plus a short digest of its uid — so
two documents with the same display name stay apart, and the uid (an internal
working identifier) never becomes a file name. One file per document also means
two people triaging different documents never contend.

## Why there is no service key

The sibling sample, [progress-tracker](../progress-tracker/README.md), needs a
shared `PROGRESS_TRACKER_SERVICE_KEY` so its capability can call its API. This
one does not, and the difference is worth understanding before you copy either.

A capability has no way to authenticate an outbound call. `fred-sdk` declares a
`TokenProviderPort` and exposes it as `ctx.services.token_provider`, but nothing
in the runtime ever injects one — it is `None` in practice, and
`CapabilityIdentity` carries no token either. So any capability that calls a
service of its own must invent a credential.

The way out is not a better secret; it is not needing one. Two rules make that
possible:

1. **Read through the ports.** `document_folders`, `document_summarize`,
   `workspace_fs` and friends are implemented by the runtime, which holds the
   token privately and exposes scope parameters only. A capability using them
   authenticates nothing.
2. **Let the human write.** Agents may read team-shared files but may only
   *mutate* inside their own subtree — `_resolve_owned` rejects a write into
   `shared/` outright, on the stated doctrine that **agents never share**
   (FILES-04). An application whose agents wrote shared state would be routing
   around that boundary, which is exactly what a service key buys.

So the agent proposes and the person commits. The record is then backed by an
authenticated request from someone who looked at the document, rather than by an
agent asserting its own output — a stronger trust story than the one a service
key can offer, and it falls out of the constraint rather than fighting it.

## Run the API on its own

The service is stateless and needs no database. It does need a Control Plane
and a Knowledge Flow to talk to, so running it bare mostly proves the
fail-closed behaviour:

```bash
cd apps/document-triage/api
uv venv && uv pip install -r requirements.txt
.venv/bin/uvicorn app:app --port 8000
```

```bash
curl -i http://127.0.0.1:8000/healthz                      # 200
curl -i http://127.0.0.1:8000/teams/demo/folders           # 401, no bearer
```

With a bearer but no `CONTROL_PLANE_BASE` it is a `403`
(`entitlement_check_unconfigured`); entitled but with no `KNOWLEDGE_FLOW_BASE`
it is a `503`. Every failure closes.

The **UI cannot run standalone**. It holds no API address and makes no `fetch`
call: every request goes to the host frame over `postMessage`, so outside Fred
it stops at "connecting…". That is the design, not a gap.

## The agent side

The capability is already wired into this repository's sample pod —
`agents/pyproject.toml` depends on it by path, so `cd agents && make dev`
installs it, and the pod discovers it at boot through the `fred.capabilities`
entry point. Confirm by importing, since a broken install still advertises its
entry point:

```bash
cd agents
.venv/bin/python -c "import fred_capability_document_triage.capability as m; print(m.DocumentTriageCapability)"
```

Three tools, all read-only:

| Tool | What it does |
| --- | --- |
| `list_folder_documents(folder)` | resolves a folder to its tag and lists its documents, joined with their triage state |
| `show_triage_board()` | reads what the team has already decided |
| `propose_triage(folder, document_name)` | summarizes one document for a reviewer and proposes a mark |

`propose_triage` ends by telling the user it cannot record anything, so the
model does not claim to have saved a decision it has no way to save.

If the runtime injects no document ports, the capability logs a warning and
contributes **no tools** rather than half a toolset that fails at call time.

## Register it

Two halves, one `app_id`, and **nothing cross-checks them**. Both are edits to
your Fred deployment's own values, not to anything in this repository.

Control plane:

```yaml
platform:
  frontend:
    feature_flags:
      enableApplications: true
  application_sources:
    - app_id: fred.samples.document-triage
      ui_prefix: /apps/fred.samples.document-triage   # must be exactly /apps/<app_id>
      version: 0.1.0
      icon: checklist
      display_name:
        en: "Document Triage"
      description:
        en: "Review a folder of documents and record what was decided."
      enabled: true
```

Frontend gateway:

```
FRONTEND_APPLICATIONS_JSON: |
  [
    {
      "app_id": "fred.samples.document-triage",
      "ui_upstream": "http://document-triage-ui.<namespace>.svc.cluster.local:80",
      "service_upstream": "http://document-triage-api.<namespace>.svc.cluster.local:8000",
      "service_required": true
    }
  ]
```

**Both upstreams must be fully qualified.** Fred proxies applications through a
variable `proxy_pass`, which makes nginx resolve the host at request time
through its own resolver — and that path does not apply the pod's DNS search
list. A bare Service name yields `could not be resolved` and a 502, while the
same name works from a shell in the very same pod.

Then a platform administrator grants `app__document-triage` to a team.
Registration alone grants nothing.

## Deploy

```bash
docker build -t document-triage-ui:sample  apps/document-triage/ui
docker build -t document-triage-api:sample apps/document-triage/api
kubectl apply -f apps/document-triage/deploy.yaml
```

Edit the two addresses marked `EDIT` in `deploy.yaml` first. There are no
secrets to create and no volume to provision.

The full walkthrough of building, registering and verifying a Fred application
— including the troubleshooting table — is [../DEPLOYMENT.md](../DEPLOYMENT.md),
which covers both samples side by side.

## Before you ship anything modelled on this

Create a user who is **not** in the granted team, get a token, and call the API
directly:

```
GET /app-services/document-triage/teams/<granted-team>/board?tag_id=<tag>   as the outside user
expected 403 — a 200 means your data is readable by the whole realm
```

Repeat for a user who *is* in a team that was never granted the app. Forgetting
the entitlement check produces no error and no log line, because from inside a
granted team everything looks correct. That test is the only way to see it.

Note that Knowledge Flow would still refuse an unauthorized user's *documents*
even if the entitlement check were missing — but it would not stop them opening
the application or seeing which folders exist. The two gates cover different
things, which is why both are here.
