# Test Cycle: Bottom-Up Sequential Validation

This is the execution plan for a full test cycle. Follow phases in order. Each phase gates the next — if a foundation fails, don't test the roof.

The cycle is the most thorough, human-gated test approach. Not the only way to run tests — `make test-unit` and `make test-all` exist for quick/automated runs. Use the cycle for pre-release, after major changes, or when you need confidence.

**How it works:**
1. Agent presents the step — WHY we're testing this, WHAT we're checking, HOW we'll run it
2. Agent runs it
3. Agent presents results AND tells the human what to look at — not just "43 passed" but "these endpoints have no auth coverage, this README is missing a HOW section, this config defaults to DEBUG"
4. Human validates — approves, asks questions, or says fix it
5. Next step

The agent's job is to interpret, not just report. Surface what matters. Point to the gaps. The human decides.

## Principles

1. **Isolate, then chain.** Test each service completely alone first (unit → start → verify → stress → quality). Only then test it connected to its dependencies. If the chain fails, you already know the individual pieces work — the problem is the connection.

2. **Bottom-up by dependency.** Start at the foundation (shared-models), work up. Each service is tested after its dependencies are verified. Never test something whose foundation is unverified.

3. **Every service has THREE gates:**
   - **Unit tests pass** — code logic is sound
   - **README exists with WHY/WHAT/HOW** — documented for humans
   - **Hot test passes** — the service actually runs and does its job (where applicable — libraries skip this)

   If any gate fails, the step fails. Fix it before moving on.

4. **Chain tests follow isolation tests.** After service A (standalone) passes, and service B (standalone) passes, test A→B as a chain. The chain test verifies the connection, not the individual services.

5. **Test objectives per service:** throughput, quality, developer experience, clean code, robustness/stability, security, compatibility, recovery, configuration validation, observability. Not every service needs all 10 — but the agent should consider each.

6. **Results go to TWO places:** the service's own `tests/results/` (local context) AND the repo's central `tests/*/results/` (cross-service comparison, baseline tracking). Central baselines in `tests/load/results/README.md` must be updated after each test run.

7. **Findings feed back into docs.** Every test finding — security gaps, code issues, missing docs — gets documented. If a finding changes how we test, update CYCLE.md. If it changes how a service works, update the service README. If it changes a principle, update testing-and-audit.md.

## Service dependency graph (test order)

```
shared-models              ← no deps, test first
     ↓
admin-api                  ← needs shared-models + postgres
transcription-service      ← no deps, standalone
     ↓
WhisperLive                ← needs transcription-service + redis
transcription-collector    ← needs redis + postgres
     ↓
bot-manager                ← needs redis + postgres + WhisperLive
tts-service                ← needs external API
mcp                        ← needs api-gateway
     ↓
api-gateway                ← needs admin-api + bot-manager
     ↓
dashboard                  ← needs api-gateway + admin-api
```

---

## Phase 1: Foundations (no Docker, no services)

Test each service's code in isolation. Bottom of dependency graph first. Present WHY/WHAT/HOW to human before each step.

### 1.1 shared-models

**WHY:** Foundation for all services — token scoping, webhook retry, delivery history, DB models. Every service imports from it. If broken, nothing works. Zero dependencies, maximum downstream impact.

**WHAT:** 39 tests:
- Token prefix generation and parsing (`vxa_bot_`, `vxa_tx_`, `vxa_user_`) — auth fails everywhere if broken
- Webhook retry queue — backoff timing, expiry, permanent vs transient failure handling
- Webhook delivery history — metadata tracking, meeting status updates

**HOW:**
```bash
cd libs/shared-models && python -m pytest shared_models/test_*.py -v --tb=short
```

**FAIL? STOP.** Everything depends on shared-models.

---

### 1.2a transcription-service (unit tests)

**WHY:** Standalone transcription engine. No deps on other Vexa services. Converts audio to text. If transcription logic is wrong (hallucination detection, silence filtering), transcripts are garbage.

**WHAT:** 30 tests — config parsing, hallucination heuristics, silence detection, tier normalization, capacity admission.

**HOW:**
```bash
cd services/transcription-service && pytest tests/ -v --tb=short
```

**FAIL?** Fix before proceeding. WhisperLive depends on this.

---

### 1.2b transcription-service (start and verify)

**WHY:** Unit tests prove the logic is sound. Now prove the service actually runs, accepts audio, and returns transcripts. This is standalone — no other Vexa service needed. Test it hot before touching anything else.

**WHAT:** Start the service, send test audio, verify transcript quality.

**HOW:**
```bash
# Start (its own docker-compose)
cd services/transcription-service && docker compose up -d
curl -s http://localhost:8080/health
```

Send test audio:
```bash
curl -X POST http://localhost:8080/v1/audio/transcriptions \
  -F "file=@tests/test_audio.wav" \
  -F "model=large-v3-turbo"
```
- **Expect:** transcript text returned, coherent, not garbage
- Agent: evaluate transcription quality — not just "200 OK"

**What to look at:** Does the transcript make sense? Any hallucination artifacts? Response time?

---

### 1.2c transcription-service (load test)

**WHY:** Known bottleneck — catastrophic degradation at 10 concurrent (15% coverage, March 2026 test). Need current limits before building anything on top of it.

**WHAT:** Single request baseline, concurrent throughput curve, memory leak check.

**HOW:**
```bash
# Single request — what's the baseline latency?
python tests/load/transcription_service.py --mode single

# Concurrent — where does it break?
python tests/load/transcription_service.py --mode concurrent --vus 5
python tests/load/transcription_service.py --mode concurrent --vus 10

# Memory — does it leak under sustained load?
python tests/load/transcription_service.py --mode memory --vus 2 --duration 300
```

**Known baselines:**
- Single client: ~14.4s avg latency
- 10 concurrent: 15% coverage (catastrophic degradation)
- Bot resource: 250m CPU, 597Mi RAM per bot

**What to look at:** Current single latency vs baseline. At what concurrency does quality degrade? Does memory grow linearly or plateau? Save results to `tests/load/results/`.

**Stop after:**
```bash
cd services/transcription-service && docker compose down
```

**FAIL?** Fix or document limits before testing WhisperLive chain.

---

### 1.3 admin-api

**WHY:** User and token management for all services. The critical risk: JSONB merge bug — PATCH was replacing entire `data` objects instead of merging. This test permanently guards against that production bug.

**WHAT:** 43 tests — JSONB merge, auth enforcement, all 14 route definitions, CRUD with mocked DB.

**HOW:**
```bash
cd services/admin-api && pytest tests/ -v --tb=short
```

**FAIL?** Fix before testing api-gateway (depends on admin-api).

---

### 1.4 WhisperLive

**WHY:** Real-time audio bridge. Receives WebSocket audio from bots, forwards to transcription-service, streams segments to Redis. If its data types are malformed, segments are garbage and downstream breaks silently — no error, just wrong data.

**WHAT:** 10 tests:
- VadOptions, Segment, TranscriptionOptions dataclass validation
- Field types, defaults, serialization — these are the contracts between WhisperLive and everything downstream

**HOW:**
```bash
cd services/WhisperLive && pytest tests/ -v --tb=short
```

**FAIL?** Fix before testing bot chain (bots connect to WhisperLive).

---

### 1.5 bot-manager

**WHY:** Orchestrates meeting bots — launches containers (Docker/K8s), manages lifecycle, handles recordings. The critical risk: two concurrent requests launching the same bot = duplicate containers, resource leak, billing chaos. We fixed this with a `FOR UPDATE` row lock and the test verifies the lock exists in the SQL.

**WHAT:** 8 tests:
- Concurrent launch guard — verifies `FOR UPDATE` lock in the query (the race condition fix)
- Zoom OBF token handling — Zoom-specific obfuscation logic

**HOW:**
```bash
cd services/bot-manager && pytest tests/ -v --tb=short
```

**FAIL?** Fix before testing bot chains.

---

### 1.6 transcription-collector

**WHY:** Consumes transcription segments from Redis streams, persists to Postgres, serves via REST API. If its filters are wrong, duplicate or garbage segments make it to the database. Users see junk transcripts.

**WHAT:** 46 tests:
- Filtering logic — minimum length, real word counting, stopword detection
- Time-based deduplication — prevents duplicate segments within a time window
- Cache management — filter state cleanup (prevents memory leaks)
- Config defaults — Redis connection, speaker event settings

**HOW:**
```bash
cd services/transcription-collector && pytest tests/ -v --tb=short
```

**FAIL?** Transcripts won't persist correctly.

---

### 1.7 api-gateway

**WHY:** Single entry point for all external API traffic. Routes requests to admin-api, bot-manager, transcription-collector. Enforces auth via token scope prefix. If a route is misconfigured, the API returns 502. If auth is bypassed, the API is exposed.

**WHAT:** 35 tests:
- Route existence — every documented API endpoint has a corresponding route defined
- Timestamp formatting — consistent date formatting in API responses
- CORS config parsing — converts env var string to allowed origins list

**HOW:**
```bash
cd services/api-gateway && pytest tests/ -v --tb=short
```

**FAIL?** External API access is broken.

---

### 1.8 tts-service

**WHY:** Text-to-speech proxy for interactive bots. Proxies to OpenAI TTS API. If input validation is wrong, bad requests hit OpenAI and cost money for nothing. If auth is missing, anyone can use it.

**WHAT:** 16 tests:
- Voice name validation, audio format validation, speed range checks
- Auth enforcement — API key required, wrong key rejected
- Health endpoint responds

**HOW:**
```bash
cd services/tts-service && pytest tests/ -v --tb=short
```

---

### 1.9 mcp

**WHY:** MCP protocol service — makes Vexa API available as tools for AI assistants (Cursor, VS Code, etc.). The URL parser is critical: if it can't parse a Google Meet or Teams URL correctly, the AI assistant can't join a meeting.

**WHAT:** 53 tests:
- Google Meet URL parsing (various formats, with/without codes)
- Teams URL parsing (personal, enterprise, legacy URL formats)
- Zoom URL parsing (meeting IDs, passcodes)
- Meeting ID validation, URL construction

**HOW:**
```bash
cd services/mcp && pytest tests/ -v --tb=short
```

---

### 1.10 top-level integration tests (no services needed)

**WHY:** Cross-service contracts tested with mocked boundaries. Token scoping must work across service boundaries — a `vxa_bot_` token used on a user-only endpoint must be rejected. Webhook signatures must be verifiable by the receiver. These tests verify the contracts without starting Docker.

**WHAT:** 46 tests:
- Token prefix scoping across services (T2-T6 test matrix)
- Webhook signing and delivery header validation

**HOW:**
```bash
cd /home/dima/dev/vexa && python -m pytest tests/test_token_scoping_integration.py tests/test_webhook_delivery.py -v --tb=short
```

---

### Phase 1 Gate

**Expected: 287+ tests, 0 failures, 0 skips.**

All READMEs reviewed: each has WHY (why this service exists), WHAT (what it does, inputs/outputs), HOW (how to run, configure, test).

---

## Phase 2: Services come up (Docker, one at a time)

Start services in dependency order. Each must be healthy before its dependents start. Agent verifies each service does its job, not just that it responds to health checks.

### 2.1 Infrastructure

**WHY:** Postgres and Redis are the stateful foundation. Every service depends on at least one of them. If they're not healthy, starting any service is pointless.

**WHAT:** Start postgres and redis, verify they accept connections.

**HOW:**
```bash
docker compose up -d postgres redis
docker exec vexa_dev-postgres-1 pg_isready -U postgres
docker exec vexa_dev-redis-1 redis-cli ping
```

**FAIL? STOP.** Nothing else works.

---

### 2.2 admin-api

**WHY:** User/token store. Gateway and dashboard both call it. If admin-api is down, no user can authenticate, no tokens can be created.

**WHAT:** Start admin-api, verify it responds and can perform CRUD operations.

**HOW:**
```bash
docker compose up -d admin-api
curl -s http://localhost:8057/openapi.json | head -1
curl -s -H "X-Admin-API-Key: $ADMIN_API_TOKEN" http://localhost:8057/admin/users?limit=1
```
Agent: create a user, read it back, update it, verify merge works.

**FAIL? STOP.** Gateway depends on this.

---

### 2.3 transcription-service

**WHY:** The transcription engine. Without it, no audio gets transcribed. WhisperLive forwards audio here.

**WHAT:** Start transcription-service, send a test audio file, get a transcript back.

**HOW:**
```bash
docker compose up -d transcription-service
curl -s http://localhost:8080/health
```
Agent: POST a test audio file to `/v1/audio/transcriptions`, verify transcript text is returned.

**FAIL? STOP.** WhisperLive depends on this.

---

### 2.4 WhisperLive

**WHY:** Real-time WebSocket bridge between bots and transcription. If it doesn't start or can't connect to transcription-service, live transcription is dead.

**WHAT:** Start WhisperLive, verify it accepts WebSocket connections and produces segments.

**HOW:**
```bash
docker compose up -d whisperlive
```
Agent: WebSocket connect, send audio frames, verify segments arrive in Redis.

**FAIL? STOP.** Bots depend on this.

---

### 2.5 transcription-collector

**WHY:** Persists transcripts from Redis to Postgres. Without it, transcripts exist only in Redis (ephemeral) and are lost.

**WHAT:** Start collector, verify it's consuming from Redis streams.

**HOW:**
```bash
docker compose up -d transcription-collector
```
Agent: check logs for Redis stream consumption activity.

**FAIL?** Transcripts won't persist.

---

### 2.6 bot-manager

**WHY:** Launches and manages bots. Without it, no bots join meetings.

**WHAT:** Start bot-manager, verify it can respond to bot launch requests.

**HOW:**
```bash
docker compose up -d bot-manager
```
Agent: verify the service is up and can list bots (may return empty list — that's fine).

**FAIL?** Bots won't work.

---

### 2.7 api-gateway

**WHY:** External API entry point. Without it, nothing is accessible outside Docker network.

**WHAT:** Start gateway, verify routing to admin-api and bot-manager, verify auth enforcement.

**HOW:**
```bash
docker compose up -d api-gateway
curl -s http://localhost:8056/openapi.json | head -1
curl -s http://localhost:8056/meetings  # should reject (401/403)
```
Agent: verify valid token gets through, invalid token is rejected, routes reach backends.

**FAIL?** External API is dead.

---

### 2.8 dashboard

**WHY:** User-facing web UI. If it doesn't start, users can't manage meetings, view transcripts, or configure webhooks.

**WHAT:** Start dashboard, verify pages load.

**HOW:**
```bash
docker compose up -d dashboard
curl -s http://localhost:3001/api/health
```
Agent: verify main pages load (200), no console errors, login page renders.

---

### Phase 2 Gate

**All 8 services healthy. Basic operations verified per service.**

---

## Phase 3: Chains (services talk to each other)

### 3.1 Transcription chain (THE HOT PATH)

**WHY:** This is the product. Audio in, transcript out. If this chain breaks, nothing else matters.

**WHAT:** Send audio → WhisperLive → transcription-service → Redis → collector → Postgres → API returns transcript.

**HOW:** Agent sends real audio through the pipeline, queries the API for the transcript, verifies it's coherent text (not empty, not garbage).

**FAIL?** The product is broken. Stop everything.

---

### 3.2 Bot chain

**WHY:** Bots are how audio gets into the system. If a bot can't launch, connect to WhisperLive, and produce a transcript, the user gets nothing.

**WHAT:** Launch a bot → bot connects to WhisperLive → audio flows → transcript appears in API.

**HOW:** Agent launches a bot via bot-manager API, monitors its lifecycle (pending → running → stopped), checks resource usage against baselines (250m CPU, 597Mi RAM).

---

### 3.3 API chain

**WHY:** Full user flow without UI. This is what API customers experience.

**WHAT:** Authenticate → create user → generate token → launch bot → poll for transcript → get result.

**HOW:** Agent executes the full sequence via curl/API calls.

---

### Phase 3 Gate

**All chains work end-to-end.**

---

## Phase 4: Stress (one service at a time)

### 4.1 transcription-service under load

**WHY:** Known bottleneck — catastrophic degradation at 10 concurrent (15% coverage in March 2026 test). Need to know current limits so we can set appropriate concurrency caps.

**WHAT:** Single request baseline, concurrent throughput curve (5, 10 users), memory leak check under sustained load.

**HOW:**
```bash
python tests/load/transcription_service.py --mode single
python tests/load/transcription_service.py --mode concurrent --vus 5
python tests/load/transcription_service.py --mode concurrent --vus 10
python tests/load/transcription_service.py --mode memory --vus 2 --duration 300
```
Agent: compare results to baselines in `tests/load/results/README.md`.

---

### 4.2 WhisperLive under load

**WHY:** WebSocket connections are expensive. Need to know how many concurrent streams WhisperLive handles before segments start dropping.

**WHAT:** Concurrent WebSocket streams (1, 5, 10). Measure segment delivery rate, latency, connection stability.

**HOW:** Agent runs concurrent WebSocket clients, measures per-stream metrics.

---

### 4.3 Bot scaling

**WHY:** Each bot consumes significant resources (250m CPU, 597Mi RAM). Need to verify scaling behavior and cleanup.

**WHAT:** 1, 3, 5 concurrent bots. Measure resource per bot. Verify cleanup on exit (no orphaned containers).

**HOW:** Agent launches bots, monitors `docker stats`, verifies containers are removed after exit.

---

### Phase 4 Gate

**No regression > 20% from baselines.**

---

## Phase 5: Audit (code, not runtime)

### 5.1 Security audit

**WHY:** Catch hardcoded secrets, auth gaps, CORS issues before they ship.

**WHAT:** Scan all source files for known vulnerability patterns.

**HOW:**
```bash
python tests/audit/security_audit.py
```
Agent: compare findings to previous run. New findings = investigate. Resolved = good.

---

### 5.2 Architecture audit

**WHY:** Enforce design principles — stateless services, token scoping, durable delivery, self-hostable.

**WHAT:** Scan for principle violations.

**HOW:**
```bash
python tests/audit/architecture_audit.py
```

---

### 5.3 Staleness audit

**WHY:** Dead code and orphaned files are confusion and risk. They mislead developers and hide bugs.

**WHAT:** Find dead code, orphaned files, stale docs, unused dependencies.

**HOW:**
```bash
python tests/audit/staleness_audit.py
```

---

### 5.4 Docs validation

**WHY:** If the README says "run X" and X doesn't work, the project is hostile to contributors and self-hosters. Docs must match reality.

**WHAT:** Every README has WHY/WHAT/HOW. Documented commands actually work. No stale references.

**HOW:** Agent reads each README, runs documented commands, reports gaps.

---

### Phase 5 Gate

**0 errors. Warnings reviewed by human.**

---

## Phase 6: Report → Human

Agent presents per-phase summary:

```
Phase 1: X/Y services pass unit tests (N total tests)
Phase 2: X/Y services come up healthy
Phase 3: X/Y chains work end-to-end
Phase 4: baselines held / regressed (details)
Phase 5: N new findings, M resolved since last cycle
```

**Human decides: commit, fix more, or investigate.**

---

## Rules

1. Each phase gates the next. Phase 2 doesn't start until Phase 1 passes.
2. Within a phase, services tested in dependency order. If a dependency fails, dependents are skipped with reason.
3. Agent presents WHY/WHAT/HOW to human BEFORE each step. Human validates, then agent executes.
4. Agent proposes fixes. Human approves before any changes.
5. Nothing is committed without human approval.
6. All results saved to `tests/*/results/` with date stamps.
7. Each cycle's results compared to previous cycle.
8. Phases are strictly sequential. Parallelism within a phase is allowed only for independent services (no shared deps).
