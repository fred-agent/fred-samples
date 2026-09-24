# Local folder — sample Knowledge Base

A Knowledge Base tells Fred where a team's documents come from and how to keep
them up to date. You declare one with the `fred_sdk.knowledge_base` authoring
surface: an identity, the configuration a team fills in, and one async handler
that reconciles the source and reports what changed.

This sample is the smallest believable implementation: it synchronizes Markdown
files from a folder on the machine running it.

Read [`knowledge_base.py`](fred_samples_local_folder_kb/knowledge_base.py) first
— the declaration and the handler are the whole authoring surface.

---

## The image is the integration

An author writes no plumbing. The SDK owns both commands, and
[`__main__.py`](fred_samples_local_folder_kb/__main__.py) is the entire
integration:

```python
raise SystemExit(knowledge_base_main(kb))
```

```bash
python -m fred_samples_local_folder_kb publish   # deployment step: declare this KB to Fred, exit
python -m fred_samples_local_folder_kb run       # serve runs until stopped
```

`publish` runs on every deployment of the image, so what Fred stores is what is
deployed — that is how the configuration form a team fills in reaches the UI.
`run` polls for work; it never accepts an inbound connection.

Both read the pod's environment. A deployed pod gets it from its Deployment
spec; locally, copy the template and let the Makefile export it:

```bash
cp config/env.template config/.env   # then check the URLs and the client secret
make publish
make run
```

Everything a contributor adds to Fred is named the same way: dotted segments
under a prefix that contributor owns. This one is **`fred.samples.local-folder`**
— `fred.samples` is the prefix this repository owns, exactly as its agents are
`fred.samples.hello_graph`.

Publishing needs a confidential Keycloak client holding the `app:service_agent`
role. Fred claims the *prefix* for whichever client publishes under it first, so
the client is named after the prefix — a second sample here publishes under the
same prefix with the same client. In the local stack, `kb-fred.samples` is
provisioned by fred-deployment-factory's `make keycloak-post-install`.

---

## Where the documents go

Through Knowledge Flow's REST API, with the SDK's `DocumentPublisher`, in
[`document_boundary.py`](fred_samples_local_folder_kb/document_boundary.py).
When the pod's configuration names no `knowledge_flow_url`, that file logs what
it would publish instead. It is the only place that knows the difference, and a
Knowledge Base never writes to OpenSearch or S3 directly.

The full chain (a schedule dispatching a run that writes into a real library) has
been exercised with the `webdav` sample; this one uses the same SDK path.

---

## Try it without a deployment

A small developer tool runs the same declaration and the same handler with no
Fred and no Temporal:

```bash
make declaration                   # what `publish` would send
make sync ROOT=/path/to/your/notes # one synchronization run
```

`sync` prints the run's result as JSON and logs one line per document it would
publish. Run it twice: the second run reports everything `unchanged`, because the
implementation keeps its own ledger (below).

A run also states whether it observed the folder exhaustively
(`reconciliation_complete`). Only a complete run's `removed` count means a
document is really gone — which is why a run bounded by `max_files` carries the
files it never reached forward instead of retracting them.

---

## Configuration

| Field | Type | Required | Meaning |
|---|---|---|---|
| `root_path` | `string` | yes | Folder to synchronize. Nothing outside it is ever read. |
| `glob` | `string` | no | Which files to pick up. Default `**/*.md`. Hidden paths are never picked up. |
| `max_files` | `integer` | no | Safety bound. A bounded run reports `reconciliation_complete: false`, so an absence in it never reads as a deletion. |

A team fills these in per instance; the published declaration carries the
*declarations* only, never a value.

---

## The ledger

Fred reports counters, not state. What "already synchronized" means is this
implementation's business, so it keeps a JSON sidecar per instance, mapping
relative path to content hash:

```
${XDG_STATE_HOME:-~/.local/state}/fred-samples-local-folder-kb/<instance-id>.json
```

Set `FRED_SAMPLES_KB_STATE_DIR` to put it elsewhere. Fred never sees this file,
and gives an implementation no place to keep one.

---

## Test it

```bash
make code-quality
make test
```

Offline, no network, no external service.
