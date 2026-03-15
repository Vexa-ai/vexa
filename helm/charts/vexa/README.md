# Vexa Helm Chart

## What
Deploys the Vexa real-time meeting transcription platform to Kubernetes.

## Why
Self-hosted deployment of the full Vexa stack: bot management, per-speaker transcription, real-time delivery via WebSocket, and a dashboard UI.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────────┐
│  Dashboard   │────>│  API Gateway  │────>│  Admin API          │
│  (Next.js)   │     │  (FastAPI)    │     │  (FastAPI)          │
└─────────────┘     └──────┬───────┘     └─────────────────────┘
                           │
                    ┌──────▼───────┐     ┌─────────────────────┐
                    │ Bot Manager   │────>│  Transcription       │
                    │ (FastAPI)     │     │  Collector (FastAPI)  │
                    └──────┬───────┘     └──────────┬──────────┘
                           │                        │
                    ┌──────▼───────┐         ┌──────▼──────┐
                    │  Bot Pods     │         │   Postgres   │
                    │  (Playwright) │         │   Redis      │
                    └──────────────┘         └─────────────┘
```

## Services

| Service | Description | Port |
|---------|-------------|------|
| api-gateway | HTTP + WebSocket API entry point | 8000 |
| admin-api | User/token CRUD, meeting management | 8001 |
| bot-manager | Spawns and manages meeting bots | 8080 |
| transcription-collector | Redis stream -> Postgres persistence | 8000 |
| transcription-service | GPU inference (Whisper) -- optional, can run externally | 8000 |
| mcp | Model Context Protocol server | 18888 |
| dashboard | Next.js meeting dashboard | 3000 |
| postgres | Database (bundled, optional) | 5432 |
| redis | Stream + pub/sub (bundled, optional) | 6379 |

## Quick Start

```bash
helm install vexa ./helm/charts/vexa \
  --set secrets.adminApiToken=your-secret \
  --set database.host=your-pg-host \
  --set redisConfig.host=your-redis-host
```

## Bot Orchestration

The bot-manager supports three orchestrator modes:

- **process** (default): Bots run as child processes inside the bot-manager pod. Simple, no extra permissions. Recommended for small deployments.
- **kubernetes**: Bots spawn as separate Pods. Requires RBAC (set `botManager.kubernetesOrchestrator.createRbac=true`). Best for scale.
- **docker**: Bots spawn as Docker containers. Requires Docker socket mount. Not recommended for K8s.

## Transcription Service

The transcription-service requires a GPU. Options:
- **External**: Run on a GPU machine outside K8s. Set `transcriptionService.enabled=false` and configure the URL in bot-manager.
- **In-cluster**: Set `transcriptionService.enabled=true` with a GPU node pool.

## Configuration

See `values.yaml` for all options. Key overrides for production:

```yaml
secrets:
  adminApiToken: "strong-random-token"
  transcriberApiKey: "match-transcription-service-API_TOKEN"

database:
  host: "your-postgres-host"

ingress:
  enabled: true
  host: "gateway.yourdomain.com"
  className: "nginx"
  tls:
    - secretName: vexa-tls
      hosts: ["gateway.yourdomain.com"]
```
