---
id: test/infra-lite
type: validation
requires: []
produces: [GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE]
validates: [infrastructure]
docs: [features/infrastructure/README.md, deploy/lite/README.md]
mode: machine
---

# Infra Up — Lite

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

Single container running ALL services via supervisord. Uses `--network host`
so all ports bind directly to the host. Bots run as child processes (not
Docker containers), managed by the process backend in runtime-api.

## Architecture

```
Host
├── vexa (single container, --network host)
│   ├── supervisord
│   │   ├── redis (6379)
│   │   ├── admin-api (8057)
│   │   ├── meeting-api (8080)
│   │   ├── runtime-api (8090)
│   │   ├── agent-api (8100)
│   │   ├── api-gateway (8056)     ← main entry point
│   │   ├── dashboard (3000)
│   │   ├── mcp (18888)
│   │   ├── tts-service (8059)
│   │   ├── xvfb (:99)
│   │   ├── pulseaudio
│   │   ├── x11vnc (5900)
│   │   └── websockify (6080)
│   └── bot child processes (spawned by runtime-api)
│       ├── node /app/vexa-bot/dist/docker.js (bot 1)
│       ├── node /app/vexa-bot/dist/docker.js (bot 2)
│       └── ...
├── postgres (from compose, port 5438)  ← external
├── minio (from compose, port 9000)     ← external
└── transcription-lb (port 8085)        ← external
```

## Service Ports

| Service | Port | Notes |
|---------|------|-------|
| API Gateway | 8056 | Main entry point |
| Admin API | 8057 | User/token management |
| Meeting API | 8080 | Bot orchestration |
| Runtime API | 8090 | Process backend (no Docker socket) |
| Agent API | 8100 | AI agent runtime |
| Dashboard | 3000 | Next.js web UI (**3000, not 3001 like compose**) |
| MCP | 18888 | Model Context Protocol |
| TTS | 8059 | Text-to-speech (Piper, local) |
| Redis | 6379 | Internal (managed by supervisord) |
| Xvfb | :99 | Virtual display for headless browsers |
| Chrome CDP | 9222 | Browser remote debugging (**not 9223**) |

## Prerequisites

- External PostgreSQL running (e.g. compose Postgres on port 5438)
- External transcription service running (e.g. transcription-lb on port 8085)
- External MinIO running (e.g. compose MinIO on port 9000)
- Ports 8056, 8057, 8080, 8090, 8100, 3000, 18888, 8059 free on host
  (stop compose app services and traefik if they hold these ports)

## Steps

### 1. Start external dependencies (if not running)

```bash
cd deploy/compose && docker compose up -d postgres minio
```

### 2. Fresh build with immutable tag (always rebuild — G9)

```bash
cd /path/to/repo
TAG=$(date +%y%m%d-%H%M)
docker build -f deploy/lite/Dockerfile.lite -t vexa-lite:$TAG .
echo "Built vexa-lite:$TAG"
```

Never use `:dev` or `:latest` — the tag must be immutable so you know
exactly which code is running inside the container.

### 3. Stop conflicting services (if compose app is running)

```bash
docker compose -f deploy/compose/docker-compose.yml stop \
  admin-api agent-api api-gateway dashboard mcp meeting-api \
  runtime-api telegram-bot tts-service redis
docker stop traefik 2>/dev/null || true   # holds port 8080
```

Verify ports are free:
```bash
ss -tlnp | grep -E ':(8056|8057|8080|8090|8100|3000|18888|8059)' && echo "PORTS BUSY" || echo "PORTS FREE"
```

### 4. Start lite container

```bash
docker rm -f vexa 2>/dev/null || true
docker run -d \
  --name vexa \
  --shm-size=2g \
  --network host \
  -e DATABASE_URL="postgresql://postgres:postgres@localhost:5438/vexa" \
  -e DB_HOST="localhost" \
  -e DB_PORT="5438" \
  -e DB_NAME="vexa" \
  -e DB_USER="postgres" \
  -e DB_PASSWORD="postgres" \
  -e ADMIN_API_TOKEN="changeme" \
  -e TRANSCRIBER_URL="http://localhost:8085/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="$TRANSCRIPTION_SERVICE_TOKEN" \
  -e SKIP_TRANSCRIPTION_CHECK="false" \
  -e MINIO_ENDPOINT="localhost:9000" \
  -e MINIO_ACCESS_KEY="vexa-access-key" \
  -e MINIO_SECRET_KEY="vexa-secret-key" \
  -e MINIO_BUCKET="vexa" \
  -e MINIO_SECURE="false" \
  -e STORAGE_BACKEND="minio" \
  -e LOG_LEVEL="info" \
  vexa-lite:$TAG
```

**Verify the tag matches what you just built:**
```bash
docker inspect vexa --format '{{.Config.Image}}'   # must show vexa-lite:$TAG
```

### 5. Verify container refuses to start without transcription

The entrypoint must `exit 1` if it can't transcribe. Test this BEFORE the happy path:

```bash
# Bad URL → must fail
docker rm -f vexa-test 2>/dev/null || true
docker run --rm --name vexa-test \
  --network host \
  -e DATABASE_URL="postgresql://postgres:postgres@localhost:5438/vexa" \
  -e DB_HOST="localhost" -e DB_PORT="5438" -e DB_NAME="vexa" \
  -e DB_USER="postgres" -e DB_PASSWORD="postgres" \
  -e ADMIN_API_TOKEN="changeme" \
  -e TRANSCRIBER_URL="http://localhost:9999/v1/audio/transcriptions" \
  -e SKIP_TRANSCRIPTION_CHECK="false" \
  vexa-lite:$TAG 2>&1 | tail -5
# expect: "ERROR: Transcription service not reachable" + exit code 1

echo "Exit code: $?"   # must be non-zero
```

```bash
# Missing URL → must fail
docker rm -f vexa-test 2>/dev/null || true
docker run --rm --name vexa-test \
  --network host \
  -e DATABASE_URL="postgresql://postgres:postgres@localhost:5438/vexa" \
  -e DB_HOST="localhost" -e DB_PORT="5438" -e DB_NAME="vexa" \
  -e DB_USER="postgres" -e DB_PASSWORD="postgres" \
  -e ADMIN_API_TOKEN="changeme" \
  -e TRANSCRIBER_URL="" \
  vexa-lite:$TAG 2>&1 | tail -5
# expect: "ERROR: TRANSCRIBER_URL is not set" + exit code 1
```

```bash
# Skip flag → must start despite bad URL
docker rm -f vexa-test 2>/dev/null || true
docker run -d --name vexa-test \
  --network host --shm-size=2g \
  -e DATABASE_URL="postgresql://postgres:postgres@localhost:5438/vexa" \
  -e DB_HOST="localhost" -e DB_PORT="5438" -e DB_NAME="vexa" \
  -e DB_USER="postgres" -e DB_PASSWORD="postgres" \
  -e ADMIN_API_TOKEN="changeme" \
  -e TRANSCRIBER_URL="http://localhost:9999/bad" \
  -e SKIP_TRANSCRIPTION_CHECK="true" \
  vexa-lite:$TAG
sleep 5
docker logs vexa-test 2>&1 | grep -i "skipping transcription"  # must appear
docker rm -f vexa-test
```

> assert: container exits 1 with clear error when transcription is unreachable
> assert: container exits 1 when TRANSCRIBER_URL is empty
> assert: container starts when SKIP_TRANSCRIPTION_CHECK=true
> on-fail: entrypoint.sh transcription check is broken — fix it before proceeding

### 6. Start lite container (happy path)

Now start the real container with valid transcription config:

```bash
docker rm -f vexa 2>/dev/null || true
docker run -d \
  --name vexa \
  --shm-size=2g \
  --network host \
  -e DATABASE_URL="postgresql://postgres:postgres@localhost:5438/vexa" \
  -e DB_HOST="localhost" -e DB_PORT="5438" -e DB_NAME="vexa" \
  -e DB_USER="postgres" -e DB_PASSWORD="postgres" \
  -e ADMIN_API_TOKEN="changeme" \
  -e TRANSCRIBER_URL="http://localhost:8085/v1/audio/transcriptions" \
  -e TRANSCRIBER_API_KEY="$TRANSCRIPTION_SERVICE_TOKEN" \
  -e SKIP_TRANSCRIPTION_CHECK="false" \
  -e MINIO_ENDPOINT="localhost:9000" \
  -e MINIO_ACCESS_KEY="vexa-access-key" \
  -e MINIO_SECRET_KEY="vexa-secret-key" \
  -e MINIO_BUCKET="vexa" \
  -e MINIO_SECURE="false" \
  -e STORAGE_BACKEND="minio" \
  -e LOG_LEVEL="info" \
  vexa-lite:$TAG
```

Verify transcription check passed:
```bash
sleep 10
docker logs vexa 2>&1 | grep -i "Transcription OK"   # must appear
```

> assert: logs show "Transcription OK: ..." with actual transcribed text
> on-fail: entrypoint check failed despite valid service — check TRANSCRIBER_URL, TRANSCRIBER_API_KEY

### 7. Wait for supervisor services

```bash
sleep 15   # remaining startup time after transcription check
RUNNING=$(docker logs vexa 2>&1 | grep -c 'entered RUNNING state')
echo "$RUNNING services running"
# expect ≥12 (14 total: redis, xvfb, pulseaudio, x11vnc, websockify,
# admin-api, meeting-api, runtime-api, agent-api, api-gateway, dashboard, mcp, tts-service, pa-sinks)
```

> assert: ≥12 services in RUNNING state
> on-fail: `docker logs vexa 2>&1 | grep -E 'FATAL|ERROR|BACKOFF'` to find which service failed

### 8. Health checks — every service from host

```bash
curl -sf http://localhost:8056/           # gateway
curl -sf http://localhost:8057/admin/users -H "X-Admin-API-Key: changeme"  # admin-api
curl -sf http://localhost:8080/health     # meeting-api (directly, not via gateway)
curl -sf http://localhost:8090/health     # runtime-api
curl -sf http://localhost:8100/health     # agent-api
curl -sf http://localhost:3000/           # dashboard
curl -sf http://localhost:18888/docs      # mcp
curl -sf http://localhost:8059/health     # tts-service
curl -sf http://localhost:8085/health     # transcription (external)
```

> assert: ALL endpoints return 200
> on-fail: identify which service failed, check supervisor logs

### 9. Verify post-startup self-check passed

The container validates its own internal connectivity ~20s after supervisor
starts. No `docker exec` needed — the check runs inside the container.

```bash
docker logs vexa 2>&1 | grep -A 15 "Post-Startup Health Validation"
```

Expected:
```
  OK: API Gateway
  OK: Meeting API
  OK: Runtime API
  OK: Agent API
  OK: Dashboard
  OK: TTS Service
  OK: Redis
  OK: Transcription

  ALL SERVICES HEALTHY
```

> assert: logs show "ALL SERVICES HEALTHY"
> on-fail: logs show which service FAILed — check supervisor for that service

**Why self-check, not `docker exec`:** External `docker exec curl` proves
the host can reach the container. But bots and services communicate from
INSIDE. The post-startup script runs the same network paths services use.
If it passes, the internal wiring works.

### 10. WebSocket smoke test

```bash
# Basic WS connectivity — dashboard depends on this for live transcripts
node -e "
const ws = new (require('ws'))('ws://localhost:8056/ws?api_key=TEST');
ws.on('open', () => { ws.send(JSON.stringify({action:'ping'})); });
ws.on('message', d => { console.log('WS:', d.toString()); ws.close(); process.exit(0); });
ws.on('error', e => { console.error('WS ERROR:', e.message); process.exit(1); });
setTimeout(() => { console.error('WS TIMEOUT'); process.exit(1); }, 5000);
" 2>&1 || echo "WS check failed — install ws: npm i -g ws"
```

> assert: WS connects and returns pong
> on-fail: gateway WS endpoint broken — dashboard will show no live data

### 11. Zombie process baseline

Record zombie count before any bots are spawned. This feeds into
task 14 (container-lifecycle) which checks for zombie accumulation.

```bash
ZOMBIES=$(docker exec vexa ps aux | grep -c '[Z]' || echo 0)
echo "Zombie baseline: $ZOMBIES"
```

> assert: 0 zombies at startup
> on-fail: stale processes from previous container run. Known bug: process
>          backend reaper doesn't detect zombies (task #20).

### 12. Read ADMIN_TOKEN

```bash
docker exec vexa printenv ADMIN_API_TOKEN
```

## Outputs

| Name | Description |
|------|-------------|
| GATEWAY_URL | http://localhost:8056 |
| ADMIN_URL | http://localhost:8057 |
| ADMIN_TOKEN | From `docker exec vexa printenv ADMIN_API_TOKEN` |
| DEPLOY_MODE | `lite` |

## Lite-specific known issues

These are issues specific to the lite deployment that don't affect compose:

| Issue | Impact | Status | Task |
|-------|--------|--------|------|
| Zombie reaper bug | Dead bot processes reported as "active" | Open | #20 |
| CDP gateway port mismatch | Gateway proxies to 9223, Chrome on 9222 | Open | #21 |
| Process backend no Docker socket | Can't use `docker ps` for container lifecycle | By design | — |
| All services share one container | One crash can affect all services | By design | — |
| Dashboard on port 3000 (not 3001) | Different from compose | By design | — |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| Container exits immediately | entrypoint.sh failed — DB unreachable or transcription check failed | `docker logs vexa` for error | entrypoint.sh now exits on transcription failure (not just warning) |
| ≤10 services RUNNING | Some service crashed on startup | `docker logs vexa 2>&1 \| grep BACKOFF` | Check which service entered BACKOFF state |
| "Process backend requires spec.command" | Stale profiles.yaml baked into image | Rebuild image | G9: lite bakes ALL code — always rebuild |
| Port 8080 already in use | Traefik holds the port | `docker stop traefik` | `ss -tlnp \| grep 8080` to find culprit |
| Host health OK but inside-container fails | `--network host` not set, or env var points to wrong host | Verify `docker run` used `--network host` | Without `--network host`, localhost inside container ≠ host |
| Transcription health OK but WAV fails | API key wrong or transcription URL path wrong | Check TRANSCRIBER_URL includes `/v1/audio/transcriptions` | Health endpoint doesn't require auth, transcription does |
| Zombies at startup | Previous container left orphan processes | `docker rm -f vexa` before starting | Always `docker rm -f` the old container first |
| supervisorctl "no such file" | No `[unix_http_server]` in supervisord.conf | Use `docker logs vexa` instead of supervisorctl | supervisorctl requires a socket not configured in lite |
| CDP proxy returns 502 | Gateway hardcodes port 9223, Chrome on 9222 in lite | Use CDP directly on port 9222, or socat 9223→9222 | Bug #21 — compose maps 9222→9223, lite has no mapping |

## Docs ownership

After this test runs, verify and update:

- **features/infrastructure/README.md**
  - DoD table: items #1-#6 (immutable tags, healthy services, endpoints, GPU, DB, MinIO)
  - Components table: verify `deploy/lite/` paths

- **deploy/lite/README.md**
  - Quick Start: verify `docker build -f deploy/lite/Dockerfile.lite` works
  - Services table: verify port assignments match actual supervisord config
  - Environment variables: verify all required vars match what the container reads
  - Architecture diagram: verify against actual `docker exec vexa supervisorctl status`
