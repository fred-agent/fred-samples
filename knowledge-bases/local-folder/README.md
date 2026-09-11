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

Fred names a Knowledge Base with two segments, like everything else a pod
publishes (`agent__<runtime>__<agent>`, `model__<provider>__<name>`): this one is
`kb__fred-samples__local-folder`. `fred-samples` is the namespace this
repository's images own; `local-folder` is one Knowledge Base in it.

Publishing needs a confidential Keycloak client holding the `app:service_agent`
role. Fred binds the *provider* to whichever client publishes it first, so the
client belongs to the namespace rather than to one Knowledge Base — a second
sample here publishes under the same provider with the same client. In the local
stack, `knowledge-base-fred-samples` is provisioned by fred-deployment-factory's
`make keycloak-post-install`.

---

## What is not built yet

Two pieces of the platform side are still open, so a real `run` cannot complete
end to end today:

- the Control Plane endpoints a run uses to fetch its configuration and report
  its result;
- the authorized document boundary. Documents will go through Knowledge Flow's
  REST API; until then this sample calls
  [`document_boundary.py`](fred_samples_local_folder_kb/document_boundary.py),
  which logs what it would publish. That file is the seam, and it is the only
  place that changes. A Knowledge Base never writes to OpenSearch or S3 directly.

`fred-sdk` 3.7.0 is not on PyPI either, so `make dev` resolves it from the `fred`
monorepo checked out next to this repository. `make dev-pypi` fails until it is.

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
