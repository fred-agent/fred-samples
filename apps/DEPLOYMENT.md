# Building and deploying a Fred application

A step-by-step walkthrough covering the three sample applications in this
directory.

Use it two ways: to deploy any of the samples, or as the template for your own
application by substituting your `app_id` throughout.

---

## The three samples

They solve the same shape of problem and differ in one decision — **who writes
the durable record.** That single choice decides whether the application needs a
store of its own and whether it offers agents any tools at all.

| | [`apps/document-triage/`](document-triage/) | [`apps/progress-tracker/`](progress-tracker/) | [`apps/review-board/`](review-board/) |
| --- | --- | --- | --- |
| `app_id` | `document-triage` | `progress-tracker` | `review-board` |
| What it does | review a folder of documents, mark each reviewed / needs-work | track long-running tasks and the decisions taken along the way | run a corpus document through specialist agents and watch each stage land |
| Who writes | **the human**, under their own bearer | **agents**, through the app's own tools | **agents**, through the app's own tools |
| Storage | the team's Knowledge Flow workspace | SQLite owned by the app | OpenSearch indices owned by the app |
| Secret | `document-triage-security` | `progress-tracker-security` | `review-board-security` |
| Volume | none — the API is stateless | `emptyDir` (or a PVC) | none — OpenSearch is external |
| Agent tools | none | `/mcp`, granted as `mcp-progress-tracker` | `/mcp`, granted as `mcp-review-board` |
| Copy it when | agents inform a decision a person records | agents advance a record the app owns | several agents advance one record in stages |

**Start from `document-triage` unless you know you need agents to write.**
Agents may read team-shared files but may only *mutate* inside their own
subtree, so an application whose agents advance shared state has to keep that
state in a store it owns and admit every write through its own API. The other
two show how that is done; `document-triage` shows how to not need it.

Every sample is a first-party backend: it validates the caller's bearer against
Keycloak itself and reads the team's grant straight from Fred's existing
OpenFGA. None of them holds a shared agent key.

---

## What you are building

Two container images that Fred does not build, plus an optional tool mount on
the API you already wrote:

| Piece | What it is | Where Fred meets it |
| --- | --- | --- |
| UI image | static bundle in any web server | `/apps/<app_id>/`, rendered in a frame |
| API image | any HTTP service, any language | `/app-services/<app_id>/` |
| Tool mount | routes the API already serves, offered over MCP at `/mcp` | catalogued as an MCP server the agent runtime calls |

No sample ships a Python capability package: nothing here is installed into the
agents image and the runtime imports no application code. The tool mount is only
needed if agents must read or advance your data — `document-triage` has none. A
UI-only application needs neither it nor the API.

Throughout, `<app>` stands for the sample folder you are deploying —
`document-triage`, `progress-tracker` or `review-board` — which is also its
`app_id`.

---

## Prerequisites

- A running Fred deployment with `enableApplications: true`
- Platform-admin access, to grant the application to a team
- Somewhere the cluster can pull images from — a registry, or `k3d image import`
  for local work
- An enabled confidential Keycloak client for this application, and Fred's
  login-client audience `app` in the browser token's `aud` — a first-party
  backend verifies that claim strictly (see
  [review-board's audience section](review-board/README.md#4-put-the-audience-in-the-browser-token))
- An OpenFGA API token with read access to Fred's existing store. The backend
  reads the shared authorization model; it never creates a store or syncs a
  schema
- For `document-triage` and `review-board`: a reachable Knowledge Flow with
  documents ingested
- For `review-board`: an OpenSearch the API pod can reach, with credentials that
  stay in the application's namespace

Startup fails fast when any of these is missing, rather than turning up as a
refused call later.

> If your Knowledge Flow runs with authentication relaxed for development, the
> outsider check in Step 10 passes for the wrong reason and proves nothing. Run
> it against an instance that enforces auth.

---

## Step 1 — Write the UI

A static page. Two things about it are not style choices; both cost real
debugging time when missed.

**Serve the bare prefix.** The iframe `src` is `/apps/<app_id>` with **no
trailing slash**, because the control plane normalises it that way. A server
that only handles the directory form will redirect, and see the next point.

**Turn off absolute redirects.** Fred forwards `Location` verbatim. An absolute
redirect carries *your container's* hostname, which the browser cannot resolve.

**Send `Cache-Control: no-store` for the entry point.** Otherwise the browser
keeps serving the previous build from cache, and a deployment that is correct on
the server still shows the old page — including the old bug you just fixed.

`ui/Dockerfile`:

```dockerfile
FROM nginx:1.27-alpine
RUN mkdir -p /usr/share/nginx/html/apps/<app>
COPY index.html /usr/share/nginx/html/apps/<app>/index.html
RUN printf '%s\n' \
  'server {' \
  '  listen 80;' \
  '  root /usr/share/nginx/html;' \
  '  absolute_redirect off;' \
  '  location = /apps/<app> { add_header Cache-Control "no-store"; try_files /apps/<app>/index.html =404; }' \
  '  location /apps/<app>/ {' \
  '    add_header Cache-Control "no-store";' \
  '    try_files $uri $uri/ /apps/<app>/index.html;' \
  '  }' \
  '  location = /healthz { return 200 "ok"; add_header content-type text/plain; }' \
  '}' > /etc/nginx/conf.d/default.conf
```

That is the minimum. A page serving more than one file needs its own rules for
them: `review-board` adds a location block giving its `.mjs` modules a
JavaScript media type, without which the browser refuses to execute them.

Every sample here also installs Fred's frontend packages rather than carrying
copies of them, which adds a Node stage ahead of nginx. All three pin
`@fred-oss/design-tokens` and `@fred-oss/iframe-sdk`, and copy out a stylesheet
and a self-contained ES module: the palette and the frame's side of the host
protocol. `review-board` takes the typeface with the palette as well, and keeps
a thin class of its own around the client for the request bookkeeping its
interface depends on.

No bundler is involved and the `docker build` command is unchanged, but the
build now needs the npm registry. Exclude the installed files from the page
fallback, as all three do, so that a missing one answers 404 instead of quietly
serving the page under its name and failing unstyled.

Build your bundle with `/apps/<app_id>/` as its base path — Fred forwards the
whole prefix upstream, so the absolute asset URLs your bundler bakes in resolve
back through the same route.

---

## Step 2 — Speak the frame protocol

Your page holds no token and names no upstream. It announces itself, receives
context, and asks the host for everything else.

```js
const PROTOCOL_VERSION = "1";

// Announce LAST, after the listener is installed: the host replies with
// fred:context, which is what starts your first load.
parent.postMessage({ type: "fred:ready", protocolVersion: PROTOCOL_VERSION }, "*");

window.addEventListener("message", (event) => {
  const m = event.data;
  if (!m || typeof m !== "object") return;
  if (m.type === "fred:context") {
    const { team, locale } = m.context;   // who is looking at this
    load();
  }
});
```

To reach your own API, ask the host. **The host already prefixes
`/app-services/<app_id>/teams/<team_id>`**, so send only the path below that
root — never repeat the team segment:

```js
// correct
hostFetch("tasks");
// wrong -> /teams/<id>/teams/<id>/tasks -> 404
hostFetch(`teams/${teamId}/tasks`);
```

```js
function hostFetch(path, init = {}) {
  const requestId = `r${++seq}`;          // fresh id per request, never reused
  parent.postMessage({
    type: "fred:request", requestId, path,
    method: init.method || "GET",
    headers: init.body ? { "content-type": "application/json" } : {},
    body: init.body || null,
  }, "*");
  // resolve when fred:response arrives with this requestId
}
```

Never reach outside this channel — no parent DOM, no globals, no shared build.
They work only while the frame is same-origin, and stop the day it is not.

There is **no push channel**. Progress made elsewhere reaches your screen by
polling.

---

## Step 3 — Write the API

Fred strips `/app-services/<app_id>` before proxying, so your service sees
`/teams/<team_id>/...`.

**The gateway authorizes nothing.** It checks only that the `app_id` is
registered and has an upstream, then forwards the caller's `Authorization`
header untouched. Any authenticated user in the realm reaches you. Your service
is the only thing standing between them and your data.

**The Control Plane is not an authorization service.** Read the grant where it
lives instead: one ReBAC check covers both membership and grant, because grants
are team to capability, and no request leaves the pod per call.

```python
def _security_configuration() -> SecurityConfiguration:
    # Only the two variable names that are this application's own; every
    # shared rule lives in the library rather than a copy per application.
    return security_configuration_from_env(
        m2m_secret_env="MY_APP_M2M_CLIENT_SECRET",  # pragma: allowlist secret
        openfga_token_env="MY_APP_OPENFGA_API_TOKEN",
        delegation_env="FRED_DELEGATION",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Process-lifetime, and a failure here fails startup rather than the
    # first request.
    sdk = await rebac_sdk_factory(_security_configuration(), kpi_writer=KPI)
    try:
        app.state.rebac_sdk = sdk
        yield
    finally:
        await sdk.close()


async def require_entitled(team_id: str, user: CurrentUser, rebac: Rebac) -> str:
    await rebac.check_application_access(user, team_id=team_id, app_id=APP_ID)
    return team_id
```

`register_exception_handlers(app)` turns the refusal into the response. **A
403 is a decision about the caller; a 5xx is not.** When the authorization
dependency cannot be consulted the request still fails closed, but nothing was
decided, so the failure surfaces as a server error rather than a refusal. Do not
collapse the two; a client that retries an outage must not retry a refusal.
With delegation on, account-status enforcement adds a narrower signal: `403`
`account_suspended`, or `503` `account_status_unavailable` when the check cannot
be answered, each with an `X-Fred-Denial-Cause` header.

Use the **caller's** identity for the decision, never a service credential. The
M2M client is this process's own outbound identity and validates nobody.

`api/Dockerfile` — note the numeric user:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
# Numeric, not a name: a cluster enforcing runAsNonRoot cannot verify a named
# user and refuses to start the container.
RUN useradd --uid 10001 --create-home appuser
USER 10001
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Step 4 — Build the images

From the repository root, with `<app>` the sample folder name:

```bash
docker build -t <app>-ui:sample  apps/<app>/ui
docker build -t <app>-api:sample apps/<app>/api
```

Concretely, for one of the samples:

```bash
docker build -t document-triage-ui:sample    apps/document-triage/ui
docker build -t document-triage-api:sample   apps/document-triage/api
# or
docker build -t progress-tracker-ui:sample   apps/progress-tracker/ui
docker build -t progress-tracker-api:sample  apps/progress-tracker/api
# or
docker build -t review-board-ui:sample       apps/review-board/ui
docker build -t review-board-api:sample      apps/review-board/api
```

## Step 5 — Get them where the cluster can pull

Local k3d — the cluster runs its own containerd and cannot see your Docker
daemon's images:

```bash
k3d image import <app>-ui:sample <app>-api:sample -c fred
```

Anywhere else, push to a registry the nodes can reach. **Image imports do not
survive cluster recreation** — re-import after any `k3d cluster delete`.

## Step 6 — Deploy the workloads

Its own namespace. Fred reaches your services only by DNS name, so they can live
anywhere.

**The Secret comes first.** No sample manifest declares one — each references
`<app>-security` by `secretKeyRef`, so applying the manifest without it leaves
the API pod in `CreateContainerConfigError`. Create it out of band, in the
application's namespace, and never copy it into the Fred or agents namespace:

```bash
kubectl create namespace <app>
kubectl create secret generic <app>-security -n <app> \
  --from-literal=m2m-client-secret="$M2M_CLIENT_SECRET" \
  --from-literal=openfga-api-token="$OPENFGA_API_TOKEN"
```

`review-board` adds `opensearch-username` and `opensearch-password` to that
Secret; both references are optional, so omit them when OpenSearch has no basic
authentication. Each sample's README gives its own exact key names.

```bash
kubectl apply -f apps/<app>/deploy.yaml
```

Edit the addresses marked `EDIT` in that manifest first — they name your Fred
namespace and your realm, and nothing else in the file is cluster-specific.

**For `document-triage` and `review-board`, that is the whole step.** Neither
provisions storage: triage records land in the team's Knowledge Flow workspace
written with the caller's own bearer, and review-board's indices live in an
OpenSearch it does not deploy.

**For `progress-tracker`, one more thing.** Its records live in SQLite on an
`emptyDir`, so there is no database to provision — and no records after a pod
restart; point `PROGRESS_TRACKER_DB` at a PersistentVolumeClaim when you want
them to outlive one.

### The agent half — a tool server on the routes you already wrote

Only needed if agents must read or advance your records. An application offers
its own routes as a tool server rather than shipping a package into the agents
image, so nothing here edits Fred's own Dockerfile or its dependency list, and
the runtime imports no application code.

Tag the routes that become tools. Tagging is the whole allowlist: an untagged
route stays reachable by the UI and invisible to an agent. Build the mount after
the routes, because the tool list is snapshotted from the OpenAPI schema at
construction, and serve it at `/mcp`. The mount is `DelegatedFastApiMCP` from
`fred_core.security.mcp_delegation_fastapi`, behind fred-core's optional `mcp`
extra — a service that mounts no tools does not carry it. Declare the grant
parameters as an application-wide dependency; without that a tool whose route
takes no body silently acts for nobody.

Catalog that endpoint in the agent runtime's `mcp_catalog.yaml` with
`auth_mode: user_token`, or `delegated` where the runtime acts for people, and
keep `team_scope: admin_gated` so activation stays an explicit administrator
decision. **The catalog id becomes a team capability in its own right** —
`mcp-<app_id>` — granted separately from `app__<app_id>`.
That separation is the point: a team can hold the application, so its people see
the page, while no agent may touch the record. Set the backend's
`MCP_SERVER_ID` to that exact id, because the tool routes check that grant
directly.

A graph agent additionally has to declare that it offers capabilities, because
graph definitions do not by default; without that its tool server is never
advertised and the agent can never be enabled for a team.

**A tool call carries the identity of the person driving it,** and re-enters the
same team and application admission the interface uses, so an agent reads and
writes exactly what that person can. On an interactive turn Fred's MCP client
sends the caller's own bearer. Where a run continues without a person present,
a workload credential holding the delegation caller role carries a grant
naming the person, the run and the agent, and the mount resolves that grant to
an asserted identity — verified at the mount, never read from the tool
arguments, so a model cannot name a different subject. There is no service-key
fallback and no shared key: an application authenticates a caller, never a
peer service.

**Identity comes from the runtime, never from a tool argument.** A model that
supplies a team or a subject is supplying display metadata; the platform
replaces a team argument with the active collaborative team before the tool ever
sees it. That is attribution you get for free rather than by trusting the model
to report itself honestly.

### Showing conversation history in your application

Your service already holds the caller's bearer, so it can read back the
conversations linked to a record:

```
GET {RUNTIME_BASE}/agents/sessions/{session_id}/messages
Authorization: Bearer <the caller's own token>
```

Forward the **caller's** token, never a service credential. The runtime returns
only rows belonging to the authenticated user, and an empty list for anyone
else's session — indistinguishable from a session that does not exist. That is
what makes this safe to expose in a shared application: the view is per-viewer
by construction, and a teammate's transcript stays unreadable even though the
record itself is shared.

You cannot route the user wherever you like: `fred:navigate` is resolved against
your own base path and an escaping path is dropped silently. To hand a record to
a conversation, send `fred:open-chat`. It carries no destination — the host
chooses one — and an optional session id is honoured only when it matches one of
the viewer's own conversations, so at worst it opens a fresh chat.

**Deferred intent is the way around it that needs no contract change.** Instead
of navigating, record what the user intends and let the agent side pick it up:

1. "Discuss in chat" POSTs a short-lived pending pin — one per team and user,
   keyed on the caller's own subject, with a TTL so a forgotten click cannot
   hijack a conversation hours later.
2. The user opens a chat themselves and just starts talking.
3. On the first tool call of a session with no task yet, the tool claims the pin
   and links the session.

The claim runs inside one write transaction, so two sessions starting at once
cannot both take the pin: the second waits, finds it gone, and reports no pin
rather than double-claiming. The user's identity comes from the runtime on the
agent side and from the bearer on the app side — the model never names a task or
a session, so a prompt cannot redirect the pin to someone else's work.

## Step 7 — Register it, in both halves

This is where most first deployments fail. Two places, one `app_id`, and
**nothing cross-checks them**. An application with agent tools has a third
registration as well — its MCP catalog entry, covered in Step 6.

**Half 1 — the catalog** (control-plane values). Owns what teams see and the
capability that authorization is granted against. No proxy upstream here:

```yaml
platform:
  frontend:
    feature_flags:
      enableApplications: true
  application_sources:
    - app_id: document-triage
      ui_prefix: /apps/document-triage       # must be exactly /apps/<app_id>
      version: 0.1.0
      icon: checklist
      display_name:
        en: "Document Triage"
      description:
        en: "Review a folder of documents and record what was decided."
      enabled: true
    - app_id: progress-tracker
      ui_prefix: /apps/progress-tracker
      version: 0.1.0
      icon: checklist
      display_name:
        en: "Progress Tracker"
      description:
        en: "Track long-running work and the decisions taken along the way."
      enabled: true
    - app_id: review-board
      ui_prefix: /apps/review-board
      version: 0.1.0
      icon: checklist
      display_name:
        en: "Review Board"
      description:
        en: "Watch several agents report progress on one task."
      enabled: true
```

**Half 2 — the routes** (frontend container env). Owns the server-side
addresses:

```yaml
env:
  - name: FRONTEND_APPLICATIONS_JSON
    value: |
      [
        {
          "app_id": "document-triage",
          "ui_upstream": "http://document-triage-ui.document-triage.svc.cluster.local:80",
          "service_upstream": "http://document-triage-api.document-triage.svc.cluster.local:8000",
          "service_required": true
        },
        {
          "app_id": "progress-tracker",
          "ui_upstream": "http://progress-tracker-ui.progress-tracker.svc.cluster.local:80",
          "service_upstream": "http://progress-tracker-api.progress-tracker.svc.cluster.local:8000",
          "service_required": true
        },
        {
          "app_id": "review-board",
          "ui_upstream": "http://review-board-ui.review-board.svc.cluster.local:80",
          "service_upstream": "http://review-board-api.review-board.svc.cluster.local:8000",
          "service_required": true
        }
      ]
```

Each sample's `deploy.yaml` puts its workloads in a namespace named after the
sample folder, which is why `<app>-ui.<app>.svc.cluster.local` is the pattern
above. List only the applications you actually deployed — **keep the gateway
list a subset of the catalog**, or routes serve for an app nobody was granted.

**Both upstreams must be fully qualified.** Fred proxies applications through a
variable `proxy_pass`, which makes nginx resolve the host at request time
through its own resolver — and that path does not apply the pod's DNS search
list. A bare Service name yields `could not be resolved` and a 502, while the
same name works from a shell in the very same pod.

Both halves are edits to your Fred deployment's own values, not to anything in
this repository. Run these in order.

**1. Dump the current values and check them against the live cluster.** Helm
applies what the values say, so anything present on the cluster but missing from
the values is removed by the upgrade.

```bash
helm get values <release> -n <ns> -o yaml > current-values.yaml

kubectl get cm <release>-control-plane-backend-back -n <ns> \
  -o jsonpath='{.data.configuration\.yaml}' | grep app_id
kubectl get deploy frontend -n <ns> \
  -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="FRONTEND_APPLICATIONS_JSON")].value}'
kubectl get deploy fred-agents -n <ns> \
  -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}'
```

**2. Edit `current-values.yaml`** so it contains everything the cluster has plus
your new application:

- `applications.control-plane-backend.configuration.platform.application_sources`
  — one entry per app, including any the cluster has that the values lack
- `applications.frontend.env[].FRONTEND_APPLICATIONS_JSON` — one route per app,
  every upstream `<svc>.<namespace>.svc.cluster.local`
- any `*_API_BASE` on `applications.fred-agents.env` — set to the namespace the
  Service is actually in

**3. Upgrade.**

```bash
helm upgrade <release> <your-fred-chart> -n <ns> -f current-values.yaml --wait
```

If it fails with `conflict with "kubectl-set"` or `"kubectl-client-side-apply"`,
the field is owned by a hand-patch. Re-run with `--force-conflicts`, which
applies the values file's value — only correct once step 2 is done.

```bash
helm upgrade <release> <your-fred-chart> -n <ns> -f current-values.yaml \
  --force-conflicts --wait
```

**4. Roll both pods.**

```bash
kubectl rollout restart deployment/frontend deployment/control-plane-backend -n <ns>
kubectl rollout status deployment/control-plane-backend -n <ns> --timeout=150s
```

**5. Confirm the running pod has the new config.** The control plane reads its
ConfigMap once at startup, and ConfigMap volumes refresh on the kubelet's sync
cycle — so a pod can start on the previous file and keep it.

```bash
kubectl exec -n <ns> deploy/control-plane-backend -- \
  grep -c <app_id> /app/config/configuration.yaml
```

`0` means stale: repeat step 4 and re-check. A non-zero count is what makes the
capability appear on the Capabilities page.

**6. Check every app answers through the gateway.**

```bash
kubectl exec -n <ns> deploy/frontend -- \
  wget -qS -O /dev/null http://localhost/apps/<app_id>    # 200
kubectl exec -n <ns> deploy/frontend -- \
  wget -qS -O /dev/null http://localhost/apps/not-real    # 404
```

## Step 8 — Grant it to a team

Registration grants nothing. A platform admin enables `app__<app>` for each
collaborative team on the Capabilities page, or:

```bash
curl -X PUT -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"enabled":true}' \
  "$FRED/control-plane/v1/admin/capabilities/app__<app>/teams/$TEAM_ID"
```

That is `app__document-triage`, `app__progress-tracker` or `app__review-board` —
the `app__` prefix plus the `app_id`, which is also the sample folder name.

**A sample with agent tools needs a second grant.** `mcp-progress-tracker` and
`mcp-review-board` are separate capabilities, granted the same way, and the
application grant does not imply them. `document-triage` has neither.

## Step 9 — Verify, in this order

Each check isolates one layer, so a failure tells you where to look.

```bash
# 1. the UI image, through Fred — note NO trailing slash
curl -o /dev/null -w "%{http_code}\n" $FRED/apps/<app>                       # 200

# 2. fail-closed on an unknown id
curl -o /dev/null -w "%{http_code}\n" $FRED/apps/not-real                    # 404

# 3. the catalog sees it for this team
curl -H "Authorization: Bearer $TOKEN" \
  $FRED/control-plane/v1/teams/$TEAM_ID/applications                         # your app listed

# 4. the API, through Fred — each sample's own first read
curl -H "Authorization: Bearer $TOKEN" \
  $FRED/app-services/document-triage/teams/$TEAM_ID/folders                  # 200
curl -H "Authorization: Bearer $TOKEN" \
  $FRED/app-services/progress-tracker/teams/$TEAM_ID/tasks                   # 200
curl -H "Authorization: Bearer $TOKEN" \
  $FRED/app-services/review-board/teams/$TEAM_ID/tasks                       # 200

# 5. open it in Fred — the frame should render
```

## Step 10 — Test with someone who should not have access

**Required, not optional.** Forgetting the entitlement check produces no error,
no warning and no log line: you are in a granted team, so every check you
naturally perform passes. The gap is visible only from outside.

```bash
# a user NOT in the granted team
curl -H "Authorization: Bearer $OUTSIDER" \
  $FRED/app-services/document-triage/teams/$TEAM_ID/folders    # expect 403
curl -H "Authorization: Bearer $OUTSIDER" \
  $FRED/app-services/progress-tracker/teams/$TEAM_ID/tasks     # expect 403
curl -H "Authorization: Bearer $OUTSIDER" \
  $FRED/app-services/review-board/teams/$TEAM_ID/tasks         # expect 403
```

A 5xx here is not a pass. It means the authorization dependency could not be
consulted, so nothing was decided about this caller — fix the outage and run the
check again.

Repeat for a user who *is* in a team whose team was never granted the app —
that case is the one most often missed.

Before trusting a green result, confirm your negative user is actually
negative: a capability left `default_on` makes every team entitled, so the test
passes for the wrong reason.

---

## Troubleshooting

Symptom, cause, fix.

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Could not load tasks: HTTP 404` in the frame | The UI repeated the team segment; the host already prefixes `/teams/<id>` | Send paths relative to that root — `hostFetch("tasks")` |
| A fixed bug persists in the frame, but `curl` through Fred shows the fix | Browser cached the old entry point | `Cache-Control: no-store` on the HTML; hard-reload once to clear what is already cached |
| Frame shows “did not respond” after ~15s | UI server 301s the bare prefix with an absolute `Location` carrying the container's host | `absolute_redirect off` + serve the bare prefix directly. Browsers cache the 301, so re-fetch that URL after fixing |
| 502, log says `could not be resolved` | Bare Service name in `FRONTEND_APPLICATIONS_JSON` | Use `<svc>.<namespace>.svc.cluster.local` |
| Pod `CreateContainerConfigError`, “cannot verify user is non-root” | Image ends `USER <name>` under `runAsNonRoot` | Use a numeric `USER`, or set `runAsUser` |
| Pod `CreateContainerConfigError`, a `secretKeyRef` not found | The manifest declares no Secret; `<app>-security` was never created | Create it in the application's namespace before applying the manifest |
| Frame and tool calls all answer 401 `Invalid token` | The browser token lacks `app`, the API's `KEYCLOAK_USER_AUDIENCE`, in `aud` | Add the audience mapper ([review-board's audience section](review-board/README.md#4-put-the-audience-in-the-browser-token)), then sign in again |
| API answers 5xx on every request | Its OpenFGA or Keycloak endpoint is unreachable, so the authorization dependency never decided | Fix the endpoint. A 5xx is an outage, not a refusal — do not read it as a working deny |
| API 500s on every write, `attempt to write a readonly database` | The mounted volume is not writable by the image's uid | `fsGroup` on the pod matching the container `USER` (`deploy.yaml` sets `10001`) |
| Task records vanish after a restart | `emptyDir` lives and dies with the pod | Swap it for a PersistentVolumeClaim and repoint `PROGRESS_TRACKER_DB` |
| App in the catalog but frame 404s | Registered in the catalog half only | Add the gateway half |
| Routes serve for an app nobody was granted | Registered in the gateway half only | Keep the gateway list a subset of the catalog |
| `enabled: false` did not stop it | That withdraws it from the catalog, not from the gateway | Remove both halves to retire an application |
| Both halves are registered, the ConfigMap is correct, but **no capability to grant** appears on the Capabilities page | The control-plane pod restarted before the new ConfigMap reached the node, read the old file at startup, and holds it | `kubectl exec … -- grep -c <app_id> /app/config/configuration.yaml`. A `0` means stale — restart it again and re-check |
| `helm upgrade` fails: `conflict with "kubectl-set"` / `"kubectl-client-side-apply"` | Someone patched the release by hand; that field manager owns the field and Helm will not seize it | Reconcile the values to match live, then `--force-conflicts`. Never force first — it applies the values file's value over the working one |
| An application silently disappears after a `helm upgrade` | It was patched into the live cluster but never added to the values file | Diff `helm get values` against the live ConfigMap and Deployments before upgrading |
| A capability's tools all fail after a `helm upgrade`, with nothing in the logs | Its `*_API_BASE` reverted to a stale namespace whose Services no longer exist | Same cause as above — check the value on `deploy/fred-agents` and fold the live one into your values |
