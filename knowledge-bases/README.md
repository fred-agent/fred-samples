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

A pod is configured exactly the way every other Fred component is: one
`configuration.yaml` resolved from `$CONFIG_FILE`, the models `fred_core` owns,
and environment variables carrying **secrets only**.

```
config/configuration.yaml   knowledge_base:   prefix, control_plane_url, knowledge_flow_url
                            security.m2m:     realm_url, client_id, secret_env_var
                            scheduler.temporal: host, namespace

config/.env                 CONFIG_FILE, and the one secret the YAML names
```

`security.m2m` is parsed by `M2MSecurity` and `scheduler.temporal` by
`TemporalSchedulerConfig` — the same model the Control Plane parses for
Knowledge Base cadence, so both halves of the contract describe Temporal
identically.

`make publish` sends the declaration to the Control Plane:

```
PUT $CONFIG.knowledge_base.control_plane_url/knowledge-bases/definitions/<definition id>
     { "prefix": <knowledge_base.prefix>, ...the declared fields }
```

Idempotent, so every deployment of the image replays it and what Fred stores
stays what is deployed.

`make run` connects to `scheduler.temporal.host` and polls one Temporal task
queue. It opens no port of its own.

Both authenticate with Fred's own M2M mechanism
(`fred_core.security.backend_to_backend_auth`, the module the other backends
use, not a scheme of its own):

```
POST <security.m2m.realm_url>/protocol/openid-connect/token
     grant_type=client_credentials
     client_id=<security.m2m.client_id>
     client_secret=$<security.m2m.secret_env_var>
```

then `Authorization: Bearer <token>` on every call. **The configuration names
which variable holds the secret**; the value never appears in the YAML, and
nothing on disk ever holds it.

### The queue is derived, never configured

Both the pod and the Control Plane compute it from the definition id with the
same `fred_core` function, so the dispatching side and the worker side cannot
disagree by construction:

```
kb__ + fred.samples.webdav  →  kb__fred.samples.webdav
```

`kb__` is the catalog namespace Knowledge Bases reserve, so they never collide
with capabilities, agents or applications. The rest is the definition id, which
already carries the prefix its contributor owns — which is why two contributors
can never end up sharing a queue. `scheduler.temporal.task_queue` is therefore
read by nobody: configuring it would be the bug, since a pod and a Control
Plane that disagreed would lose every run silently.

The first line `make run` prints tells you which queue it took:

```
INFO    Knowledge Base fred.samples.webdav serving runs on kb__fred.samples.webdav
```

### Two failure modes worth recognising rather than debugging

- **`control_plane_url` without its `/control-plane/v1` prefix** makes every
  call 404 against a healthy Control Plane.
- **The named secret variable unset** — the token exchange fails at startup
  rather than at the first document, which is deliberate: a Knowledge Base acts
  as a workload and Fred admits it as nothing else.

`security.user` is deliberately absent from a pod's configuration: it serves no
user, opens no inbound port and validates no user token.

**For the local stack the templates already hold the right values.** `cp
config/env.template config/.env` is enough — there is nothing to edit before
`make publish`.

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

To watch documents appear as you edit them, loop instead of running once:

```bash
make watch URL=http://localhost:8088/dav/ LIBRARY=<id> INTERVAL=30
```

Edit a file under the folder it serves and the next pass reports it `updated`;
add one and it is `created`; delete one and it is `removed`. This is what a
Fred schedule will do once one dispatches — Fred's own cadences are hourly,
daily and weekly, so thirty seconds is a developer's loop and never an
instance's setting.

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
| a **schedule** dispatching a run to `make run` | works |

The whole chain has been exercised against a local security-on stack: a
definition published, an instance created from the UI, a Temporal schedule
registered for it, runs dispatched on `kb__<definition id>`, the documents
ingested, and the next run downloading nothing because the entity tags matched.

If `make run` sits silent instead, the run is not reaching its queue. Check, in
this order, that an instance exists, that its schedule is not paused, and that
the queue the worker announced at startup matches the definition id — rather
than assuming the trigger is unbuilt.

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
