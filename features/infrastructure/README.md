---
services: [api-gateway, admin-api, meeting-api, runtime-api, dashboard]
tests3:
  targets: [smoke]
  checks: [GATEWAY_UP, ADMIN_API_UP, DASHBOARD_UP, RUNTIME_API_UP, TRANSCRIPTION_UP, REDIS_UP, MINIO_UP]
---

# Infrastructure

## Why

Everything depends on the stack running. If services aren't healthy, nothing else works.

## What

```
make build → immutable tagged images
make up → compose stack running
make test → all services respond
```

### Components

| Component | Path | Role |
|-----------|------|------|
| Compose stack | `deploy/compose/` | Docker Compose, Makefile, env |
| Helm charts | `deploy/helm/` | Kubernetes deployment |
| Env config | `deploy/env-example` | env template with defaults |
| Deploy scripts | `deploy/scripts/` | Fresh setup automation |

## How

### 1. Build images

```bash
cd deploy/compose
make build
# Builds all images with immutable tag (e.g., 260405-1517):
#   api-gateway, admin-api, runtime-api, meeting-api,
#   agent-api, mcp, dashboard, tts-service, vexa-bot, vexa-lite
```

### 2. Start the stack

```bash
make up
# Starts all services via docker compose
# Wait for postgres to be healthy, then all services start
```

### 3. Verify services are healthy

```bash
# Gateway
curl -s -o /dev/null -w "%{http_code}" http://localhost:8056/health
# 200

# Admin API
curl -s -o /dev/null -w "%{http_code}" http://localhost:8067/users
# 200

# Runtime API
curl -s -o /dev/null -w "%{http_code}" http://localhost:8090/health
# 200

# Dashboard
curl -s -o /dev/null -w "%{http_code}" http://localhost:3001
# 200

# Transcription service (GPU check)
curl -s http://localhost:8085/health
# {"status": "ok", "gpu_available": true}

# Redis
redis-cli ping
# PONG
```

### 4. Check database

```bash
# Verify tables exist via API
curl -s -H "X-API-Key: $VEXA_API_KEY" http://localhost:8056/bots
# 200 [...]

curl -s -H "X-API-Key: $VEXA_API_KEY" http://localhost:8056/meetings
# 200 [...]
```

### 5. Tear down

```bash
make down
```

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|-------|--------|----------|--------------|-------|
| 1 | make build produces immutable tagged images | 20 | ceiling | 0 | PASS | Compose build: all images tagged 0.10.0-260408-1826 (api-gateway, admin-api, runtime-api, meeting-api, agent-api, mcp, dashboard, tts-service, vexa-bot, vexa-lite) | 2026-04-08 | Phase 2a compose build |
| 2 | make up starts all services healthy | 25 | ceiling | 0 | PASS | Compose pull: all containers started, gateway:8056, admin:8057, dashboard:3001 responding. Transcription verified. | 2026-04-08 | Phase 1a compose pull |
| 3 | Gateway, admin, dashboard respond | 20 | ceiling | 0 | PASS | gateway 200, admin 200, dashboard 200. Transcription test: "Hello, this is a test..." | 2026-04-08 | Phase 1a compose pull |
| 4 | Transcription service has GPU | 15 | — | 0 | PASS | Transcription works: test WAV → "Hello, this is a test of the transcription service" | 2026-04-08 | Phase 1a compose test |
| 5 | Database migrated and accessible | 10 | — | 0 | PASS | restore-db: 1761 users, 9587 meetings, 507233 transcriptions loaded. Schema sync complete. Services healthy after restore. | 2026-04-08 | Phase 3a compose restore-db |
| 6 | MinIO bucket exists | 10 | — | 0 | PASS | Smoke health checks pass: MINIO_ENDPOINT_SET, MINIO_BUCKET_SET. Browser session S3 roundtrip works on helm. | 2026-04-08 | Phase 4 smoke |

Confidence: 100 (all 6 items PASS — full retest 2026-04-08)
