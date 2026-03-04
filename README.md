# Vexa Helm Charts

Helm charts for deploying Vexa, the self-hosted real-time meeting transcription platform, including the Vexa Dashboard (https://github.com/Vexa-ai/Vexa-Dashboard).

Upstream app repo: https://github.com/Vexa-ai/vexa

## Charts

- `vexa`: Full, multi-service deployment matching the upstream Docker Compose topology, with optional Vexa Dashboard deployment.
- `vexa-lite`: Single-container deployment intended for simpler setups, with optional Vexa Dashboard deployment.

## Prerequisites

- Kubernetes cluster (v1.22+ recommended)
- Helm v3
- Container images for Vexa services available in your registry

Note: The charts default to local image repos/tags for development. Override image repositories and tags for production.

## Quickstart

Install the full chart from this repo:

```bash
helm install vexa ./charts/vexa \
  --set secrets.adminApiToken=CHANGE_ME \
  --set secrets.transcriberApiKey=CHANGE_ME \
  --set database.host=postgres \
  --set redisConfig.url=redis://redis:6379
```

Install the lite chart:

```bash
helm install vexa-lite ./charts/vexa-lite \
  --set vexa.databaseUrl=postgres://USER:PASS@HOST:5432/vexa \
  --set vexa.adminApiToken=CHANGE_ME \
  --set vexa.transcriberApiKey=CHANGE_ME
```

## Configuration

### vexa

Key values in `charts/vexa/values.yaml`:

- `secrets.adminApiToken`, `secrets.transcriberApiKey`: Required for auth and service communication.
- `database.host`, `database.user`, `database.name`: Used by admin-api, bot-manager, transcription-collector.
- `redisConfig.url` (or `redisConfig.host`/`port`): Required if `redis.enabled=false`.
- `botManager.orchestrator`: `process` (default), `kubernetes` (PoC), or `docker`.
- `whisperLive.profile`: `cpu` or `gpu` (use with GPU resources and node selectors).
- `ingress.*`: Optional ingress for `api-gateway`.

Bundled dev dependencies:

- `postgres.enabled=true` and `redis.enabled=true` create in-cluster Postgres/Redis for development.

### vexa-lite

Key values in `charts/vexa-lite/values.yaml`:

- `vexa.databaseUrl`, `vexa.adminApiToken`, `vexa.transcriberApiKey`: Required unless `vexa.existingSecret` is set.
- `vexa.orchestrator`: Defaults to `process` (no Docker socket required).
- `dashboard.enabled`: Deploys a separate dashboard container.
- `ingress.*`: Optional ingress for the lite API and dashboard.

## Notes

- For production, set explicit image repositories/tags and configure external Postgres/Redis and secrets management.

## License

See `LICENSE`.
