# Container standards

Applies to every container-based Celoria workload.

## Tooling
- Podman/Buildah, not Docker. Files are named `Containerfile` and
  `.containerignore`. CI uses `.github/workflows/container-image.yml`.
- Do not use `HEALTHCHECK` in the image; Kubernetes probes are the source of truth.
- Run as a non-root user.

## HTTP probe endpoints
Served under a `/-/` prefix, keeping them out of the application route space.

| Path | Purpose | Must not depend on |
|---|---|---|
| `/-/livez` | Process is alive; failure means restart | any dependency |
| `/-/readyz` | Can accept traffic; failure removes from Service | downstream backends |
| `/-/healthz` | General health summary | n/a |
| `/-/metricsz` | Prometheus text-format metrics | n/a |

Block `/-/` at the public ingress; allow only the kubelet and metrics scraper.
