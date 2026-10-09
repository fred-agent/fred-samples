# Knowledge Bases

A Knowledge Base tells Fred where a team's documents come from and how to keep
them up to date. You declare one with `fred_sdk.knowledge_base`: an identity,
the configuration a team fills in, and one async handler that reconciles the
source and reports what changed.

Two samples, from the smallest to the most demanding:

| Sample | Synchronizes | Read it for |
|---|---|---|
| [`local-folder/`](local-folder/) | a folder on the machine running it | the smallest believable implementation |
| [`git-repository/`](git-repository/) | a GitHub or GitLab branch | a pod that keeps **no state at all** |

Each is a standalone package with its own venv. Start with `local-folder`.

---

## A pod's ledger never lives in this repository

A sample that remembers what it already published keeps a **ledger**: one small
JSON file per Knowledge Base instance, holding each document's path and the
version the last run accepted. It is a cache, never a source of truth — delete
it and the next run republishes present files. For `local-folder`, losing the
ledger also loses the list needed to retract files deleted since the last run;
reconcile the target library before treating a ledger reset as harmless.

**It belongs outside the working tree, always.** The default is already outside
it, and nothing needs adding to `.gitignore`:

    ${XDG_STATE_HOME:-~/.local/state}/<package>/<instance>.json

`FRED_SAMPLES_KB_STATE_DIR` moves the `local-folder` ledger. Point it at a
volume on a deployed pod. **Never point it at a path inside this repository:**
it is per-machine, per-instance state, it means nothing on anyone else's
checkout, and a ledger committed by accident makes the next run believe the
library already holds documents it does not.

`git-repository` keeps no local synchronization ledger: it reads the saved
source revision from Fred. It can still keep a local clone cache; see its own
README.

---

## Every sample works the same way

Run these from the sample's own directory.

```bash
make dev          # install (resolves fred-sdk from the ../../../fred checkout)
make test         # offline, no network, no external service
make code-quality # ruff, bandit, detect-secrets, basedpyright
```

Without the sibling Fred checkout, use `make dev-pypi` and keep
`UV_NO_SOURCES=1` on later Make commands, so `uv run` does not return to local
sources. Initial dependency installation requires network access.

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
`configuration.yaml` resolved from `$CONFIG_FILE`, the models `fred_pod` owns,
and environment variables carrying **secrets only**.

```
config/configuration.yaml   app:              runtime_id (the pod's name in metrics and logs)
                            knowledge_base:   prefix, control_plane_url, knowledge_flow_url
                            security.m2m:     realm_url, client_id, secret_env_var
                            scheduler.temporal: host, namespace

config/.env                 CONFIG_FILE, and the one secret the YAML names
```

A sample may add one section of its own for what its operator decides,
parsed by a subclass of the SDK's `PodConfiguration`, from the same file, by
the same loader. Never an environment variable.

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
(`fred_pod.security.backend_to_backend_auth`, the module the other backends
use, not a scheme of its own):

```
POST <security.m2m.realm_url>/protocol/openid-connect/token
     grant_type=client_credentials
     client_id=<security.m2m.client_id>
     client_secret=$<security.m2m.secret_env_var>
```

then `Authorization: Bearer <token>` on every call. **The configuration names
which variable holds the secret**. The value stays out of the YAML; locally it
is stored in the untracked `config/.env`, and a deployment supplies it through
its secret mechanism.

### The queue is derived, never configured

Both the pod and the Control Plane compute it from the definition id with the
same function, `fred_sdk.knowledge_base.routing.task_queue_for`, so the
dispatching side and the worker side cannot disagree by construction:

```
kb__ + fred.samples.local-folder  →  kb__fred.samples.local-folder
```

`kb__` is the catalog namespace Knowledge Bases reserve, so they never collide
with capabilities, agents or applications. The rest is the definition id, which
already carries the prefix its contributor owns — which is why two contributors
can never end up sharing a queue. `scheduler.temporal.task_queue` is therefore
read by nobody: configuring it would be the bug, since a pod and a Control
Plane that disagreed would lose every run silently.

The first line `make run` prints tells you which queue it took:

```
INFO    Knowledge Base fred.samples.local-folder serving runs on kb__fred.samples.local-folder
```

### Two failure modes worth recognising rather than debugging

- **`control_plane_url` without its `/control-plane/v1` prefix** makes every
  call 404 against a healthy Control Plane.
- **The named secret variable unset** — the token exchange fails at startup
  rather than at the first document, which is deliberate: a Knowledge Base acts
  as a workload and Fred admits it as nothing else.

`security.user` is deliberately absent from a pod's configuration: it serves no
user, opens no inbound port and validates no user token.

The templates illustrate one local stack. Check their URLs, client identity
and secret against your deployment before `make publish`; copying a template
does not provision a Keycloak client or create the target services.

---

## Trying one with no Fred at all

Every sample ships a developer tool to read a source without Temporal. Run the
commands from the indicated sample directory. Git uses a logging library
unless you select a real library. For local-folder, explicitly suppress
platform configuration to keep the run source-only:

```bash
# local-folder
CONFIG_FILE=/nonexistent ENV_FILE=/nonexistent make sync ROOT=/path/to/your/notes

# git-repository
make sync REPO=ThalesGroup/fred SUBDIR=docs
make sync REPO=group/sub/project PROVIDER=gitlab
```

To watch documents appear as you edit them, loop instead of running once:

```bash
# local-folder
CONFIG_FILE=/nonexistent ENV_FILE=/nonexistent make watch ROOT=/path/to/your/notes
```

Edit a file under that folder and the next pass reports it `updated`;
add one and it is `created`; delete one and it is `removed`. This is what a
Fred schedule will do once one dispatches — Fred's own cadences are hourly,
daily and weekly, so thirty seconds is a developer's loop and never an
instance's setting.

**Run it twice.** The first run proves the source can be read; the second proves
the implementation knows what it already published — which is where a
synchronizer is usually wrong.

---

## Against a real Fred

1. Bring up the stack — Keycloak, Postgres, OpenFGA, OpenSearch, Temporal — with
   docker compose in `~/Fred/fred-deployment-factory`.
2. Run its `make keycloak-post-install` once. It provisions `kb-fred.samples`,
   the confidential client every sample here publishes as.
3. `cp config/env.template config/.env` and check the URLs and the secret.
4. `make publish`, then `make run`.

**Fred claims a prefix for the first client that publishes under it, and there
is no rebinding path.** Both samples publish under `fred.samples`, so
publishing from some other convenient client does not burn one definition — it
burns the whole prefix.

### How far this actually goes today

| Step | State |
|---|---|
| `publish` puts the definition in front of an admin | works |
| enabling it for a team | works |
| writing documents into a real library | works — in a scheduled run once `knowledge_flow_url` is set |
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

`live-knowledge-base-session` in [`.claude/skills/`](../.claude/skills/) is
collaborative: you drive the UI and decide what to test, the assistant
publishes and serves a pod against the live stack, watching the pod *and* the
Control Plane together, and reports what it sees.

---

## Logging, so you are not surprised

`fred_sdk.knowledge_base` configures logging with a plain
`logging.basicConfig(format="%(levelname)-7s %(message)s")` and wires none of
`fred_core`'s observability — no structured logs, no audit trail, no KPI sink.
A Knowledge Base pod therefore has **one unstructured stdout stream** where a
Fred application has three. That is the SDK's doing, identical for every sample
here, and it is a gap to close in `libs/fred-sdk` rather than in any one sample.
