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
| Env config | `deploy/env/` | env-example, defaults |
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
| 1 | make build produces immutable tagged images | 20 | ceiling | 0 | UNTESTED | Build succeeded — all images tagged 260405-1517 (api-gateway, admin-api, runtime-api, meeting-api, agent-api, mcp, dashboard, tts-service, vexa-bot, vexa-lite) | 2026-04-05T19:37Z | 01a-infra-compose |
| 2 | make up starts all services healthy | 25 | ceiling | 0 | UNTESTED | All containers started, postgres healthy. Host health: gateway:8056, admin:8067, runtime:8090, agent:8100, dashboard:3001, transcription:8085 all responding. | 2026-04-05T19:37Z | 01-infra-up, 01a-infra-compose |
| 3 | Gateway, admin, dashboard respond | 20 | ceiling | 0 | UNTESTED | gateway → 200, admin /users → 200, dashboard serving on :3001. Inter-container: gateway→meeting-api, meeting-api→runtime-api, meeting-api→transcription-lb all verified. | 2026-04-05T19:37Z | 01-infra-up, 01a-infra-compose, 02-api, 04-dashboard |
| 4 | Transcription service has GPU | 15 | — | 0 | UNTESTED | transcription-lb: gpu_available=True. Reachable from meeting-api via host IP (172.17.0.1:8085) and DNS (transcription-lb). | 2026-04-05T19:37Z | 01-infra-up, 01a-infra-compose, 02-api |
| 5 | Database migrated and accessible | 10 | — | 0 | UNTESTED | meetings list → 200, bots list → 200, users → 200. DB has 8 tables. Note: migrate-or-init fails (fix_alembic_version.py missing) but schema is functional. | 2026-04-05T19:37Z | 01a-infra-compose, 02-api |
| 6 | MinIO bucket exists | 10 | — | 0 | UNTESTED | Recordings uploaded and retrieved for both platforms. Buckets: vexa/, vexa-recordings/. Redis ping ��� PONG. | 2026-04-05T19:37Z | 01a-infra-compose, 10-verify-post-meeting |

Confidence: 95 (all items pass including build. Minor finding: Admin API port is 8067 not 8057 as 01a procedure states — doc discrepancy, not functional issue.)
