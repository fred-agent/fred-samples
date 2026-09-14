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
