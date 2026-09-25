# Knowledge Base Helm chart

This chart deploys the Fred WebDAV Knowledge Base as two workloads:

- a permanent `Deployment` running `knowledge-base run` and polling Temporal;
- a Helm post-install/post-upgrade/post-rollback `Job` running
  `knowledge-base publish` to
  register or update the Knowledge Base declaration in the Control Plane.

The worker exposes no port, so the chart intentionally creates no Service or
Ingress. It stores no local synchronization state, so it also creates no PVC.

## Prerequisites

- Fred Control Plane and Knowledge Flow reachable from the target namespace;
- Temporal reachable from the target namespace;
- a confidential Keycloak client, normally `kb-fred.samples`, with the
  `app:service_agent` role;
- that client's secret, either supplied through the chart or stored in an
  existing Kubernetes Secret;
- a WebDAV Knowledge Base image compatible with the deployed Fred version.

## Render and validate

```bash
helm lint charts/knowledge-base
helm template knowledge-base charts/knowledge-base \
  --namespace prism
```

## Published OCI chart

A `chart/vX.Y.Z` repository tag publishes this chart. Container images are
released independently with `code/vX.Y.Z` tags.
The chart is available at:

```text
oci://ghcr.io/fred-agent/fred-samples/charts/knowledge-base
```

For example:

```bash
helm pull oci://ghcr.io/fred-agent/fred-samples/charts/knowledge-base \
  --version 0.1.0
```

The chart's `appVersion` is used as the image tag when `image.tag` is empty.
An environment that mirrors the image can override both `image.repository`
and `image.tag` without repackaging the chart.

## Install

Create the target namespace first:

```bash
kubectl create namespace prism
```

### Use an existing Secret

Create the M2M Secret with `kubectl`, External Secrets or another secret
manager:

```bash
kubectl -n prism create secret generic knowledge-base-m2m \
  --from-literal=client-secret='<keycloak-client-secret>'
```

Reference it from the values:

```yaml
m2mSecret:
  create: false
  existingSecret: knowledge-base-m2m
  key: client-secret
```

### Let the chart create the Secret

Set the client secret in an encrypted values file, for example
`values-knowledge-base.sec.yaml` managed with SOPS:

```yaml
m2mSecret:
  create: true
  existingSecret: ""
  key: client-secret
  clientSecret: "<keycloak-client-secret>"
```

The generated Secret is named `<release>-m2m` when the release name already
contains the chart name (for example `knowledge-base-m2m`). Set
`m2mSecret.nameOverride` to choose another name.

Create an environment-specific values file containing the real Fred endpoints,
then install either the local chart or its released OCI artifact:

```bash
helm upgrade --install knowledge-base \
  charts/knowledge-base \
  --namespace prism \
  --values values-knowledge-base.yaml \
  --values values-knowledge-base.sec.yaml \
  --wait
```

```bash
helm upgrade --install knowledge-base \
  oci://ghcr.io/fred-agent/fred-samples/charts/knowledge-base \
  --version 0.1.0 \
  --namespace prism \
  --values values-knowledge-base.yaml \
  --values values-knowledge-base.sec.yaml \
  --wait
```

Do not put `clientSecret` in an unencrypted values file or on a command line.
When `create=false`, omit the secure values file if it contains no other
secrets.

## Private WebDAV certificate authority

Create a ConfigMap from the public CA certificate:

```bash
kubectl -n prism create configmap corporate-root-ca \
  --from-file=corporate-root.pem=./ca.pem
```

Enable its mount in the values file:

```yaml
webdavCa:
  enabled: true
  configMapName: corporate-root-ca
  key: corporate-root.pem
  mountPath: /etc/ssl/private-ca
  fileName: corporate-root.pem
```

The chart then sets `FRED_SAMPLES_WEBDAV_CA_FILE` to the mounted PEM. This CA
is added to the image's public trust roots rather than replacing them.

## Deployment behavior

The `publish` hook runs during Helm installation, upgrade and rollback. Actual
document synchronization is dispatched by Fred through Temporal; a Kubernetes
CronJob is neither created nor required.

The default `Recreate` Deployment strategy avoids running two versions of the
worker during an upgrade. Start with one replica and increase it only after
validating the desired Temporal worker concurrency.
