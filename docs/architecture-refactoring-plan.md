# Architecture Refactoring Plan

## Executive Summary

Refactor Vexa from a tangled monolith (original bot-manager doing 5 jobs) into a clean layered architecture with two clear boundaries:

- **Domain layer**: Meeting API, Agent API, Admin API — business logic, no container knowledge
- **Infrastructure layer**: Runtime API — generic container orchestration, no meeting/agent knowledge

Bot-manager was deleted. Its orchestration code merged into Runtime API. Its meeting domain code became Meeting API.

**Decision: Build, not buy.** Research across 10 alternatives (Nomad, Knative, Fly Machines, Temporal, Argo, KEDA, Modal, E2B, K8s Jobs, serverless) confirms domain-specific requirements kill generic solutions. Recall.ai (8M EC2 instances/month) built fully custom, validating this approach.

---

## Constraints

These are non-negotiable. Every design decision must respect them.

### 1. API contracts are frozen

External API paths and request/response schemas **must not change**:

```
POST   /bots                    → stays as /bots (not /meetings)
GET    /bots/{id}/status        → stays
DELETE /bots/{platform}/{id}    → stays
GET    /transcripts             → stays
GET    /meetings                → stays
GET    /recordings/{id}         → stays
POST   /bots/{id}/speak         → stays
POST   /bots/{id}/chat          → stays
WS     /ws/subscribe            → stays
```

The refactoring is **internal wiring only**. API Gateway routes to different services behind the same paths. External clients, Dashboard, Telegram Bot, Calendar Service — nothing changes for them.

### 2. Three deployment backends

| Backend | Use Case | How |
|---------|----------|-----|
| **Process** | Vexa Lite (single container) | All services + bot as child processes. No Docker socket needed. |
| **Docker** | Local dev, standard deployment | Docker socket, containers on same host |
| **K8s** | Production | Pods, ServiceAccount RBAC, resource limits, node selectors |

Runtime API must support all three through one backend interface. The backend is selected by config (`ORCHESTRATOR_BACKEND=process|docker|kubernetes`), not code changes.

### 3. No new infrastructure requirements

The refactoring must not add new infrastructure dependencies. Current stack:
- PostgreSQL
- Redis
- S3/MinIO (optional)
- Docker socket or K8s API (depending on backend)

No Temporal, no Argo, no message queues, no new databases. These can be evaluated later but are not part of this refactoring.

### 4. Incremental migration

Every phase must be independently deployable. At no point should the system be in a broken state. Old and new paths coexist during migration via feature toggles, not big-bang switchover.

---

## Target Architecture

```
                         ┌─────────────┐
                         │   Dashboard  │
                         │   (Next.js)  │
                         └──────┬───────┘
                                │
                         ┌──────▼───────┐
                         │ API Gateway  │  ← public entry point
                         └──────┬───────┘
                                │
         ┌──────────────────────┼──────────────────────┐
         │                      │                      │
  ┌──────▼──────┐       ┌──────▼──────┐       ┌───────▼─────┐
  │  Admin API  │       │ Meeting API │       │  Agent API   │
  │             │       │             │       │              │
  │ • users     │       │ • join/stop │       │ • chat (SSE) │
  │ • tokens    │       │ • status    │       │ • workspaces │
  │ • analytics │       │ • speak     │       │ • scheduling │
  │             │       │ • chat      │       │ • TTS integ. │
  │             │       │ • screen    │       │              │
  │             │       │ • recordings│       │              │
  │             │       │ • webhooks  │       │              │
  └─────────────┘       └──────┬──────┘       └───────┬──────┘
                               │                      │
                   ════════════╪══════════════════════╪═══════════
                    domain     │                      │
                    ───────────┼──────────────────────┘
                    infra      │
                               │
                        ┌──────▼──────┐
                        │ Runtime API │  ← internal only (not in gateway)
                        │             │
                        │  Container  │
                        │  as a       │
                        │  Service    │
                        │             │
                        │ • CRUD      │
                        │ • profiles  │
                        │ • backends  │
                        │ • lifecycle │
                        │ • callbacks │
                        │ • exec      │
                        │ • idle mgmt │
                        │ • concurr.  │
                        └──────┬──────┘
                               │
                      ┌────────┼────────┐
                      │        │        │
                ┌─────▼──┐ ┌──▼───┐ ┌──▼──────┐
                │vexa-bot│ │agent │ │ browser │
                │        │ │      │ │         │
                │Playwr. │ │Claude│ │Chromium │
                │browser │ │Code +│ │CDP/VNC  │
                │meeting │ │CLI   │ │         │
                └────┬───┘ └──────┘ └─────────┘
                     │
             ┌───────┴────────┐
             ▼                ▼
  ┌──────────────┐  ┌─────────────────┐
  │ Transcription│  │ Transcription   │
  │  Collector   │  │   Service       │
  │ (Redis → DB) │  │ (Whisper API)   │
  └──────────────┘  └─────────────────┘
```

### Supporting Services

```
  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
  │ TTS Service  │  │   Calendar   │  │   Telegram   │
  │ (speech      │  │   Service    │  │     Bot      │
  │  synthesis)  │  │ (Cal sync)   │  │ (mobile)     │
  └──────────────┘  └──────────────┘  └──────────────┘

  ┌──────────────┐  ┌──────────────┐
  │  MCP Server  │  │  Transcript  │
  │ (AI tools)   │  │  Rendering   │
  │              │  │  (TS lib)    │
  └──────────────┘  └──────────────┘
```

### Infrastructure

```
  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
  │  PostgreSQL  │  │    Redis     │  │   S3/MinIO   │
  │              │  │              │  │              │
  │ • meetings   │  │ • streams    │  │ • recordings │
  │ • users      │  │ • pub/sub    │  │ • audio      │
  │ • transcripts│  │ • state      │  │ • video      │
  │ • tokens     │  │ • scheduler  │  │              │
  │ • recordings │  │ • bot cmds   │  │              │
  └──────────────┘  └──────────────┘  └──────────────┘
```

---

## Service Specifications

### Runtime API — Container-as-a-Service

**Purpose:** Generic container orchestration. Knows nothing about meetings, agents, or any domain concept. Its vocabulary is containers, profiles, and backends.

**Endpoints:**

```
POST   /containers              Create and start a container
GET    /containers              List containers (?profile=&user_id=)
GET    /containers/{name}       Inspect container (status, ports, metadata)
DELETE /containers/{name}       Stop and remove container
POST   /containers/{name}/touch Heartbeat (resets idle timer)
POST   /containers/{name}/exec  Execute command inside container
GET    /containers/{name}/wait  Long-poll until target state reached
```

**Profile System:**

Profiles are declarative container templates. Domain services reference by name.

```python
profiles = {
    "meeting": {
        "image": "vexa-bot:latest",
        "resources": {"cpu": "1", "memory": "2Gi"},
        "idle_timeout": 0,          # meetings don't auto-stop
        "auto_remove": True,
        "ports": {"9223": "cdp"},
        "gpu": True,                # optional hardware accel
    },
    "agent": {
        "image": "vexa-agent:latest",
        "resources": {"cpu": "0.5", "memory": "1Gi"},
        "idle_timeout": 900,        # 15min
        "auto_remove": False,       # reuse across sessions
        "mounts": ["/workspace"],
        "one_per_user": True,
    },
    "browser": {
        "image": "chromium-cdp:latest",
        "resources": {"cpu": "1", "memory": "2Gi"},
        "idle_timeout": 600,        # 10min
        "ports": {"9223": "cdp", "6080": "vnc"},
    },
}
```

**Backend Abstraction:**

```
┌─────────────────────────────────────┐
│           Backend Interface          │
│                                      │
│  create(spec) → container_id         │
│  start(id)                           │
│  stop(id)                            │
│  remove(id)                          │
│  inspect(id) → status, ports, meta   │
│  list(labels) → containers[]         │
│  exec(id, cmd) → stream              │
└──────┬──────────┬───────────┬────────┘
       │          │           │
  ┌────▼───┐ ┌───▼────┐ ┌────▼─────┐
  │ Docker │ │  K8s   │ │ Process  │
  │ socket │ │  pods  │ │ child    │
  └────────┘ └────────┘ └──────────┘
```

**State Management:**
- Redis for fast queries (container status, ports, metadata, idle timers)
- Reconciliation loop syncs Redis with backend reality on startup
- TTL on stopped entries (24h)

**Lifecycle Callbacks:**
- Caller provides `callback_url` at container creation
- Runtime API POSTs to callback when container exits, fails, or starts
- Payload: `{name, profile, status, exit_code, metadata}`

**Concurrency:**
- Per-user, per-profile limits (configurable)
- Checked at creation time, rejected with 429 if exceeded

**What merged in from bot-manager:**
- `orchestrators/kubernetes.py` → K8s backend
- `orchestrators/process.py` → process backend
- GPU passthrough logic from `orchestrator_utils.py`
- Port allocation logic (CDP, VNC)

**What merges in from current runtime-api:**
- Profile system
- Idle management loop
- Redis state + reconciliation
- Docker backend (`docker_ops.py`)

### Meeting API — Meeting Domain

**Purpose:** Meeting lifecycle, voice agent control, recordings, webhooks. Uses Runtime API for all container operations.

**Port:** 8080 (inherited from bot-manager)

**Constraint: all existing endpoint paths are preserved.** Gateway routes `/bots/*` to meeting-api (previously bot-manager).

**Public Endpoints (unchanged paths):**

```
# Meeting lifecycle
POST   /bots                                    Create meeting + spawn bot
GET    /bots                                     List active bots
GET    /bots/{meeting_id}/status                 Get meeting status
DELETE /bots/{platform}/{native_meeting_id}       Stop bot
PUT    /bots/{platform}/{native_meeting_id}/config Update bot config

# Voice agent control
POST   /bots/{platform}/{native_meeting_id}/speak   TTS → play in meeting
POST   /bots/{platform}/{native_meeting_id}/chat    Send chat message
POST   /bots/{platform}/{native_meeting_id}/screen  Share screen content

# Recordings
GET    /recordings                               List recordings
GET    /recordings/{id}                           Get recording
GET    /recordings/{id}/raw                       Stream recording file
DELETE /recordings/{id}                           Delete recording

# Meetings (read-only, from transcription collector today)
GET    /meetings                                 List meetings
GET    /meetings/{id}                            Get meeting details
```

**Internal Endpoints (from container callbacks):**

```
POST   /bots/internal/callback/status            Bot status change
POST   /bots/internal/callback/exited            Bot container exited
POST   /bots/internal/callback/admission         Bot admission event
POST   /internal/recordings/upload               Recording upload from bot
```

**What moved here from bot-manager:**
- Meeting CRUD (`POST /bots` → `POST /meetings`)
- Status state machine (requested → joining → awaiting_admission → active → completed)
- Voice agent endpoints (/speak, /chat, /screen, /avatar)
- Recording management
- Webhook delivery (HMAC signing, retry, delivery history)
- Post-meeting hooks (aggregate transcription, send webhook)
- Meeting token minting (HS256 JWT for bot auth)
- Concurrency check delegation to Runtime API

**What it does NOT do:**
- No Docker/K8s operations (delegates to Runtime API)
- No container lifecycle management
- No knowledge of how containers work

### Agent API — Agent Domain

**Purpose:** AI agent chat sessions, workspace management, scheduling. Uses Runtime API for containers.

**Port:** 8100 (unchanged)

**Already clean.** Agent API already delegates container lifecycle to Runtime API and does local `docker exec` for streaming. Minimal changes needed.

**Key design:** `docker exec` stays local (not through Runtime API). Streaming CLI output over an HTTP hop adds latency for no benefit. Agent API needs Docker socket access for exec only.

### Admin API — Identity & Access

**Port:** 8001 (unchanged)

No changes. Already clean.

### API Gateway — Entry Point

**Port:** 8000 (unchanged)

**Routing (all paths unchanged):**

```
/bots/*         → Meeting API (8080)      # was bot-manager, now meeting-api (done)
/recordings/*   → Meeting API (8080)      # was bot-manager (done)
/admin/*        → Admin API (8001)        # unchanged
/api/chat/*     → Agent API (8100)        # unchanged
/transcripts/*  → Transcription Collector (8002)  # unchanged
/meetings/*     → Transcription Collector (8002)  # unchanged (read-only)
/calendar/*     → Calendar Service (8085) # unchanged
```

**Runtime API is NOT routed through gateway.** Internal only — domain services call it directly.

---

## Data Flows

### Meeting Transcription

```
User → Gateway → Meeting API: POST /meetings {meeting_url}
                 Meeting API → Runtime API: POST /containers {profile: "meeting"}
                               Runtime API → Docker/K8s: create vexa-bot
                               Runtime API → Meeting API: callback_url confirmed

vexa-bot: joins meeting → captures audio per speaker
vexa-bot → Transcription Service: HTTP audio → text
vexa-bot → Redis streams: publish segments
Transcription Collector → Redis: consume streams → PostgreSQL

vexa-bot: exits meeting
Runtime API: detects exit → POST callback_url
Meeting API: receives callback → post-meeting hooks
Meeting API → webhook: notify user
```

### Agent Chat

```
User → Gateway → Agent API: POST /chat {message}
                 Agent API → Runtime API: POST /containers {profile: "agent"}
                              (if not already running)
                 Agent API → docker exec: run Claude CLI
                 Claude: processes with workspace context
                 Agent API → User: SSE stream response
```

### Scheduled Join

```
Calendar Service: syncs Google Calendar → finds upcoming meeting
Calendar Service → Agent API: POST /schedule {execute_at, request: POST /meetings}
... time passes ...
Scheduler worker fires → Gateway → Meeting API: POST /meetings
Meeting API → Runtime API → vexa-bot (same as above)
```

### Voice Agent

```
User → Gateway → Meeting API: POST /meetings/{id}/speak {text}
                 Meeting API → TTS Service: text → audio
                 Meeting API → Redis pub/sub: send audio to bot
                 vexa-bot: plays audio via virtual microphone
```

---

## Directory Structure (Target)

```
services/
├── api-gateway/              # Entry point, routing, CORS
├── admin-api/                # Users, tokens, analytics
├── meeting-api/              # NEW — meetings, recordings, webhooks, voice agent
├── agent-api/                # Chat, workspaces, scheduling
├── runtime-api/              # EXPANDED — generic CaaS (absorbed bot-manager orchestration)
├── transcription-service/    # Whisper API
├── transcription-collector/  # Redis → DB pipeline
├── tts-service/              # Text-to-speech
├── mcp/                      # MCP server
├── calendar-service/         # Google Calendar sync
├── telegram-bot/             # Telegram interface
├── dashboard/                # Next.js web UI
├── vexa-bot/                 # Meeting bot container image
├── vexa-agent/               # Agent container image (moved from containers/agent/)
└── transcript-rendering/     # TypeScript library

DELETED:
├── bot-manager/              # DELETED — split into meeting-api + runtime-api
└── containers/               # Moved to services/vexa-agent/
```

### Shared Libraries

```
libs/
└── shared-models/
    ├── models.py              # SQLAlchemy: User, Meeting, Recording, etc.
    ├── schemas.py             # Pydantic: MeetingCreate, Platform, MeetingStatus
    ├── token_scope.py         # Token validation (vxa_user_*, vxa_bot_*, etc.)
    ├── scheduler.py           # Redis sorted set scheduler
    ├── scheduler_worker.py    # In-process executor loop
    ├── security_headers.py    # Shared middleware
    ├── webhook_delivery.py    # Webhook HMAC signing + delivery
    ├── webhook_url.py         # Webhook URL model
    └── webhook_retry_worker.py
```

---

## Auth Model

```
┌─────────────────────────────────────────────────────┐
│                    Token Scopes                      │
├──────────────┬──────────────────────────────────────┤
│ vxa_user_*   │ User API tokens → Gateway → all APIs │
│ vxa_bot_*    │ Bot containers → Meeting API callbacks│
│ vxa_tx_*     │ Bot → Transcription Collector         │
│ vxa_admin_*  │ Admin operations                      │
├──────────────┴──────────────────────────────────────┤
│ Meeting JWT  │ Minted by Meeting API, used by bot   │
│              │ for callback auth (HS256, 1hr TTL)   │
├──────────────┴──────────────────────────────────────┤
│ Internal     │ Runtime API accepts only service      │
│              │ tokens — not exposed via Gateway      │
└─────────────────────────────────────────────────────┘
```

---

## Migration Strategy

### Approach: Strangler Fig + Branch by Abstraction

No big bang rewrite. Extract piece by piece while production keeps running.

### Phase 1: Unify Backends in Runtime API

**Goal:** Runtime API gains K8s and process backends. Can spawn meeting bots.

1. Port `orchestrators/kubernetes.py` and `orchestrators/process.py` into runtime-api
2. Enrich meeting profile with full BOT_CONFIG expansion (env vars, platform config, GPU)
3. Add lifecycle callbacks (POST to callback_url on exit/fail)
4. Add per-user concurrency limits
5. Add `/wait` endpoint (long-poll for state)

**Validation:** `POST /containers {profile: "meeting", config: {...}}` spawns a working meeting bot.

**Safety:** Feature toggle. Both paths run, compare results.

### Phase 2: Create Meeting API

**Goal:** New service owns all meeting domain logic. Same endpoint paths.

1. Create `services/meeting-api/`
2. Move endpoints from bot-manager to meeting-api: `/bots/*`, recordings, webhooks, voice agent
3. Meeting API calls Runtime API for container operations
4. API Gateway: proxy `/bots/*` target from bot-manager:8080 → meeting-api:8080
5. External clients see zero change

**Validation:** Full meeting lifecycle works through Meeting API → Runtime API. Same curl commands, same responses.

### Phase 3: Delete Bot Manager

**Goal:** Bot-manager is empty and removed.

1. Verify all traffic routes through Meeting API
2. Remove bot-manager service, Dockerfile, deploy configs
3. Move `containers/agent/` to `services/vexa-agent/`
4. Update docker-compose, Helm charts, docs

**Validation:** `docker-compose up` works without bot-manager.

### Phase 4: Clean Up

1. Remove stale references in docs, configs, tests
2. Update `services/README.md` architecture diagram
3. Move `containers/agent/` to `services/vexa-agent/`
4. Run full integration tests
5. No API route changes needed — paths were preserved throughout

---

## Build vs Buy Decision

### Why Build

1. **Domain logic is the hard part** — lifecycle callbacks, activity-based idle, CDP tracking, per-user concurrency, platform-specific metadata. No external tool handles these.
2. **Runtime API is already 80% there** — ~500 lines, clean profile system, Redis state. Needs K8s backend and richer profiles, not replacement.
3. **Recall.ai validates** — Market leader built fully custom at 8M instances/month.
4. **Small codebase** — This isn't distributed systems complexity. It's a well-defined container manager.

### Adopt Later (If Complexity Grows)

| Tool | When | For What |
|------|------|----------|
| Temporal | Meeting lifecycle becomes complex (retries, sagas) | Durable workflow above Runtime API |
| Argo Workflows | Post-meeting pipeline grows | DAG-based processing chain |
| K8s Operator (CRD) | K8s becomes primary backend | Declarative state, kubectl visibility |
| Pre-warm pool | Startup latency critical (<5s) | Warm container inventory |

---

## Industry References

- **Recall.ai** — Direct competitor. 8M EC2/month, fully custom orchestration. Single `POST /bots` abstracts all platforms. Validates build approach.
- **Fly Machines API** — Closest API pattern. `POST /machines` with config, explicit lifecycle, lease-based concurrency. Inspiration for Runtime API contract.
- **Selenium Grid 4** — Architectural parallel. Router → Distributor → Node = Gateway → Runtime API → backends. Capability-based selection = profile system.
- **E2B** — Template system (Dockerfile → snapshot → fast start) mirrors profile concept.
- **Stripe API versioning** — Expand-and-contract for schema migration. Date-based versions overkill for internal refactoring.

---

## Risk Assessment

Three independent devil's advocate reviews (infrastructure, code, product) identified 13 risks. 5 are blockers that must be resolved before starting.

### Blockers

**1. Hardcoded callback URLs in bot containers**
Bot containers had `http://bot-manager:8080/bots/internal/callback/exited` baked into BOT_CONFIG at spawn time. After refactoring, bot-manager is gone — meeting-api handles callbacks.
**Fix:** Runtime API accepts `callback_url` as a parameter at container creation. Meeting API passes its own URL. No hardcoded service names in container config.

**2. Dashboard depends on exact `/bots/status` response shape**
Dashboard merges `/bots/status` (running_bots with container_name, meeting_id_from_name, etc.) with `/meetings`. The admin page also uses it. Auth verification calls it.
**Fix:** Meeting API must return identical response shape. Write contract tests before Phase 2.

**3. Calendar service** (resolved)
`services/calendar-service/app/sync.py` calls `POST {MEETING_API_URL}/bots` directly. Default hostname is `meeting-api`.
**Fix:** Route calendar-service through API gateway, or update env var. Add startup health check.

**4. Redis channel names are frozen contracts**
Three publishers use specific channel prefixes that gateway WebSocket handler subscribes to:
- `bm:meeting:{id}:status` (meeting-api → `bm:` prefix is frozen)
- `tc:meeting:{id}:mutable` (transcription-collector)
- `va:meeting:{id}:chat` (vexa-bot)
Agent API also subscribes to `bm:meeting:*:status` for agent wake-up.
**Fix:** Document as frozen. Meeting API publishes to same `bm:` prefix with same payload shapes.

**5. Phase 2 in-flight meetings span the cutover**
Bots previously called back to `http://bot-manager:8080`. After cutover, meeting-api handles all callbacks directly (resolved — same DB, same callback paths).

### High Priority (design before Phase 1)

**6. Callback delivery has no guarantees**
Runtime API POSTs to callback_url on container exit. If Meeting API is down, callback is lost forever. No retry, no dead-letter queue.
**Fix:** Use Docker event stream listener (`docker events`) instead of polling. Retry with exponential backoff (1s/5s/30s). Store pending callbacks in Redis with TTL.

**7. Docker socket race: idle-stop during exec**
Runtime API's idle loop stops containers. Agent API does `docker exec` directly. If stop fires mid-exec, user sees broken SSE stream.
**Fix:** Agent API must call `POST /containers/{name}/touch` before every exec. Runtime API checks Docker state before stopping.

**8. Process backend registry is in-memory only**
If Runtime API crashes, all process tracking is lost. Orphaned Node.js bots run forever. Post-meeting hooks never fire.
**Fix:** Redis-backed process registry. Periodic reaper loop. Resource limits via `setrlimit()`.

**9. `bot_exit_callback` bridges both domains in one function**
The callback reads Redis chat messages (orchestration), updates meeting status (domain), and triggers post-meeting tasks — all in one DB transaction.
**Fix:** Redesign: Runtime API fires generic callback `{container_id, status, exit_code, metadata}`. Meeting API receives it and handles all domain logic (status update, chat persistence, post-meeting hooks, webhooks).

### Medium Priority

**10. Redis/Postgres split-brain**
Container exits but callback fails → Redis says "stopped", Postgres says "active". Zombie meetings.
**Fix:** Reconciliation loop in Meeting API: query Runtime API for active containers, mark missing ones as failed.

**11. Webhook delivery timing shifts**
Adding a network hop delays webhooks by callback latency. If callback fails, webhooks never fire.
**Fix:** Callback retry queue (see #6). Reconciliation (see #10).

**12. Voice agent latency +5-20ms**
Additional Meeting API hop in speak/chat/screen commands.
**Fix:** Benchmark. Likely acceptable. If not, colocate Meeting API with bot containers.

**13. K8s profile abstraction bloat**
Meeting profile needs `/dev/shm`, GPU, node selectors, tolerations. Generic profiles can't hold K8s-specific fields.
**Fix:** Opaque `k8s_overrides` dict in profile system. Don't try to abstract away K8s specifics.

### Pre-Phase-1 Checklist

Before starting any refactoring:

- [ ] Design callback delivery guarantee (event stream vs polling, retry queue, at-least-once semantics)
- [ ] Make process registry durable (Redis-backed)
- [ ] Define Phase 2 drain strategy (drain meetings or backward-compat callbacks)
- [ ] Write contract tests for `/bots/status` response shape
- [ ] Document frozen contracts: API paths, response shapes, Redis channel names, webhook HMAC format
- [ ] Resolve calendar-service routing (gateway vs env var update)
- [ ] Design generic callback contract: `{container_id, status, exit_code, metadata}`

---

## Build vs Buy: Deep Validation

### Research Scope
30+ projects evaluated across 5 categories (sandboxing, browser farms, dev environments, task runners, container APIs). Two separate investigations: platform-level CaaS alternatives and SDK/library unification layers.

### Verdict: Build

**No unified Docker+K8s container CRUD API exists as open source.** Everyone who needs this builds their own. Our ~764-line Runtime API is the correct approach.

| Category | Projects Reviewed | Best Match | Why Not |
|----------|-------------------|------------|---------|
| Sandboxing | E2B, Piston, Judge0, OpenSandbox, Microsandbox, Daytona | OpenSandbox (Alibaba) | 3 weeks old, injects Go daemon, no callbacks |
| Browser farms | Selenium Grid, Browserless, Selenoid, Moon, Steel | Selenium Grid 4 | K8s only for dynamic, no Docker backend |
| Dev environments | Coder, DevPod, Gitpod, Coolify, CapRover | Coder | AGPL license, massive overkill |
| K8s-native | agent-sandbox (k8s-sigs) | agent-sandbox | K8s only, no Docker for local dev |
| Libraries | Libcloud, kr8s, Dagger, Pulumi, Testcontainers | None | No library unifies Docker+K8s container CRUD |

### Patterns to Adopt

| Pattern | Source | Apply To |
|---------|--------|----------|
| `callback_url` on creation | Judge0 | Runtime API: caller passes URL, we POST on exit/fail |
| Container groups | Sablier | Start agent + browser atomically |
| 5 timeout taxonomy | Selenoid | idle, max, startup, creation-attempt, deletion timeouts |
| Hot-reloadable profiles | Selenoid | `profiles.json` with SIGHUP reload |
| `POST /renew` (extend TTL) | OpenSandbox | Complements our `/touch` endpoint |
| SandboxTemplate CRD | agent-sandbox | Future K8s operator migration |
| Lifecycle hooks | Nomad | prestart/poststart/poststop per profile |

### SDK Recommendation

Replace `kubernetes` Python client with **kr8s** (BSD-3, 955 stars, monthly releases):
- Native async (eliminates `run_in_executor`)
- Dict-based pod specs (no V1Pod/V1Container boilerplate)
- Estimated K8s backend reduction: 426 → ~250 lines

Keep `docker` Python SDK as-is (stable, well-documented).
