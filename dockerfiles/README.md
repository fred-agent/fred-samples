# Docker Guide for `fred-samples` Agents

This guide explains how to build, run, and push the agents image.

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
