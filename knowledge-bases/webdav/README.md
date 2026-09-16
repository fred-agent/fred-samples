# WebDAV share — sample Knowledge Base

Mirrors the documents of a folder published over WebDAV into a Fred library:
you give it an address, and every run brings the library to what that folder
now holds.

It is a complete Knowledge Base image, started exactly like every other one:

```bash
python -m fred_samples_webdav_kb publish   # deployment step: declare this KB, exit
python -m fred_samples_webdav_kb run       # serve runs until stopped
```

`publish` runs on every deployment, so what Fred stores is what is deployed —
that is how the configuration form below reaches a team's UI. `run` polls for
work and never accepts an inbound connection. The SDK owns both commands;
[`__main__.py`](fred_samples_webdav_kb/__main__.py) is the entire integration.

Read [`knowledge_base.py`](fred_samples_webdav_kb/knowledge_base.py) first — the
declaration and the handler are the whole authoring surface — then
[`synchronize.py`](fred_samples_webdav_kb/synchronize.py) for what one run does.

---

## The idea: the share is asked everything, every time

The sibling [`git-repository`](../git-repository) sample keeps nothing at all,
because Git answers "what changed since this revision" on its own. WebDAV has
no such question. There is one verb that lists a folder and one that fetches a
file, and nothing that compares two moments in time.

So this one asks for the whole tree on every run, and keeps the versions the
last run accepted:

| What | Where it lives |
|---|---|
| What the share holds now | the share, re-listed on every run |
| What the library already has | a JSON ledger beside the pod |
| A document's identity | its path on the share, which Fred stores as the source key |
| A document's version | the share's entity tag — equal tags mean equal bytes |

That trade buys something the Git sample cannot have: because the listing is
**exhaustive**, a file that is gone really is gone, and the run can retract its
document. The Git sample's full pass can never do that — it has no inventory to
compare against.

**The ledger is a cache, not a source of truth.** Delete it and the next run
republishes everything (idempotent — a document is addressed by its path on
both sides) and retracts nothing. It costs one expensive run, never
correctness. It lives per instance under
`${XDG_STATE_HOME:-~/.local/state}/fred-samples-webdav-kb/`, and
`$FRED_SAMPLES_WEBDAV_STATE_DIR` moves it — a deployed pod points that at a
volume.

Why a ledger at all, rather than asking Fred what the library holds? Because
Fred does not offer that: a library's contents cannot be read back. The same
gap is why the sibling sample's full pass issues no removals.

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
sibling samples, provisioned in the local stack by fred-deployment-factory's
`make keycloak-post-install`.

---

## Try it, with no Fred and no share of your own

[`local-webdav/`](local-webdav/) starts Apache `mod_dav` + `mod_dav_fs` in a
container — the same pair a real share runs, not a stand-in that would agree
with whatever this code happens to do:

```bash
make dev
make share-run                            # http://localhost:8088/dav/
make sync URL=http://localhost:8088/dav/
```

`make share-run` builds the image on first use. It serves
`local-webdav/documents` by default and any folder you name:

```bash
make share-run SHARE_DIR=/path/to/your/notes
```

Edit a file under the folder it serves, run `make sync` again, and watch it come
back as `updated` while its neighbours stay `unchanged`; delete one and watch it
come back as `removed`. `make share-stop` when you are finished.

Nothing has reached Fred at this point — the run logs what it *would* write.
Name a library that exists and it really writes, through the same boundary a
dispatched run uses:

```bash
make sync URL=http://localhost:8088/dav/ LIBRARY=<library-id>
```

That needs `config/.env` and the local stack up. The `webdav-share` skill in
this repository drives the whole sequence.

Against a share of your own:

```bash
make declaration                                    # what `publish` would send
make sync URL=https://share.example.com/documents/
make sync URL=https://share.example.com/documents/ INCLUDE='**/*.md,**/*.pdf'
```

It runs the real synchronization against the real share and logs the documents
it *would* write. The ledger is the real one, so a second run is genuinely
incremental — `INSTANCE=other` gives a second share its own.

A share needing a sign-in takes its password from the environment, never from
the command line:

```bash
export FRED_SAMPLES_WEBDAV_PASSWORD=...
make sync URL=https://share.example.com/documents/ WEBDAV_USER=reader
```

---

## Configuration

| Field | Default | Meaning |
|---|---|---|
| `url` | — | The folder, as you would open it in a browser. A missing trailing slash is added. |
| `username` | none | Only for a share that needs a sign-in. |
| `password` | none | Only with a user name. |
| `include` | `**/*.md` | gitignore patterns, separated by commas or newlines. |
| `exclude` | none | Same language. Exclusion always wins. |
| `max_files` | 2000 | A run carries at most this many; the rest waits. |
| `trust_any_certificate` | off | See below. Prefer `$FRED_SAMPLES_WEBDAV_CA_FILE`. |

**Taking more than Markdown is `include`, not a code change.**
`**/*.md,**/*.pdf,**/*.png` takes all three: content is carried as bytes from
end to end, and the media type is read from the file's name. Knowledge Flow
converts what it is given, and it has a processor for PDFs, Office documents
and images (`.png .jpg .jpeg .gif .bmp .svg .webp .ico`).

One thing worth knowing before synchronizing a folder of images: **they become
searchable by their content only if the Knowledge Flow deployment has a vision
model configured.** Without one, its image processor keeps the filename as the
document's only searchable text — so the images are retrievable by name and no
more. That is the platform's setting, not this Knowledge Base's.

A file over 10 MiB is skipped with a warning rather than written — that one is
not on the form, because it bounds what this implementation will carry rather
than what a team is choosing, and it lives in
[`settings.py`](fred_samples_webdav_kb/settings.py). Several documents are
fetched at once and each is held whole before it is written, so that figure
bounds a pod's memory as much as it bounds one file.

`max_files` counts **documents**, not files on the share, so a folder holding a
few hundred pictures beside its Markdown does not eat the bound — and, more to
the point, does not make every run a partial one.

---

## Certificates, which is where this usually goes wrong first

A corporate share is on a name no public certificate authority can certify, so
its certificate is signed by a private one. **Python does not use the system
trust store**: httpx trusts `certifi`'s list of public authorities, and nothing
else. The share then fails in Python with `CERTIFICATE_VERIFY_FAILED` while
`curl` against the same URL works, because curl *does* read the system store.
The server is not the problem.

What is **not** needed is a client certificate. The share authenticates itself
to this Knowledge Base, not the other way round, so what has to be obtained is
the issuing authority's public root — never a certificate issued to this pod.

The fix is to give that root to the **pod**. A developer laptop's trust store
is irrelevant here: a container trusts what its own image ships, which is the
public authorities and nothing else.

```bash
export FRED_SAMPLES_WEBDAV_CA_FILE=/etc/ssl/private-ca/corporate-root.pem
```

### Why not `$SSL_CERT_FILE`

Because it **replaces** the trust store rather than adding to it. Point it at
one corporate root and this pod trusts exactly one authority — including for
its own Keycloak, Control Plane and Knowledge Flow, whose connections then fail
with a handshake error that names none of this. Measured, not assumed: a
context built from `certifi` trusts 121 authorities, and the same context built
with `SSL_CERT_FILE` holding one corporate root trusts 1.

`FRED_SAMPLES_WEBDAV_CA_FILE` is added to whatever is already trusted — 122,
not 1 — and only for the client that talks to the share. `$SSL_CERT_FILE` is
still honoured if an operator set it deliberately; this only ever adds to what
it produced.

A path that cannot be read is reported as `ca_file_unreadable` before any
request is made, because in a cluster that means one specific thing — the
volume did not mount — and the alternative is a handshake failure that blames
the share.

### In a cluster

A root certificate is public, so it belongs in a ConfigMap, not a Secret:

```yaml
env:
  - name: FRED_SAMPLES_WEBDAV_CA_FILE
    value: /etc/ssl/private-ca/corporate-root.pem
volumeMounts:
  - name: private-ca
    mountPath: /etc/ssl/private-ca
    readOnly: true
volumes:
  - name: private-ca
    configMap:
      name: corporate-root-ca      # kubectl create configmap corporate-root-ca \
                                   #   --from-file=corporate-root.pem=./ca.pem
```

Nothing here needs a writable root filesystem or `update-ca-certificates`, both
of which a hardened tenant usually forbids.

The `trust_any_certificate` box exists for the case where that certificate
genuinely cannot be obtained. It protects nothing — anything on the path can
read and rewrite the traffic — so it is off by default and **every run using it
reports a warning**, which is deliberate: a dangerous setting that goes quiet
is one nobody remembers accepting.

---

## What a run does

1. Walk the tree, one `PROPFIND` per folder, and ask each file for its entity
   tag, date and size.
2. Decide: what is new, what moved on, what the share no longer has.
3. Fetch and write what changed, four at a time.
4. Retract what the share dropped — **only if the walk was exhaustive**.
5. Record the ledger.

### Five rules worth knowing

**`Depth: 1` and our own walk, never `Depth: infinity`.** Apache's mod_dav
ships `DavDepthInfinity off` and answers 403 to it, so the one request that
would be cheapest is the one most shares refuse. The walk is breadth-first and
bounded in depth and in collections visited, because a folder symlinked back to
itself is an ordinary misconfiguration and would otherwise never end.

**An absence only means a deletion to a run that saw everything.** A walk cut
short, a bound reached, a ledger that could not be read: each makes the run
report `reconciliation_complete: false`, and such a run retracts nothing and
forgets nothing. This is the rule that decides whether a team keeps its
documents, and it is the one most heavily tested.

**A skip never removes.** A file past the size bound, or with a path the
platform will not take, is reported and left exactly as it is. The library
loses a document because the share dropped it, never because this
implementation could not carry it.

**A document that could not be written keeps the version the library still
holds.** Forgetting it looks harmless — the next run republishes it. But if the
file then disappears from the share, no run has a record of it again and the
document is orphaned for ever.

**The href decides the key, so the href is checked — and both sides are
compared decoded.** A `PROPFIND` answer naming a path outside the folder that
was asked for is dropped, decoded before it is checked rather than after:
`%2e%2e` is exactly what a check on the raw value waves through. The configured
address is decoded for the same comparison, which matters more than it looks —
a folder called `Documents partagés` is pasted from a browser as
`Documents%20partag%C3%A9s` and comes back in answers as neither, so comparing
the two forms matches nothing, drops every file, and leaves an empty inventory
that reads as a share whose every document was just deleted.

Redirects are never followed: off this host they would present the share's
credentials to whatever answered, and on it they mean the address is wrong in a
way an operator should be told about.

---

## What the share has to provide

Tested against **Apache `mod_dav` + `mod_dav_fs`**, which is the common case
and provides everything below. Anything else needs only `PROPFIND` on a
collection and `GET` on a file.

`getetag` is what makes a run cheap: equal tags mean equal bytes, so an
unchanged file is never fetched. A share that does not keep entity tags falls
back to `getlastmodified` and `getcontentlength` together, which still catches
every ordinary edit. A share offering neither leaves every file looking new on
every run — correct, expensive, and the run says so rather than letting it pass
unexplained.

---

## Where the documents go

Through Knowledge Flow's synchronizing ingestion surface — the one that
addresses a document by the key its source chose — using the SDK's own
`DocumentPublisher`, so this sample writes no authentication code and holds no
store credential. A Knowledge Base never writes to OpenSearch or object storage
directly. The seam is [`document_boundary.py`](fred_samples_webdav_kb/document_boundary.py).

Set no `FRED_KNOWLEDGE_FLOW_URL` and the run logs what it would write instead,
which is what makes `make sync` work against a real share with no Fred.

One consequence worth stating: **a Knowledge Base cannot name itself in a
document's provenance.** The source tag a write is attributed to is resolved
against Knowledge Flow's own deployment configuration, so every synchronized
document is recorded under the same tag a person's upload uses.

---

## Test it

```bash
make code-quality
make test
```

Offline, no network, no Apache: every test drives the real client against a
real WebDAV server built in the test process, answering real multistatus XML.
That is what lets the awkward cases be tested for real rather than mocked — a
share with no entity tags, one that answers 404 for a single property, one that
points outside itself, one that lists a child twice, one that never ends, and
one that understates a file's size and then sends two megabytes.
