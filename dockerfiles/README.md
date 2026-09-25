# Docker Guide for `fred-samples`

Three images live here, and they are not the same kind of thing:

| Dockerfile | What it is | Built from |
|---|---|---|
| `Dockerfile` | the agents pod | `agents/` — `make docker-build` |
| `Dockerfile.knowledge-base` | a Knowledge Base pod | `knowledge-bases/<sample>/` — `make docker-build` |
| `Dockerfile.webdav-share` | a test fixture, not a deployable | `knowledge-bases/webdav/` — `make share-build` |

Everything below is the **agents** image. For a Knowledge Base pod — how it is
configured, why it opens no port, where its ledger volume goes and how it is
given a private certificate authority — see
[`knowledge-bases/webdav/README.md`](../knowledge-bases/webdav/README.md).

One Dockerfile serves the three Knowledge Base samples: they differ by folder
and package name, passed as `--build-arg KB=` and `--build-arg KB_PACKAGE=`,
and by nothing else. The package name is passed rather than derived from the
folder, because `git-repository` holds `fred_samples_git_kb`.

Unlike the agents image, a Knowledge Base image resolves `fred-sdk` from PyPI
(`uv sync --no-sources`), so it builds from a standalone clone with no sibling
monorepo checkout — which is what CI has, and what makes it publishable.

---

## Publishing images

CI publishes the Knowledge Base images listed in the matrix of
[`.github/workflows/Build-and-push-docker.yml`](../.github/workflows/Build-and-push-docker.yml)
to `ghcr.io/fred-agent/fred-samples/<image>`. Today that is one image,
`fred-samples-webdav-kb`. The agents image is not published by CI: it is built
and pushed by hand with the commands further down.

Nothing is pushed unless three checks pass first, in this order: the sample's
offline tests (`UV_NO_SOURCES=1 make test`), a build, and `make docker-smoke`
against the image just built. A pull request runs the same checks and pushes
nothing.

| Event | Tags pushed | Use it for |
|---|---|---|
| git tag `code/v1.2.3` | `v1.2.3`, plus a GitHub release listing the images | a Deployment |
| the same tag | `latest`, which moves | knowing what the newest release is — never a Deployment |
| push to `swift` | `swift-dev-<short sha>` | pinning one development build |
| push to `swift` | `swift-dev`, which moves | trying the latest build by hand |

A release is one tag for the whole repository — every image in the matrix gets
the same version:

```bash
git tag -a code/v1.2.3 -m "fred-samples v1.2.3"
git push origin code/v1.2.3
```

The version numbers this repository's own releases, not Fred's: which Fred an
image works with is set by the `fred-sdk` floor in the sample's `pyproject.toml`.

To publish another Knowledge Base, add its entry to the matrix and give its
Makefile a `docker-smoke` target — CI calls it by name.

---

## The agents image

This section explains how to build, run, and push the agents image.

## Prerequisites

- Docker installed and running
- `agents/config/.env` created from `agents/config/env.template`

## Where to run commands

Run all commands from:

```bash
cd ./agents
```

## Build image

Default build:

```bash
make docker-build
```

Build with custom image/tag:

```bash
make docker-build DOCKER_IMAGE_NAME=your-registry/fred-samples-agents DOCKER_IMAGE_TAG=v1.0.0
```

Build with explicit container user mapping:

```bash
make docker-build \
  DOCKER_USER_NAME=fred-user \
  DOCKER_USER_ID=$(id -u) \
  DOCKER_GROUP_ID=$(id -g)
```

## Run image locally

Default run (maps `8010:8010`, mounts `agents/config` read-only):

```bash
make docker-run
```

Run with custom host port:

```bash
make docker-run HOST_PORT=18010
```

The service will be available at:

```text
http://127.0.0.1:<HOST_PORT>/samples/agents/v1
```

## Push image

Push the current `DOCKER_IMAGE`:

```bash
make docker-push
```

Push with custom image/tag:

```bash
make docker-push DOCKER_IMAGE_NAME=your-registry/fred-samples-agents DOCKER_IMAGE_TAG=v1.0.0
```

## Useful Docker targets

```bash
make docker-stop   # stop running container by name
make docker-clean  # remove only this container/image
```

## Make variables (Docker)

- `DOCKER_IMAGE_NAME` (default: `fred-samples-agents`)
- `DOCKER_IMAGE_TAG` (default: `latest`)
- `DOCKER_IMAGE` (default: `$(DOCKER_IMAGE_NAME):$(DOCKER_IMAGE_TAG)`)
- `DOCKER_CONTAINER_NAME` (default: `fred-samples-agents`)
- `DOCKERFILE_PATH` (default: `../dockerfiles/Dockerfile`)
- `DOCKER_CONTEXT` (default: `..`)
- `DOCKER_USER_NAME` (default: `fred-user`)
- `DOCKER_USER_ID` (default: `$(id -u)`)
- `DOCKER_GROUP_ID` (default: `$(id -g)`)
- `HOST_PORT` (default: `8010`)
- `CONTAINER_PORT` (default: `8010`)
