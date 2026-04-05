---
id: test/infra-compose
type: validation
requires: []
produces: [GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE]
validates: [infrastructure]
docs: [features/infrastructure/README.md, deploy/compose/README.md, deploy/env/README.md]
mode: machine
---

# Infra Up — Compose

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

Docker Compose deployment. Each service runs in its own container on a shared
Docker network. Host ports are mapped via `.env`.

## Service Ports

| Service | Host Port | Env Var | Container |
|---------|-----------|---------|-----------|
| API Gateway | 8056 | `API_GATEWAY_HOST_PORT` | vexa-api-gateway-1 |
| Admin API | 8057 | `ADMIN_API_HOST_PORT` | vexa-admin-api-1 |
| Meeting API | 8080 | — (internal) | vexa-meeting-api-1 |
| Runtime API | 8090 | `RUNTIME_API_PORT` | vexa-runtime-api-1 |
| Agent API | 8100 | `AGENT_API_PORT` | vexa-agent-api-1 |
| Dashboard | 3001 | `DASHBOARD_HOST_PORT` | vexa-dashboard-1 |
| MCP | 18888 | — | vexa-mcp-1 |
| TTS | 8059 | — | vexa-tts-service-1 |
| Postgres | 5438 | `POSTGRES_HOST_PORT` | vexa-postgres-1 |
| MinIO | 9000 | `MINIO_HOST_PORT` | vexa-minio-1 |
| Transcription LB | 8085 | separate stack | transcription-lb |

## Steps

### 1. Fresh build + start (always rebuild — G9)

```bash
cd deploy/compose && make build && make up && make migrate-or-init
```

Or from scratch (creates .env if missing):
```bash
cd deploy/compose && make all
```

`make build` generates an immutable timestamp tag (`YYMMDD-HHMM`), saves it
to `.last-tag`, and `make up` uses it. No manual tag management needed.

**Why rebuild every time:** Stale images cause false failures. The browser_session
mode, bot config schema, and API contracts change frequently. A 3-day-old image
can crash on startup with validation errors that don't exist in current code.
Running old images wastes debugging time on already-fixed bugs (G9).

### 2. Health checks — every service must respond from host

```bash
curl -sf http://localhost:8056/           # gateway
curl -sf http://localhost:8057/admin/users -H "X-Admin-API-Key: $ADMIN_TOKEN"  # admin-api
curl -sf http://localhost:8090/health     # runtime-api
curl -sf http://localhost:8100/health     # agent-api
curl -sf http://localhost:3001/           # dashboard (compose uses 3001)
curl -sf http://localhost:8085/health     # transcription (external)
```

### 3. Inter-container connectivity

Services communicate via Docker DNS on the `vexa` network. Verify the critical paths:

```bash
# Gateway can reach meeting-api (internal, not host-exposed)
docker exec vexa-api-gateway-1 curl -sf http://meeting-api:8080/health

# Meeting-api can reach runtime-api
docker exec vexa-meeting-api-1 curl -sf http://runtime-api:8090/health

# Meeting-api can reach transcription service
docker exec vexa-meeting-api-1 curl -sf "$TRANSCRIBER_URL/../health" 2>/dev/null || \
docker exec vexa-meeting-api-1 curl -sf http://transcription-lb/health
```

> assert: inter-container calls succeed using Docker service names
> on-fail: check docker network, DNS resolution, service names in docker-compose.yml

### 4. Transcription functional check from inside

```bash
# Bots run inside vexa-bot containers on the same Docker network.
# Verify the transcription URL resolves and produces results from inside the network.
docker exec vexa-meeting-api-1 curl -sf -X POST \
  "$(docker exec vexa-meeting-api-1 printenv TRANSCRIPTION_SERVICE_URL)" \
  -H "Authorization: Bearer $(docker exec vexa-meeting-api-1 printenv TRANSCRIPTION_SERVICE_TOKEN)" \
  -F "file=@/dev/null" -F "language=en" 2>/dev/null && echo "REACHABLE" || echo "UNREACHABLE"
```

If `TRANSCRIPTION_SERVICE_URL` uses `transcription-lb` (Docker DNS), verify that
hostname resolves inside the bot containers too — bots are on the same network.

### 5. Read ADMIN_TOKEN

```bash
docker exec vexa-admin-api-1 printenv ADMIN_API_TOKEN
```

### 6. Verify Redis connectivity

```bash
docker exec vexa-redis-1 redis-cli ping   # expect PONG
```

### 7. Verify MinIO bucket

```bash
docker exec vexa-minio-1 mc ls local/vexa/ 2>/dev/null | head -3
```

## Outputs

| Name | Description |
|------|-------------|
| GATEWAY_URL | http://localhost:8056 |
| ADMIN_URL | http://localhost:8057 |
| ADMIN_TOKEN | From `docker exec vexa-admin-api-1 printenv ADMIN_API_TOKEN` |
| DEPLOY_MODE | `compose` |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| Gateway 000 | Port not mapped or container unhealthy | `make test` from `deploy/compose/` | Ports come from root `.env` |
| Transcription 000 | Separate stack not running | `docker ps` for transcription-lb/worker | Not part of compose stack — runs independently |
| Admin 404 on `/users` | Missing `X-Admin-API-Key` header | `curl -H "X-Admin-API-Key: $ADMIN_TOKEN"` | Header is `X-Admin-API-Key`, not `X-Admin-Token` |
| Browser session crashes with ZodError | Stale image | `make build && make up` | G9: always rebuild |
| Container has wrong ADMIN_TOKEN | `--env-file` didn't load root `.env` | Read actual token from container | Default is `vexa-admin-token` unless overridden |
| Inter-container DNS fails | Service not on `vexa` network | Check `docker network inspect vexa` | All services must be on the same Docker network |
| Transcription reachable from host but not from container | `TRANSCRIPTION_SERVICE_URL=localhost:8085` inside container | Use Docker DNS name (`transcription-lb`) or host.docker.internal | localhost inside a container is the container itself, not the host |

## Docs ownership

After this test runs, verify and update:

- **features/infrastructure/README.md**
  - DoD table: items #1-#6 (immutable tags, healthy services, endpoints, GPU, DB, MinIO)
  - Components table: verify `deploy/compose/` paths

- **deploy/compose/README.md**
  - Quick start: verify `make all` works
  - Service ports table: match against actual docker-compose.yml
  - Startup dependency order

- **deploy/env/README.md**
  - Verify `env-example` exists and `cp deploy/env/env-example .env` works

**Reference:** [deploy/compose/README.md](../deploy/compose/README.md)
