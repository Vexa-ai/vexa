# Production Readiness Gates

## Why

"Tests pass" doesn't mean the system works. Unit tests with mocks prove code doesn't crash — they don't prove containers start, services connect, data flows, or users see results. These gates define what "ready" actually means for each component, with emphasis on Docker deployment because that's how everything runs.

## Gate Levels

| Level | What it proves | How |
|-------|---------------|-----|
| **G0: Build** | Code compiles into a container | `docker build` succeeds |
| **G1: Start** | Container starts and responds | Container health check passes within 30s |
| **G2: Connect** | Service connects to its dependencies | Health endpoint reports all dependencies healthy |
| **G3: CRUD** | Core operations work through real infrastructure | Create → Read → Update → Delete against real DB/Redis |
| **G4: Contract** | API responses match frozen shapes | Contract tests pass against running service (not mocks) |
| **G5: Flow** | Data flows end-to-end through the service | Input at one end → output at the other, verified |
| **G6: Failure** | Service handles failures gracefully | Kill dependency, verify degradation not crash |

A component at G0 builds but might not start. At G3 it handles requests but might return wrong shapes. At G5 the full pipeline works. G6 means it's production-ready.

---

## runtime-api

**Dependencies:** Redis, Docker socket (or K8s API, or nothing for Process)

| Gate | Test | Pass criteria |
|------|------|--------------|
| G0 | `docker build -t runtime-api:gate services/runtime-api/` | Exit 0 |
| G1 | Start runtime-api + Redis via `docker-compose.yml`. `curl /health` | `{"status": "ok"}` within 30s |
| G2 | Health reports Redis connected | Health response includes container count (proves Redis read works) |
| G3 | `POST /containers` → `GET /containers` → `DELETE /containers/{name}` | Container appears in list after create, disappears after delete |
| G3 | `POST /containers/{name}/touch` → verify `updated_at` changed | Heartbeat resets idle timer |
| G3 | `GET /profiles` returns loaded profiles | At least 1 profile with valid structure |
| G4 | Container response has: `name`, `status`, `profile`, `user_id`, `created_at`, `ports`, `metadata` | All fields present and correctly typed |
| G5 | Create container → wait for idle timeout → verify callback fires | Container auto-stops, callback URL receives POST with exit_code |
| G5 | Create container → container exits on its own → callback fires | Exit event detected via Docker events, callback delivered |
| G5 | `POST /schedule` → job fires at scheduled time → HTTP callback received | Scheduler end-to-end |
| G6 | Kill Redis → runtime-api stays up, returns 503 on writes | Doesn't crash, reconnects when Redis comes back |
| G6 | Create container → kill Docker daemon → runtime-api detects, marks stopped | Reconcile-on-restart handles orphans |

**Current level: G3** (create/list/delete verified against real Docker, but no callback or failure testing)

---

## meeting-api

**Dependencies:** Redis, PostgreSQL, runtime-api

| Gate | Test | Pass criteria |
|------|------|--------------|
| G0 | `docker build -f services/meeting-api/Dockerfile -t meeting-api:gate .` | Exit 0 |
| G1 | Start meeting-api + Redis + Postgres + runtime-api. `curl /health` | `{"status": "ok"}` within 30s, DB tables created |
| G2 | Health reports Redis + Postgres + runtime-api connected | All three dependencies reachable |
| G3 | Create user token (via admin-api or X-User-ID header) → `POST /bots` → `GET /bots/status` → `DELETE /bots/{platform}/{id}` | Meeting created in Postgres, appears in status, deleted cleanly |
| G3 | `POST /bots/internal/callback/started` → meeting status transitions to ACTIVE | Callback updates DB correctly |
| G3 | `POST /bots/internal/callback/exited` with exit_code=0 → COMPLETED | Terminal status set, end_time populated |
| G4 | `GET /bots/status` response matches frozen contract: `{"running_bots": [{meeting_id_from_name, container_name, ...}]}` | All 11 frozen fields present |
| G4 | Callback payloads match `BotExitCallbackPayload`, `BotStartupCallbackPayload`, `BotStatusChangePayload` | Field names and types match contracts |
| G4 | Webhook delivery envelope: `{event_id, event_type, api_version, created_at, data}` | 5-key envelope, HMAC signature correct |
| G5 | `POST /bots` → runtime-api spawns container → bot sends `/callback/started` → status is ACTIVE → send `/callback/exited` → status is COMPLETED → post-meeting hooks fire | Full bot lifecycle through real infrastructure |
| G5 | Transcription collector: publish segment to Redis stream → collector consumes → writes to Postgres → `GET /transcripts` returns it | Full transcription pipeline inside meeting-api |
| G5 | Voice agent: `POST /speak` → Redis pub/sub message published → bot container receives it | Voice command reaches the bot |
| G6 | Kill Postgres → meeting-api returns 503, doesn't crash → restart Postgres → recovers | Graceful degradation |
| G6 | Kill runtime-api → `POST /bots` returns error → restart → works again | Dependency failure handling |

**Current level: G1** (Docker image builds and starts, but no CRUD tested against real infrastructure)

---

## admin-api

**Dependencies:** PostgreSQL

| Gate | Test | Pass criteria |
|------|------|--------------|
| G0 | `docker build -f services/admin-api/Dockerfile -t admin-api:gate .` | Exit 0 |
| G1 | Start admin-api + Postgres. `curl /` | Returns JSON within 30s |
| G2 | Health reports Postgres connected | Users table queryable |
| G3 | `POST /admin/users` → `GET /admin/users` → `PUT /admin/users/{id}` → `DELETE /admin/users/{id}` | Full CRUD cycle |
| G3 | `POST /admin/users/{id}/tokens` → token returned → use token for API calls | Token issuance works |
| G3 | `POST /internal/validate` with valid token → returns user_id, scopes | Token validation for gateway |
| G4 | Scoped tokens: `vxa_bot_` → scopes=["bot"], `vxa_user_` → scopes=["user"] | Scope extraction correct |
| G4 | Legacy token → scopes=["legacy"] (not admin) | No privilege escalation |
| G5 | Gateway calls `/internal/validate` → gets user info → injects headers → downstream receives headers | Full auth flow through gateway |
| G6 | `INTERNAL_API_SECRET` unset + `DEV_MODE=false` → `/internal/validate` returns 503 | Fail-closed in production |
| G6 | Kill Postgres → admin-api returns 503, reconnects when DB comes back | Graceful degradation |

**Current level: G3** (CRUD verified against real Postgres in compose stack)

---

## api-gateway

**Dependencies:** admin-api, meeting-api, Redis (for WebSocket + token cache)

| Gate | Test | Pass criteria |
|------|------|--------------|
| G0 | `docker build -f services/api-gateway/Dockerfile -t gateway:gate .` | Exit 0 |
| G1 | Start gateway + all backends. `curl /` | Returns welcome JSON within 30s |
| G2 | All route targets reachable | `/bots/status`, `/admin/users`, `/meetings` all proxy successfully |
| G3 | `POST /bots` via gateway → meeting-api receives correct headers (X-User-ID injected) | Header injection works end-to-end |
| G3 | `GET /bots/status` via gateway → returns meeting-api response unmodified | Proxy transparency |
| G3 | Token cache: first request validates via admin-api, second request serves from Redis cache | Cache hit (check admin-api logs: only 1 validate call for 2 requests) |
| G4 | Spoofed `X-User-ID` header in request → stripped before forwarding | Security: client cannot inject identity |
| G4 | Invalid API key → 401/403 (not proxied to backend) | Auth enforcement at gateway |
| G5 | WebSocket: connect → subscribe to meeting → publish to Redis `tc:meeting:{id}:mutable` → message delivered to WS client | Real-time pipeline through gateway |
| G5 | Full flow: create user → create token → create bot → get status → get transcript → all via gateway | Complete API surface works |
| G6 | Kill meeting-api → gateway returns 502 → restart meeting-api → gateway recovers | Backend failure handling |
| G6 | Kill Redis → token cache disabled, gateway falls through to admin-api for every request | Cache failure doesn't break auth |

**Current level: G2** (routes to backends, but no header injection or WebSocket verified against real infrastructure)

---

## agent-api

**Dependencies:** Redis, runtime-api

| Gate | Test | Pass criteria |
|------|------|--------------|
| G0 | `docker build -f services/agent-api/Dockerfile -t agent-api:gate .` | Exit 0 |
| G1 | Start agent-api + Redis + runtime-api. `curl /health` | `{"status": "ok"}` within 30s |
| G2 | Health reports Redis connected, runtime-api reachable | Both dependencies OK |
| G3 | `POST /api/sessions` → creates session in Redis → `GET /api/sessions/{id}` → returns it → `DELETE /api/sessions/{id}` | Session CRUD |
| G3 | `POST /api/workspace/file` → `GET /api/workspace/files` → file listed → `GET /api/workspace/file?path=...` → content returned | Workspace CRUD |
| G4 | Chat response is SSE stream with correct event types | `event: text`, `event: tool_use`, `event: result` |
| G5 | Create session → runtime-api spawns container → `POST /api/chat` → exec runs in container → SSE response streams back | Full chat pipeline |
| G5 | Workspace: upload file → file appears in container → modify in container → download reflects changes | Bidirectional workspace sync |
| G6 | Kill runtime-api → agent-api returns error for session creation → restart → works | Dependency failure |
| G6 | Container dies mid-chat → SSE stream closes cleanly with error event | Graceful stream termination |

**Current level: G0** (Docker image builds but never verified to start against real infra)

---

## Full Stack

The system is production-ready when ALL components pass G5 AND this integration gate passes:

| Gate | Test | Pass criteria |
|------|------|--------------|
| **Stack G0** | `make build` (all images) | All images build without error |
| **Stack G1** | `make up` | All services healthy within 60s, 0 restarts |
| **Stack G2** | `make test` | Gateway routes to all backends, health checks pass |
| **Stack G3** | Create user → token → bot → status → stop | Full meeting lifecycle via gateway |
| **Stack G4** | All 98 contract tests pass against running services | Frozen API shapes verified live |
| **Stack G5** | Bot joins meeting → transcribes → segments in DB → available via API → WebSocket delivers live segments → recording downloadable | End-to-end meeting transcription |
| **Stack G6** | Kill and restart each service one at a time → system recovers | No single point of failure (except Postgres/Redis) |

**Current level: Stack G2** (services start and route, but no meeting lifecycle tested)

---

## Execution Order

The gates build on each other. Run them bottom-up:

```
Week 1: Every component to G1 (build + start + health)
         ├── runtime-api: already G3 ✓
         ├── admin-api: already G3 ✓
         ├── meeting-api: needs real Postgres start test
         ├── agent-api: needs Docker start test
         └── gateway: already G2 ✓

Week 2: Every component to G3 (CRUD against real infra)
         ├── meeting-api: POST /bots against real Postgres + runtime-api
         ├── agent-api: session CRUD against real Redis
         └── gateway: header injection verified end-to-end

Week 3: Every component to G5 (data flows end-to-end)
         ├── meeting-api: full bot lifecycle + transcription pipeline
         ├── agent-api: chat streaming through real container
         ├── gateway: WebSocket live delivery
         └── Stack G5: full meeting transcription end-to-end

Week 4: G6 (failure handling) + polish
         ├── Kill-and-recover tests for each service
         ├── Load testing
         └── Dashboard verification
```

## How to Run Gates

Each gate should be runnable as a single command:

```bash
# Per-component
make -C services/runtime-api gate-g0    # build
make -C services/runtime-api gate-g1    # build + start + health
make -C services/runtime-api gate-g3    # build + start + CRUD
make -C services/runtime-api gate-g5    # build + start + full flow

# Full stack
make gate-stack-g1    # all services start
make gate-stack-g3    # meeting lifecycle works
make gate-stack-g5    # transcription end-to-end
```

These commands should:
1. Start dependencies (docker-compose up)
2. Wait for health
3. Run the gate tests (pytest or curl scripts)
4. Report PASS/FAIL per gate
5. Clean up (docker-compose down)
