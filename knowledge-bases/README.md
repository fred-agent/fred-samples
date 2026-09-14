# Knowledge Bases

A Knowledge Base tells Fred where a team's documents come from and how to keep
them up to date. You declare one with `fred_sdk.knowledge_base`: an identity,
the configuration a team fills in, and one async handler that reconciles the
source and reports what changed.

Three samples, from the smallest to the most demanding:

| Sample | Synchronizes | Read it for |
|---|---|---|
| [`local-folder/`](local-folder/) | a folder on the machine running it | the smallest believable implementation |
| [`git-repository/`](git-repository/) | a GitHub or GitLab branch | a pod that keeps **no state at all** |
| [`webdav/`](webdav/) | a folder published over WebDAV | an **exhaustive** source, so deletions are provable |

Each is a standalone package with its own venv. Start with `local-folder`.

---

## Every sample works the same way

Run these from the sample's own directory.

```bash
make dev          # install (resolves fred-sdk from the ../../../fred checkout)
make test         # offline, no network, no external service
make code-quality # ruff, bandit, detect-secrets, basedpyright
```

An image exposes exactly two commands, and the Makefile wraps both:

```bash
make publish      # deployment step: declare this KB to Fred, then exit
make run          # serve runs until stopped
```

Both read `config/.env`, which is **not** in git because it carries a secret.
Copy it before your first `publish` or `run`:

```bash
cp config/env.template config/.env
```

Without it, `make run` stops immediately and tells you exactly that.

---

## What `publish` and `run` actually do

Neither reads a YAML file. `fred_sdk.knowledge_base` reads the process
environment and nothing else — the Makefile sources `config/.env` and hands it
over. That is the whole configuration mechanism.

`make publish` sends the declaration to the Control Plane:

```
PUT $FRED_CONTROL_PLANE_URL/knowledge-bases/definitions/<definition id>
     { "prefix": $FRED_KB_PREFIX, ...the declared fields }
```

Idempotent, so every deployment of the image replays it and what Fred stores
stays what is deployed.

`make run` connects to `$FRED_TEMPORAL_HOST` and polls one Temporal task queue.
It opens no port of its own.

**Nothing names that queue — it is derived.** Both the pod and the Control Plane
compute it from the definition id with the same `fred_core` function, so the
dispatching side and the worker side cannot disagree by construction:

```
kb__ + fred.samples.webdav  →  kb__fred.samples.webdav
```

`kb__` is the catalog namespace Knowledge Bases reserve, so they never collide
with capabilities, agents or applications. The rest is the definition id, which
already carries the prefix its contributor owns — which is why two contributors
can never end up sharing a queue. Configuring a queue name would be the bug: a
pod and a Control Plane that disagreed about it would lose every run silently.

The first line `make run` prints tells you which queue it took:

```
INFO    Knowledge Base fred.samples.webdav serving runs on kb__fred.samples.webdav
```

A queue that does not match the definition id is a contract break, not a
setting to adjust.

Both authenticate the same way — Fred's own M2M mechanism
(`fred_core.security.backend_to_backend_auth`, the module the other backends
use, not a scheme of its own):

```
POST $FRED_KEYCLOAK_REALM_URL/protocol/openid-connect/token
     grant_type=client_credentials
     client_id=$FRED_KB_CLIENT_ID          → kb-fred.samples
     client_secret=$FRED_KB_CLIENT_SECRET  → read from the environment
```

then `Authorization: Bearer <token>` on every call. No token is ever written to
disk, and the SDK opens no file to find the secret.

### Why there is no `configuration_prod.yaml`

The agent pod has one because it has a lot to configure: an HTTP server and its
port, model and MCP catalogues, stores, security. Its `.env` carries two keys
and the YAML carries the rest.

A Knowledge Base pod has none of that — no inbound port, no store, no
catalogue, no model. Its entire configuration is these, all in `config/.env`:

| Variable | Needed by |
|---|---|
| `FRED_CONTROL_PLANE_URL` | both — **include the `/control-plane/v1` prefix** |
| `FRED_KB_PREFIX` | both — the dotted prefix this image owns |
| `FRED_KB_CLIENT_ID` | both |
| `FRED_KB_CLIENT_SECRET` | both |
| `FRED_KEYCLOAK_REALM_URL` | both |
| `FRED_TEMPORAL_HOST` | `run` only |
| `FRED_KNOWLEDGE_FLOW_URL` | optional — unset, a run logs instead of ingesting |
| `FRED_TEMPORAL_NAMESPACE` | optional — defaults to `default` |

That is the SDK's design, identical for every Knowledge Base, not a shortcut
taken by these samples.

Two failure modes worth recognising rather than debugging:
`FRED_CONTROL_PLANE_URL` without its `/control-plane/v1` prefix makes every call
404 against a healthy Control Plane; a missing `FRED_KB_CLIENT_SECRET` makes the
SDK log that it is calling Fred unauthenticated and carry on, after which every
call is rejected.

**For the local stack, the template already holds the right values**, dev client
secret included. `cp config/env.template config/.env` is enough — there is
nothing to edit before `make publish`.

---

## Trying one with no Fred at all

Every sample ships a developer tool that runs the real handler against a real
source and logs what it *would* publish. No Fred, no Temporal, no login.

```bash
# local-folder
make sync ROOT=/path/to/your/notes
make watch ROOT=/path/to/your/notes      # over and over

# git-repository
make sync REPO=ThalesGroup/fred SUBDIR=docs
make sync REPO=group/sub/project PROVIDER=gitlab

# webdav — ships its own share to run against
make share-run                           # Apache mod_dav on :8088, in a container
make sync URL=http://localhost:8088/dav/
make share-stop
```

**Run it twice.** The first run proves the source can be read; the second proves
the implementation knows what it already published — which is where a
synchronizer is usually wrong.

`make share-run SHARE_DIR=/path/to/documents` serves any folder you like.

---

## Against a real Fred

1. Bring up the stack — Keycloak, Postgres, OpenFGA, OpenSearch, Temporal — with
   docker compose in `~/Fred/fred-deployment-factory`.
2. Run its `make keycloak-post-install` once. It provisions `kb-fred.samples`,
   the confidential client every sample here publishes as.
3. `cp config/env.template config/.env` and check the URLs and the secret.
4. `make publish`, then `make run`.

**Fred claims a prefix for the first client that publishes under it, and there
is no rebinding path.** All three samples publish under `fred.samples`, so
publishing from some other convenient client does not burn one definition — it
burns the whole prefix.

### How far this actually goes today

| Step | State |
|---|---|
| `publish` puts the definition in front of an admin | works |
| enabling it for a team | works |
| writing documents into a real library | works — `webdav` only, via `make sync LIBRARY=<id>` |
| a **schedule** dispatching a run to `make run` | **not built yet** |

The workflow and activity adapter are unchecked tasks in the
`knowledge-base-sdk-contract` OpenSpec change. `make run` will sit there
correctly and forever, because nothing dispatches to its queue. Expect that
rather than debugging it.

---

## With the skills

Two skills in [`.claude/skills/`](../.claude/skills/), both collaborative: you
drive the UI and decide what to test, the assistant starts things and reports
what it sees.

| Skill | Ask it for |
|---|---|
| `webdav-share` | "serve ~/Fred/WebDav over WebDAV", then one synchronization run — into a real library if you name one |
| `live-knowledge-base-session` | publish and serve a pod against the live stack, watching the pod *and* the Control Plane together |

They compose: `webdav-share` supplies the source, `live-knowledge-base-session`
runs the pod that reads it.

---

## Logging, so you are not surprised

`fred_sdk.knowledge_base` configures logging with a plain
`logging.basicConfig(format="%(levelname)-7s %(message)s")` and wires none of
`fred_core`'s observability — no structured logs, no audit trail, no KPI sink.
A Knowledge Base pod therefore has **one unstructured stdout stream** where a
Fred application has three. That is the SDK's doing, identical for every sample
here, and it is a gap to close in `libs/fred-sdk` rather than in any one sample.
