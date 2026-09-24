# Git repository — sample Knowledge Base (synchronization core)

Mirrors the documents of a **GitHub or GitLab** repository into a Fred library:
you give it a repository, a branch and an access token, and every run brings
the library to what that branch now holds.

It is a complete Knowledge Base image, started exactly like every other one:

```bash
python -m fred_samples_git_kb publish   # deployment step: declare this KB, exit
python -m fred_samples_git_kb run       # serve runs until stopped
```

`publish` runs on every deployment, so what Fred stores is what is deployed —
that is how the configuration form below reaches a team's UI. `run` polls for
work and never accepts an inbound connection. The SDK owns both commands;
[`__main__.py`](fred_samples_git_kb/__main__.py) is the entire integration.

Read [`knowledge_base.py`](fred_samples_git_kb/knowledge_base.py) first — the
declaration and the handler are the whole authoring surface — then
[`synchronize.py`](fred_samples_git_kb/synchronize.py) for what one run does.

---

## The idea: the pod keeps nothing

The sibling [`local-folder`](../local-folder) sample keeps a JSON ledger on
disk — the version it last published, per file. This one keeps **nothing**:

| What | Where it lives |
|---|---|
| Which revision the library is synchronized to | Fred, as the library's source version |
| What changed since then | Git, as the difference between two snapshots |
| A document's identity | its path relative to the configured folder, which Fred stores as the source key |
| A document's version | its Git blob id — equal ids mean equal bytes |

So the pod can be restarted, rescheduled or replaced between two runs and the
next one still does exactly the right thing. There is no ledger to lose, no
Fred identifier to remember, and no second copy of the truth to drift.

The cursor Fred holds is a small JSON string Fred never reads:

```json
{"v":1,"rev":"<the commit the branch was on>","sel":"2bcc5dbcad03"}
```

`sel` fingerprints the include/exclude patterns. Widening a pattern reaches
files that no difference between two revisions would ever mention, so a
changed fingerprint forces a full pass — the one thing a cursor alone could
not notice.

---

Both commands read the pod's environment. A deployed pod gets it from its
Deployment spec; locally, copy the template and let the Makefile export it:

```bash
cp config/env.template config/.env   # then check the URLs and the client secret
make publish
make run
```

Publishing needs a confidential Keycloak client holding `app:service_agent`.
Fred claims the *prefix* for whichever client publishes under it first, so the
client is named after the prefix — **`kb-fred.samples`**, shared with the
sibling sample, provisioned in the local stack by fred-deployment-factory's
`make keycloak-post-install`.

---

## Try it, with no Fred at all

```bash
make dev
make declaration                                      # what `publish` would send
make sync REPO=octocat/Hello-World INCLUDE='**'
make sync REPO=ThalesGroup/fred SUBDIR=docs           # markdown under docs/
make sync REPO=group/sub/project PROVIDER=gitlab      # the same, on GitLab
```

It runs the real synchronization against the real repository and logs the
documents it *would* write; the library it writes into is a stand-in that
remembers the cursor for the length of the process. A private repository needs
a token in the environment, never on the command line:

```bash
export FRED_SAMPLES_GIT_TOKEN=...   # a read-only personal access token
```

---

## Configuration

| Field | Default | Meaning |
|---|---|---|
| `provider` | `github` | `github` or `gitlab`. |
| `host` | the provider's | For GitHub Enterprise or a self-managed GitLab. |
| `repository` | — | `owner/name`, `group/sub/project`, or the address you copied from the browser. |
| `branch` | the repository's own | Empty means whatever the repository points `HEAD` at. |
| `token` | none | Only for a private repository — a public one needs nothing. |
| `subdirectory` | none | Synchronize only this folder, and strip it from the library's layout. |
| `include` | `**/*.md` | gitignore patterns, separated by commas or newlines. |
| `exclude` | none | Same language. Exclusion always wins. |
| `max_files` | 2000 | Past this, the run is **refused** — see below. |

A file larger than 10 MiB is skipped with a warning rather than written. That
one is not on the form: it bounds what this implementation will carry, not what
a team is choosing, and it lives in
[`settings.py`](fred_samples_git_kb/settings.py).

---

## What a run does

1. Ask Fred for the cursor, and the remote for the branch's revision. **If it
   has not moved, stop** — that is one ref listing and nothing downloaded,
   which is what most scheduled runs are.
2. Fetch that one revision, depth 1: the snapshot, never the history.
3. Compare it with the snapshot the cursor names — or, on a first run, a
   changed selection, or a mirror this pod does not have, compare the whole
   revision with what the library holds.
4. Write, then remove, so a renamed document is never briefly absent.
5. Record the new cursor — **only if nothing failed**.

### Five rules worth knowing

**A full pass compares before it writes.** It reads the library's inventory
back from Fred: a document already held at the same blob id is left alone, so
a lost cursor or mirror costs a comparison rather than re-ingesting the
repository, and a document the library holds that the revision no longer
selects is removed — every file was seen, so its absence is proven. If any
write fails, that pass removes nothing: it cannot tell a rename from a
deletion, and the next run replays it.

**A rename is four cases, not one.** What matters is not that the file moved
but whether each of its two paths belongs in the library: in and in is a write
plus a removal, in and out is a removal, out and in is a write, out and out is
nothing. All four are tested.

**A skip never removes.** A symlink, a submodule, a file past the size bound,
an LFS pointer, a name Git is not holding as text: each is reported and left
alone. The library loses a document because the repository dropped it, never
because this implementation could not carry it — and one awkward entry costs
that entry, never the run.

**A failed document holds the cursor still.** Moving past a document that
could not be written would claim a state the library does not have, and no
later run would ever look at that document again. The cost of standing still
is a repeated pass, and writes are idempotent; the cost of moving on is a
document nobody notices is missing.

**Too many files is a refusal, not a truncation.** A half-synchronized pass
has no revision it could honestly record — it would either lie or repeat
itself for ever. The run stops and says to narrow `subdirectory` or `include`.
The bound applies to both shapes of pass, so one commit adding a whole tree is
refused too rather than leaving a library the next full pass would reject.

---

## Why Git as a library, and not the GitHub and GitLab APIs

Both forges have mature Python clients, and reading a repository through them
would have meant writing this twice:

- **GitLab's compare endpoint returns no blob id.** The content identity that
  makes a document version free on GitHub would have had to be recovered with
  a second call, or by hashing every file ourselves.
- Two clients means two paginations, two rename representations, two sets of
  truncation flags, and mocked HTTP in every test.

Through [dulwich](https://www.dulwich.io/) — Git implemented in Python — the
two forges differ by exactly two things, both in
[`providers.py`](fred_samples_git_kb/providers.py): a host, and the user name a
token is presented under. Renames, blob ids, modes and the whole difference
between two revisions come from Git itself. No `git` binary in the image, no
subprocess, and the token is passed as a parameter rather than embedded in a
URL, written into the mirror's configuration, or placed on a command line
where every process on the machine can read it.

The forge APIs remain the right tool for what Git does not carry — listing a
user's repositories to fill in the form, registering a webhook so a push
triggers a run instead of a schedule. Neither is needed to synchronize.

The mirror is a **cache, never state**: deleting it costs one fetch and one
full pass, never correctness. It lives under `$FRED_SAMPLES_GIT_MIRROR_DIR`
(a deployed pod points that at a volume), else the user's cache directory.

It only grows. Each run leaves the previous snapshot's objects behind — that is
what the next difference is computed against — and nothing here prunes them or
evicts a mirror whose instance is gone. Size the volume for the repository's
churn, and clear it when it gets large: the next run rebuilds what it needs.

---

## Where the documents go

Through the SDK's `DocumentPublisher`, with the pod's own workload identity, in
[`knowledge_flow.py`](fred_samples_git_kb/knowledge_flow.py): writes, removals,
the inventory a full pass compares against, and the cursor itself. Each write
is followed until Fred has ingested it, so a document counted as written has
landed — which is what lets the cursor move past it. A Knowledge Base never
writes to OpenSearch or object storage directly.

When the pod's configuration names no `knowledge_flow_url`, the run logs what
it would write instead, which is what makes `make sync` work against a real
repository with no Fred.

Things this implementation ran into:

- **A rename costs a re-upload and a delete.** There is no way to move a
  document from one source key to another, so the document gets a new identity
  on the Fred side for what the repository considers the same file.
- **A Knowledge Base cannot name itself in a document's provenance.** The
  source tag a write is attributed to is resolved against Knowledge Flow's own
  deployment configuration, so every synchronized document is recorded under
  the same tag a person's upload uses.

---

## Test it

```bash
make code-quality
make test
```

Offline, no network, no Git binary: every test builds a real repository object
by object in a temporary folder and fetches from it. That is what lets the
awkward cases be tested for real — a force-pushed branch, a rename, a
submodule, a symlink, a filename that is not text — instead of being mocked.

One thing that cannot be shown offline is that a second fetch downloads only
the difference: dulwich's local transport sends the whole snapshot whatever it
is offered, so the saving only appears against a real Git server. That
measurement lives in an opt-in test, which needs the Git binary:

```bash
.venv/bin/pytest -m integration     # 201 KB re-sent, against 1.4 KB
```

It is what pins down the one non-obvious line in
[`git_source.py`](fred_samples_git_kb/git_source.py): the mirror records what
it holds under `refs/heads/`, because that is the only namespace a remote is
offered as already-held. Anywhere else the ref looks tidier and every run
re-downloads the repository.
