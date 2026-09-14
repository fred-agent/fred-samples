---
name: webdav-share
description: Serve any local folder over WebDAV — Apache mod_dav + mod_dav_fs in a container — so the WebDAV Knowledge Base can be exercised against a real server, and drive one synchronization run end to end into a real Fred library. Use for "run a WebDAV on this folder", for testing the webdav sample against something other than its fixture corpus, or to watch documents arrive in a library from a source Fred did not host.
user-invocable: true
argument-hint: "[optional: the folder to serve; defaults to the sample's own corpus]"
---

# WebDAV share (fred-samples)

Serves a folder the developer names, over the same server software a production
share runs, and drives the `knowledge-bases/webdav` Knowledge Base against it.

Everything lives in `knowledge-bases/webdav` — run every command from there.

Sibling of `.claude/skills/live-knowledge-base-session/SKILL.md`, which is the
*pod* session (publish, enable, serve). This one is the *source* session: it
supplies the thing a Knowledge Base reads from. The two compose, and the section
"How far end to end actually goes" below says exactly where the seam is today.

## Serving a folder

```bash
make share-run SHARE_DIR=/absolute/path/to/documents     # builds the image if needed
make share-logs                                           # Apache's access log
make share-stop
```

Omit `SHARE_DIR` and it serves `local-webdav/documents`, the sample's own
corpus. That corpus is not arbitrary — `notes.txt` is there to be *excluded* by
the default `**/*.md`, and `Documents partagés/réunions/` is there because a
folder whose name needs escaping is what breaks a synchronizer quietly.

Two addresses, and picking the wrong one is the most common way to lose ten
minutes here:

| Reached from | Address |
|---|---|
| the host — `make sync`, `curl`, a browser | `http://localhost:8088/dav/` |
| another container on `fred-shared-network` | `http://fred-samples-webdav-share/dav/` |

The container joins `fred-shared-network` for exactly that reason. A Fred
running natively on the host (`make run`, the usual local setup) uses the first;
only a containerised one uses the second.

**Mounted read-only.** The share cannot modify the developer's folder, which is
deliberate: this Knowledge Base only ever reads, and a fixture that could write
into someone's real documents is not one to hand out. `PUT` and `DELETE` will
fail, and that is correct rather than a misconfiguration to repair.

## Driving a run

```bash
make sync URL=http://localhost:8088/dav/                      # dry run: logs what it would write
make sync URL=http://localhost:8088/dav/ INCLUDE='**/*.md,**/*.pdf'
make sync URL=http://localhost:8088/dav/ LIBRARY=<library-id> # writes into Fred for real
```

Without `LIBRARY` nothing reaches Fred and no Fred is needed. With it, documents
go through Knowledge Flow's synchronizing ingestion surface using the pod's own
identity — which needs `config/.env` filled in from `config/env.template`, and
the security-on stack up. Read that file and confirm it before running, exactly
as the pod session skill requires; a missing `FRED_KB_CLIENT_SECRET` makes the
SDK log that it is calling Fred unauthenticated and carry on, and every call is
then rejected.

The library id is a library that already exists, created by the developer in the
UI. Do not invent one.

### What to watch, and what it proves

| Line | What it tells you |
|---|---|
| `PROPFIND … 207 Multi-Status` | the walk, one request per folder |
| `GET …` only for files that changed | entity-tag comparison is working |
| `would publish` / `published` | dry run versus real ingestion |
| `3 discovered, 0 created, 0 updated, 0 removed, 3 unchanged` | a second run over an untouched share downloads nothing |

The interesting run is always the **second** one. The first proves the share can
be read; the second proves the ledger, which is where a synchronizer is usually
wrong.

To exercise the reconciliation properly, have the developer edit a file, then
delete one, running `make sync` between each — `1 updated`, then `1 removed`.
A removal only ever comes from a run that reported `exhaustive: true`.

## How far end to end actually goes

Say this at the start rather than letting the developer wait for something that
cannot come. Against a local Fred today:

- **Works:** the share, the walk, the entity tags, the reconciliation, and real
  ingestion into a real library through `LIBRARY=` — documents appear in the UI.
- **Works:** `make publish` puts the definition in front of an admin, and
  enablement binds it to a team.
- **Not built yet:** the scheduled dispatch. The generic workflow and activity
  adapter, the run record and the team-facing instance form are unchecked tasks
  in the `knowledge-base-sdk-contract` OpenSpec change, so **no run is ever
  dispatched to the worker's queue**. `make run` will sit there, correctly,
  forever.

So "end to end" today means: share → walk → Knowledge Flow → library, driven by
`make sync LIBRARY=…`. It does not mean a schedule firing. Both halves are real;
only the trigger between them is missing.

## Preconditions that are not yours to satisfy

- **Docker.** The share is a container. `make share-run` builds the image on
  first use and creates `fred-shared-network` if it is absent.
- **Port 8088** free on the host. `SHARE_HOST_PORT=…` moves it.
- **Infra**, for a `LIBRARY=` run: Keycloak, Postgres, OpenFGA, OpenSearch and
  Knowledge Flow, via docker compose in `~/Fred/fred-deployment-factory`. **Do
  not start, stop, or wipe this infra yourself** — confirm with the developer
  that it is up.

## Certificates: not this share's problem, and a trap worth naming

This share is plain HTTP on purpose. When the conversation moves to a real
corporate share over `https://`, two things matter and both are in
`knowledge-bases/webdav/README.md`:

- What is needed is the issuing authority's **root**, never a client
  certificate.
- Use `FRED_SAMPLES_WEBDAV_CA_FILE`, which **adds** it. `SSL_CERT_FILE`
  *replaces* the whole trust store — measured, 121 authorities become 1 — and it
  is process-wide, so it can silently break the pod's own Keycloak and Knowledge
  Flow connections.

Never suggest the `trust_any_certificate` box as a way past a certificate
problem without saying plainly what it costs.

## The protocol

Collaborative, like its sibling skills. The developer decides what to test and
reads the UI; you start the share, run what you are asked to run, and report
what you see.

- Start the share and any tail with `run_in_background: true`, then use
  **Monitor** to stream stdout rather than re-reading a log on a timer. Stop
  every Monitor you started when the developer is done.
- **Never `make sync LIBRARY=…` unasked.** It writes real documents into a real
  team's library. The dry run is the default for that reason.
- Leave the share running between runs — restarting it changes nothing about the
  Knowledge Base's state, but stopping it mid-session wastes the developer's
  context.
- `make share-stop` at the end, and say so. `make share-clean` also drops the
  image; only do that if asked.
- Report what the log actually says. A run that reported `exhaustive: false` did
  not prove a share is fully synchronized, whatever the counters look like.
