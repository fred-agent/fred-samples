# A WebDAV share on your own machine

Apache `mod_dav` + `mod_dav_fs` in a container — the same pair a real share
runs, rather than a stand-in that would agree with whatever this Knowledge Base
happens to do.

```bash
make share-run                                  # http://localhost:8088/dav/
make sync URL=http://localhost:8088/dav/
make share-stop
```

The image is built from [`dockerfiles/Dockerfile.webdav-share`](../../../dockerfiles/Dockerfile.webdav-share)
on first use; `httpd.conf` beside this file is what it bakes in. `make share-build`
rebuilds it after an edit, `make share-clean` removes it.

## Serving your own folder

```bash
make share-run SHARE_DIR=/absolute/path/to/documents
```

It is mounted **read-only**: this Knowledge Base only ever reads, and a fixture
that could write into your real documents is not one worth handing out. A `PUT`
against the share fails, and that is the mount rather than a misconfiguration.

## The corpus here is not arbitrary

`documents/` holds four files, two of which exist to be awkward:

- `notes.txt` — the default `**/*.md` must leave it out, which is easier to
  believe once you have watched it happen.
- `Documents partagés/réunions/` — a folder whose name needs escaping is the
  case that breaks a synchronizer quietly. Apache answers with `%c3%a9` in lower
  case while a browser puts `%C3%A9` in the address bar in upper case, so
  comparing the two encoded forms matches nothing.

## Reaching it from elsewhere

The container joins `fred-shared-network`, so another container on that network
reaches it as `http://fred-samples-webdav-share/dav/`. A Fred running natively
on the host — the usual local setup — uses `http://localhost:8088/dav/`.

`SHARE_HOST_PORT=9000 make share-run` moves the host port.

## What it is not

No authentication and no TLS, on purpose: the point is to exercise the walk, the
entity tags and the reconciliation. A real corporate share brings a certificate
authority with it, and that story is in the
[sample's README](../README.md#certificates-which-is-where-this-usually-goes-wrong-first).
