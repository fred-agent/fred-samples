# Sample application — document triage

A team reviews the documents in one of its folders: each one gets marked
**reviewed** or **needs work**, with a note. Agents help by reading the corpus
and proposing triage in chat — but only a person records a decision.

It exists to show the mechanics, not the business case. Everything here is
deliberately small enough to read in one sitting.

> **This is a first-party application backend.** It validates every caller's
> bearer against Keycloak and checks the team's `app:document-triage` grant
> through its own process-lifetime `RebacSdk`, straight against Fred's existing
> OpenFGA. It holds no shared agent key, and no credential of its own ever
> stands in for a caller: every Knowledge Flow call carries the user's bearer,
> forwarded verbatim — see [Credentials, and what they are not
> for](#credentials-and-what-they-are-not-for).

---

## What it demonstrates

| Concern | How |
| --- | --- |
| An application is its owner's images | `ui/` and `api/`, built and deployed independently |
| The frame holds no credential | the UI asks the host; the host attaches the bearer |
| The service authorizes itself | the backend validates the bearer and checks OpenFGA; the gateway does neither |
| The Control Plane is not an authorization service | no entitlement request per call; the backend reads the grant directly |
| A sample never owns Fred's model | the ReBAC config is reader-only: no store creation, no schema sync |
| Two independent gates | ReBAC gates *user + team + app*; Knowledge Flow gates *user + document* |
| Agents don't write shared state | the agent proposes; the human commits under their own identity |
| The app stores nothing | records live in the team's workspace, not in a database the app owns |

## The two pieces

```
ui/    static page, served at /apps/document-triage/
       index.html, package.json, package-lock.json, Dockerfile
api/   FastAPI service, reached at /app-services/document-triage/
```

The page has no framework and no bundler, but it is not hand-styled either. Its
palette and its side of the Fred protocol are installed from Fred's published
packages, `@fred-oss/design-tokens` and `@fred-oss/iframe-sdk`, pinned in
`ui/package.json`. The image build installs them and copies out two files: a
stylesheet and a self-contained ES module. So the frame tracks Fred's own
colours in both themes, follows the theme the user actually chose rather than
guessing at it from the operating system, and speaks the protocol through Fred's
client rather than a copy of it, which is also what gives it strict origin
checking on every message. Building the UI image therefore needs the npm registry; running it does
not.

There is no `capability/` directory. This application ships no Python
capability package and no `fred.capabilities` entry point, so nothing has to be
installed into the agents image for it.

Every path and identifier is derived from a single `app_id`. To turn this into
a different application, copy the directory and change `APP_ID`, every
occurrence of the app prefix in `ui/Dockerfile` — the mkdir, the three copy
destinations and the location blocks — and the names in `deploy.yaml`. In
`ui/index.html`, change the `<base href>` and the `applicationId` given to the
client: the first is what makes the asset URLs resolve under the new prefix,
and the client refuses to connect when the host's context names a different
application than the one it was given.

## How it fits together

```
browser ──postMessage──> Fred host ──bearer──> gateway ──bearer──> api/
                                                                    │
                                        Keycloak (validate bearer) ─┤
                                        OpenFGA  (app grant check) ─┤
                                                                    │
                                            caller's own bearer ────┤
                                                                    ▼
                                                            Knowledge Flow
                                                       (documents + workspace)
```

The gateway routes the iframe request but authorizes nothing. The backend
proves who the caller is, checks that their team holds the application grant,
and only then forwards the caller's own bearer to Knowledge Flow, which decides
per user which documents and paths they may touch. Neither gate implies the
other.

**Where the records live.** One JSON file per document, under the team's shared
workspace:

```
teams/{team}/shared/triage/{slug}.json
```

`{slug}` is derived from the document name plus a short digest of its uid — so
two documents with the same display name stay apart, and the uid (an internal
working identifier) never becomes a file name. One file per document also means
two people triaging different documents never contend.

## Configuration

All of it comes from the environment. The API reads it at startup and refuses
to start if a required value is missing.

| Variable | Required | Purpose |
| --- | --- | --- |
| `APP_ID` | no (`document-triage`) | the id checked as `app:<APP_ID>`; also the gateway and catalog id |
| `KNOWLEDGE_FLOW_BASE` | for every data route | Knowledge Flow root, e.g. `http://knowledge-flow-backend.<ns>.svc.cluster.local:8000/knowledge-flow/v1` |
| `KEYCLOAK_REALM_URL` | yes | exact `iss` of the incoming bearer; also used for JWKS discovery |
| `KEYCLOAK_USER_AUDIENCE` | yes | exact value the bearer must carry in `aud`: `app`, Fred's login client |
| `KEYCLOAK_M2M_CLIENT_ID` | yes | this backend's own confidential client |
| `DOCUMENT_TRIAGE_M2M_CLIENT_SECRET` | yes | that client's secret, from the `document-triage-security` Secret |
| `KEYCLOAK_M2M_REALM_URL` | no (the user realm) | realm a workload token is minted at, when it is not the browser's |
| `OPENFGA_API_URL` | yes | Fred's existing OpenFGA |
| `OPENFGA_STORE_NAME` | no (`fred`) | the store the Control Plane owns |
| `OPENFGA_AUTHORIZATION_MODEL_ID` | no | pin an existing model instead of resolving the latest |
| `DOCUMENT_TRIAGE_OPENFGA_API_TOKEN` | yes | this backend's independently revocable OpenFGA token |
| `FRED_DELEGATION` | no (off) | JSON delegation block; leave it unset — see [Credentials, and what they are not for](#credentials-and-what-they-are-not-for) |

`KNOWLEDGE_FLOW_BASE` is the one value that is not checked at startup: without
it the data routes answer `503`, so a misconfigured deployment fails closed on
the affected routes rather than at boot.

The API assembles its whole profile with fred-core's
`security_configuration_from_env`, which pins `create_store_if_needed=false` and
`sync_schema_on_init=false`. That is not a tuning choice. The Control Plane owns
the shared store and the authorization model, and a sample that published its
own model into that store would take them over. The SDK refuses to build with
either setting enabled.

The dependency is `fred-core>=4.4.0,<5`, so the image build needs a package
index that provides it.

## Credentials, and what they are not for

The backend holds two of its own: a Keycloak M2M client secret and an OpenFGA
API token, both in the `document-triage-security` Secret in this namespace.

| Credential | What it is for | What it is **not** for |
| --- | --- | --- |
| M2M client secret | this process's own revocable identity, required by the `c3` first-party profile | validating callers; it never substitutes for a bearer |
| OpenFGA API token | reading the team's application grant, independently revocable | writing tuples or the authorization model |

Neither is mounted into an agent pod, and neither is used on an outbound
Knowledge Flow call — those carry the caller's bearer. So a compromised
application backend cannot read a document its callers could not read.

**Leave `FRED_DELEGATION` unset.** With `accept_delegated_calls` on, a workload
holding the delegation caller role may present a grant naming a person, and
the backend then checks *that person's* entitlement while still forwarding the
*workload's* bearer to Knowledge Flow — the two gates stop describing the same
principal, and the sentence above stops holding. This application has no route an agent calls, so it needs nothing from
delegation.

**Agents still cannot write the board, and that is deliberate.** Agents may read
team-shared files but may only *mutate* inside their own subtree — a write into
`shared/` is rejected outright, on the stated doctrine that **agents never
share**. An application whose agents wrote shared state would be
routing around that boundary. So the agent proposes and the person commits: the
record is backed by an authenticated request from someone who looked at the
document, rather than by an agent asserting its own output.

The sibling [progress-tracker](../progress-tracker/README.md) sample lets agents
write, through MCP tools that run under the signed-in user's own bearer rather
than a shared agent key. This application mounts no MCP tools at all.

## Run the API on its own

The API builds its `RebacSdk` during startup, so it needs real first-party
security settings even for a local run — and a reachable OpenFGA that already
holds the store, because a reader never creates one. Otherwise the process
fails to start rather than serving a degraded API:

```bash
cd apps/document-triage/api
uv venv && uv pip install -r requirements.txt

export KEYCLOAK_REALM_URL=https://fred.example.com/realms/fred
export KEYCLOAK_USER_AUDIENCE=app
export KEYCLOAK_M2M_CLIENT_ID=document-triage-service
export DOCUMENT_TRIAGE_M2M_CLIENT_SECRET='<dedicated-client-secret>'
export OPENFGA_API_URL=http://127.0.0.1:9080
export OPENFGA_STORE_NAME=fred
export DOCUMENT_TRIAGE_OPENFGA_API_TOKEN='<dedicated-openfga-token>'
export KNOWLEDGE_FLOW_BASE=http://127.0.0.1:8111/knowledge-flow/v1

.venv/bin/uvicorn app:app --port 8000
```

```bash
curl -i http://127.0.0.1:8000/healthz                      # 200
curl -i http://127.0.0.1:8000/teams/demo/folders           # 401, no bearer
```

With a valid bearer whose team lacks the grant it is a `403`; entitled but with
no `KNOWLEDGE_FLOW_BASE` it is a `503`. Every failure closes. Any call carrying a
bearer also needs the realm's JWKS endpoint reachable from the process.

`tests/` holds the offline checks: that this application hands the shared
assembly the two credential variable names that are its own, and that it reads
its delegation block. From the repository root, with the API requirements and
pytest installed:

```bash
python -m pytest apps/document-triage/tests
```

The **UI cannot run standalone**. It holds no API address and makes no `fetch`
call: every request goes to the host frame over `postMessage`, so outside Fred
it stops at "connecting…". That is the design, not a gap.

## The agent side

There is nothing to install. This application ships no capability package, so
the agents image needs no dependency on it and the pod has no entry point to
discover.

Agents reach the corpus and the team workspace through Fred's own platform
capabilities, whose adapters keep the runtime's token private. They can read
what the team has decided and propose triage in chat; they cannot record one.
The "Ask an agent" button in the frame only opens Fred chat — no board state
travels through it.

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
    - app_id: document-triage
      ui_prefix: /apps/document-triage      # Fred-served UI: exactly /apps/<app_id>
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
      "app_id": "document-triage",
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

### Put the audience in the browser token

`KEYCLOAK_USER_AUDIENCE` is checked strictly: the access token Fred forwards
must carry it in `aud`. Set it to `app`, the audience of Fred's login client.
For a realm whose browser tokens lack `app` in `aud`, the audience mapper
commands are in the sibling sample's
[audience section](../review-board/README.md#4-put-the-audience-in-the-browser-token).
Sign out and back in after adding it: tokens minted before the mapper keep their
old `aud`. Without it the frame shows `Could not list folders: HTTP 401` and
the API logs a generic invalid-token error; the audience-specific line is debug
level, so raise `LOG_LEVEL` to see which check refused the token.

### Grant the application to a team

A platform administrator grants `app__document-triage` to a team. Registration
alone grants nothing. The backend's SDK checks the typed form `app:document-triage`;
`app__document-triage` is the catalog/admin id for the same thing.

## Deploy

Provision the backend's own Keycloak client and OpenFGA token first, then create
the Secret the manifest references:

```bash
kubectl create namespace document-triage --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic document-triage-security -n document-triage \
  --from-literal=m2m-client-secret="$DOCUMENT_TRIAGE_M2M_CLIENT_SECRET" \
  --from-literal=openfga-api-token="$DOCUMENT_TRIAGE_OPENFGA_API_TOKEN"
```

```bash
docker build -t document-triage-ui:sample  apps/document-triage/ui
docker build -t document-triage-api:sample apps/document-triage/api
kubectl apply -f apps/document-triage/deploy.yaml
```

Edit the values marked `EDIT` in `deploy.yaml` first. There is still no volume
to provision. Do **not** copy this Secret into the Fred or agents namespace.

The full walkthrough of building, registering and verifying a Fred application
— including the troubleshooting table — is [../DEPLOYMENT.md](../DEPLOYMENT.md).

## Before you ship anything modelled on this

Create a user who is **not** in the granted team, get a token, and call the API
directly:

```
GET /app-services/document-triage/teams/<granted-team>/board?tag_id=<tag>   as the outside user
expected 403 — a 200 means your data is readable by the whole realm
```

Repeat for a user who *is* in a team that was never granted the app, and once
more with no `Authorization` header at all — that one must be a `401`.
Forgetting the entitlement check produces no error and no log line, because from
inside a granted team everything looks correct. That test is the only way to see
it.

Note that Knowledge Flow would still refuse an unauthorized user's *documents*
even if the entitlement check were missing — but it would not stop them opening
the application or seeing which folders exist. The two gates cover different
things, which is why both are here.
