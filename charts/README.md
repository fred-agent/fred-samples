# Helm charts

Charts in this directory deploy the runnable samples published by this
repository. They contain no environment-specific Fred endpoints or secrets;
consumers provide those values in their own deployment repository.

## Published charts

| Chart | OCI reference |
|---|---|
| [`knowledge-base`](knowledge-base/) | `oci://ghcr.io/fred-agent/fred-samples/charts/knowledge-base` |

Pull or install a released version with Helm:

```bash
helm pull oci://ghcr.io/fred-agent/fred-samples/charts/knowledge-base \
  --version 0.1.0

helm upgrade --install knowledge-base \
  oci://ghcr.io/fred-agent/fred-samples/charts/knowledge-base \
  --version 0.1.0 \
  --namespace prism \
  --values values-knowledge-base.yaml
```

The chart is published when a matching repository tag such as
`chart/v0.1.0` is pushed. Container images use independent `code/vX.Y.Z`
releases. The chart's `appVersion` is the default image tag.
