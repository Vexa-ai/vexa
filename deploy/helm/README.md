# Vexa Helm Charts

## Why

Docker Compose gets you running locally but doesn't scale, self-heal, or manage secrets properly. For production Kubernetes deployments, you need proper resource limits, RBAC for bot pod spawning, health probes, ingress routing, and secrets management. These Helm charts package all of that — two charts covering the full multi-service topology and the simpler single-pod Lite deployment.

## What

Helm charts for deploying Vexa on Kubernetes. Includes the Vexa Dashboard.

## How

- `vexa`: Full, multi-service deployment matching the upstream Docker Compose topology, with optional Vexa Dashboard deployment.
- `vexa-lite`: Single-container deployment intended for simpler setups, with optional Vexa Dashboard deployment.

### Prerequisites

- Kubernetes cluster (v1.22+ recommended)
- Helm v3
- Container images published to DockerHub (`vexaai/` namespace)

### Image tags

Charts default to `vexaai/*:latest`. For production, pin to a specific immutable tag:

```bash
helm install vexa ./deploy/helm/charts/vexa \
  --set apiGateway.image.tag=260330-1415 \
  --set adminApi.image.tag=260330-1415 \
  --set meetingApi.image.tag=260330-1415
```

Mutable tags (`:staging`, `:latest`) are pointers managed by `make promote-staging` / `make promote-latest` in the compose Makefile. They always point to a known immutable `YYMMDD-HHMM` build.

The staging values file (`values-staging.yaml`) uses `:staging` which is updated via promotion.

### Quickstart

Install the full chart from this repo:

```bash
helm install vexa ./deploy/helm/charts/vexa \
  --set secrets.adminApiToken=CHANGE_ME \
  --set secrets.transcriptionServiceToken=CHANGE_ME \
  --set database.host=postgres \
  --set redisConfig.url=redis://redis:6379
```

Install the lite chart:

```bash
helm install vexa-lite ./deploy/helm/charts/vexa-lite \
  --set vexa.databaseUrl=postgres://USER:PASS@HOST:5432/vexa \
  --set vexa.adminApiToken=CHANGE_ME \
  --set vexa.transcriptionServiceToken=CHANGE_ME
```

### Configuration

### vexa

Key values in `charts/vexa/values.yaml`:

- `secrets.adminApiToken`, `secrets.transcriptionServiceToken`: Required for auth and service communication.
- `database.host`, `database.user`, `database.name`: Used by admin-api, meeting-api.
- `redisConfig.url` (or `redisConfig.host`/`port`): Required if `redis.enabled=false`.
- `meetingApi.orchestrator`: `process` (default), `kubernetes` (PoC), or `docker`.
- `whisperLive.profile`: `cpu` or `gpu` (use with GPU resources and node selectors).
- `ingress.*`: Optional ingress for `api-gateway`.

Bundled dev dependencies:

- `postgres.enabled=true` and `redis.enabled=true` create in-cluster Postgres/Redis for development.

### vexa-lite

Key values in `charts/vexa-lite/values.yaml`:

- `vexa.databaseUrl`, `vexa.adminApiToken`, `vexa.transcriptionServiceToken`: Required unless `vexa.existingSecret` is set.
- `vexa.orchestrator`: Defaults to `process` (no Docker socket required).
- `dashboard.enabled`: Deploys a separate dashboard container.
- `ingress.*`: Optional ingress for the lite API and dashboard.

### Notes

- All images are on DockerHub under `vexaai/`. No GHCR setup required.
- For production, pin image tags to specific `YYMMDD-HHMM` builds rather than using `:latest`.

## Development Notes

### Verification checklist

After deploying, verify:

1. `helm template` renders without errors
2. `helm install --dry-run` succeeds
3. All pods reach Running state (no CrashLoopBackOff)
4. All services have endpoints
5. Ingress routes correctly
6. Secrets are created
7. PVCs are bound
8. Bot RBAC works (can spawn pods, if using kubernetes orchestrator)
9. Inter-service connectivity (api-gateway can reach admin-api, meeting-api, etc.)
10. Health endpoints respond on each service

## Definition of Done

| # | Item | Weight | Status | Evidence | Last checked |
|---|------|--------|--------|----------|--------------|
| 1 | vexa chart installs on K8s | 25 | SKIP | Not tested | — |
| 2 | vexa-lite chart installs on K8s | 25 | SKIP | Not tested | — |
| 3 | Values documented | 15 | SKIP | Not audited | — |
| 4 | Image tags match chart defaults | 10 | SKIP | Not verified | — |
| 5 | Secrets management documented | 15 | SKIP | Not audited | — |
| 6 | Health probes configured | 10 | SKIP | Not verified | — |

## Confidence

Score: 0/100
Last validated: never
Ceiling: Never tested on a real cluster

## License

See `LICENSE`.
