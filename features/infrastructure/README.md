---
services:
- api-gateway
- admin-api
- meeting-api
- runtime-api
- dashboard
---

# Infrastructure

**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **confidence ≥ 100%**

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


<!-- BEGIN AUTO-DOD -->
<!-- Auto-written by tests3/lib/aggregate.py from release tag `0.10.0-260419-0052`. Do not edit by hand — edit the sidecar `dods.yaml` + re-run `make -C tests3 report --write-features`. -->

**Confidence: 0%** (gate: 100%, status: ❌ below gate)

| # | Behavior | Weight | Status | Evidence (modes) |
|---|----------|-------:|:------:|------------------|
| gateway-up | API gateway responds to /admin/users via valid admin token | 10 | ⬜ missing | `lite`: smoke-health/GATEWAY_UP: API gateway accepts connections — all client requests can reach backend; `compose`: smoke-health/GATEWAY_UP: API gateway accepts connections — all client requests can reach backend; `helm`: check GATEWAY_UP not found in any smoke-* report |
| admin-api-up | admin-api responds with a valid list | 10 | ⬜ missing | `lite`: smoke-health/ADMIN_API_UP: admin-api responds with valid token — user management and login work; `compose`: smoke-health/ADMIN_API_UP: admin-api responds with valid token — user management and login work; `helm`: check ADMIN_API_UP not found in any smoke-* report |
| dashboard-up | dashboard root page responds | 10 | ⬜ missing | `lite`: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI; `compose`: smoke-health/DASHBOARD_UP: dashboard serves pages — user can access the UI; `helm`: check DASHBOARD_UP not found in any smoke-* report |
| runtime-api-up | runtime-api (bot orchestrator) is reachable / has ready replicas | 15 | ⬜ missing | `lite`: smoke-health/RUNTIME_API_UP: runtime-api responds — bot container lifecycle management works; `compose`: smoke-health/RUNTIME_API_UP: runtime-api responds — bot container lifecycle management works; `helm`: check RUNTIME_API_UP not found in any smoke-* report |
| transcription-up | transcription service /health returns ok + gpu_available | 15 | ⬜ missing | `lite`: smoke-health/TRANSCRIPTION_UP: transcription service responds — audio can be converted to text; `compose`: smoke-health/TRANSCRIPTION_UP: transcription service responds — audio can be converted to text; `helm`: check TRANSCRIPTION_UP not found in any smoke-* report |
| redis-up | Redis responds to PING | 10 | ⬜ missing | `lite`: smoke-health/REDIS_UP: Redis responds to PING — WebSocket pub/sub, session state, and caching work; `compose`: smoke-health/REDIS_UP: Redis responds to PING — WebSocket pub/sub, session state, and caching work; `helm`: check REDIS_UP not found in any smoke-* report |
| minio-up | MinIO is healthy / has ready replicas | 10 | ⬜ missing | `compose`: smoke-health/MINIO_UP: MinIO responds — recordings and browser state storage work; `helm`: check MINIO_UP not found in any smoke-* report |
| db-schema | Database schema is aligned with the current model | 10 | ⬜ missing | `lite`: smoke-health/DB_SCHEMA_ALIGNED: all required columns present; `compose`: smoke-health/DB_SCHEMA_ALIGNED: all required columns present; `helm`: check DB_SCHEMA_ALIGNED not found in any smoke-* report |
| gateway-timeout | Gateway proxy timeout is ≥30s (prevents premature 504s under load) | 10 | ⬜ missing | `lite`: smoke-static/GATEWAY_TIMEOUT_ADEQUATE: API gateway HTTP client timeout >= 15s — browser session creation needs time; `compose`: smoke-static/GATEWAY_TIMEOUT_ADEQUATE: API gateway HTTP client timeout >= 15s — browser session creation needs time; `helm`: check GATEWAY_TIMEOUT_ADEQUATE not f… |

<!-- END AUTO-DOD -->

