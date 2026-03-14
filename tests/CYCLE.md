# Test Cycle: Bottom-Up Sequential Validation

The most thorough, human-gated test approach. Not the only way — `make test-unit` and `make test-all` exist for quick runs. Use the cycle for pre-release, after major changes, or when you need confidence.

**How it works:**
1. Agent presents the step — WHY, WHAT, HOW
2. Agent runs it
3. Agent presents results AND tells human what to look at — gaps, risks, findings
4. Human validates — approves, asks questions, or says fix it
5. Next step

## Principles

1. **Isolate, then chain.** Test each service completely alone first (unit → start → verify → stress → quality). Only then test it connected to its dependencies. If the chain fails, you already know the individual pieces work — the problem is the connection.

2. **Bottom-up by dependency.** Start at the foundation, work up. Never test something whose foundation is unverified.

3. **Every service has THREE gates:**
   - **Unit tests pass** — code logic is sound
   - **README exists with WHY/WHAT/HOW** — documented for humans
   - **Hot test passes** — the service actually runs and does its job

   If any gate fails, the step fails. Fix it before moving on.

4. **Chain tests follow isolation tests.** After service A passes and service B passes, test A→B as a chain. The chain test verifies the connection, not the individual services.

5. **Test objectives per service:** throughput, quality, dev experience, clean code, robustness/stability, security, compatibility, recovery, configuration validation, observability.

6. **Results go to TWO places:** the service's `tests/results/` (local) AND repo's `tests/*/results/` (central). Baselines in `tests/load/results/README.md` must be updated.

7. **Findings feed back into docs.** Every finding — security gaps, code issues, missing docs — gets documented. If it changes how we test, update CYCLE.md. If it changes a service, update its README.

8. **Code without docs is untested code.** Missing README blocks the gate.

9. **Docs must be validated.** Documented commands that don't work are bugs. Follow the README — if a step fails, it's a finding.

## Service dependency graph

```
shared-models              ← library, no deps
     ↓
transcription-service      ← standalone, GPU inference behind API
     ↓
WhisperLive                ← WebSocket server, depends on transcription-service + Redis
     ↓
Bot (per-speaker pipeline) ← depends on transcription-service + Redis (NOT WhisperLive)
     ↓
admin-api                  ← depends on Postgres + shared-models
transcription-collector    ← depends on Redis + Postgres
     ↓
bot-manager                ← depends on Redis + Postgres + admin-api
     ↓
api-gateway                ← depends on admin-api + bot-manager + Redis
     ↓
dashboard                  ← depends on api-gateway + admin-api
```

## Phase 1: Standalone services (no Docker)

Test code logic in isolation. Bottom of dependency graph first.

### 1.1 shared-models
**WHY:** Foundation — every service imports it.
**Gates:** 39 unit tests + library (no README gate).
```bash
cd libs/shared-models && python -m pytest shared_models/test_*.py -v
```

### 1.2 transcription-service

**1.2a Unit tests**
**WHY:** Transcription logic — hallucination detection, silence filtering, config parsing.
**Gates:** 30 unit tests + README with WHY/WHAT/HOW.
```bash
cd services/transcription-service && pytest tests/ -v
```

**1.2b Hot isolation test**
**WHY:** Unit tests prove logic. Now prove it actually transcribes.
```bash
bash tests/test_hot.sh --full    # start → verify → basic load → stop
```
**Look at:** Transcript quality (coherent, not garbage), latency vs baseline, README port matches actual.

**1.2c Stress test**
**WHY:** Find real capacity limits — concurrency curve, audio sizes, GPU utilization.
```bash
bash tests/test_stress.sh
```
**Look at:** At what concurrency does quality degrade? Per-worker VRAM (not total machine). Memory growth.
**Baselines:** Single: ~0.17s GPU. 40 concurrent (2 workers): 100% success. Queue limit: ~20/worker.

**1.2d Agent-driven deep test**
**WHY:** Security, compatibility, recovery, observability — things scripts can't judge.
```bash
# Agent reads tests/AGENT_TEST.md and executes all 6 tests
```
**Look at:** Auth enforcement, OpenAI API compatibility, worker crash recovery, log quality.

### 1.3 WhisperLive

**1.3a Unit tests**
**WHY:** Dataclass contracts — if Segment serializes wrong, downstream breaks silently.
**Gates:** 10 unit tests + README with WHY/WHAT/HOW.
```bash
cd services/WhisperLive && pytest tests/ -v
```

**1.3b Hot chain test**
**WHY:** WhisperLive can't be tested alone — needs transcription-service. Test the chain.
**Prerequisite:** transcription-service running (already tested in 1.2).
```bash
# Start transcription-service first
cd services/transcription-service && docker compose up -d

# Start WhisperLive test compose (separate ports, separate network)
cd services/WhisperLive && bash tests/test_hot.sh --full
```
**Look at:** Transcript quality through the chain. Audio format (Float32Array, not Int16). LIFO behavior with short audio.

**1.3c Stress test**
**WHY:** Find concurrent stream limits. Understand LIFO behavior under load.
```bash
bash tests/test_stress.sh
```
**Baselines:** 100 concurrent: 100% segment delivery. 200: 96.5%. 500: 58.4% (LIFO skipping by design). ~4MiB memory per connection.
**Look at:** This is NOT silent failure — it's LIFO prioritizing freshness. Streams that "miss" segments would get them eventually, just slower.

### 1.4 Bot (per-speaker pipeline)

**1.4a Unit tests**
**WHY:** Per-speaker buffer management, transcription client, segment publisher.
**Gates:** Unit tests + README with WHY/WHAT/HOW.
```bash
cd services/vexa-bot/core && npx jest    # or vitest
```

**1.4b Mock meeting test**
**WHY:** Test the full per-speaker pipeline without a real meeting.
**Prerequisite:** transcription-service + Redis running.
```bash
# Start backend
docker compose up -d redis
cd services/transcription-service && docker compose up -d

# Serve mock meeting (3 speakers: Alice, Bob, Carol — Edge TTS audio)
cd services/vexa-bot/tests/mock-meeting && bash serve.sh

# Run bot against mock meeting
cd services/vexa-bot/core
MEETING_URL=http://localhost:8080 npm run dev

# Verify
redis-cli XRANGE transcription_segments - +    # segments with speaker labels
redis-cli XRANGE speaker_events - +            # speaker lifecycle events
```
**Look at:**
- 3 separate streams discovered (not mixed)
- Each speaker's segments have correct speaker label
- No cross-contamination (Alice's words don't appear in Bob's segments)
- Speaker events have correct start/end times

### 1.5 admin-api
**WHY:** User/token store. JSONB merge bug guard.
**Gates:** 43 unit tests + README.
```bash
cd services/admin-api && pytest tests/ -v
```

### 1.6 transcription-collector
**WHY:** Segment persistence. Filter quality.
**Gates:** 46 unit tests + README.
```bash
cd services/transcription-collector && pytest tests/ -v
```

### 1.7 api-gateway
**WHY:** External API entry point. Route and auth enforcement.
**Gates:** 35 unit tests + README.
```bash
cd services/api-gateway && pytest tests/ -v
```

### 1.8 Remaining services
**tts-service:** 16 unit tests + README.
**mcp:** 53 unit tests + README.
**dashboard:** README check (no unit tests — UI service).

### Phase 1 Gate
**All unit tests pass. All READMEs have WHY/WHAT/HOW. Standalone services hot-tested.**

---

## Phase 2: Infrastructure + services (Docker)

Start services in dependency order. Each must be healthy before its dependents start.

### 2.1 Infrastructure
```bash
docker compose up -d postgres redis
```
**Verify:** `pg_isready`, `redis-cli ping`.

### 2.2 admin-api
```bash
docker compose up -d admin-api
```
**Verify:** health check, CRUD operations, auth enforcement.

### 2.3 transcription-collector
```bash
docker compose up -d transcription-collector
```
**Verify:** consuming Redis stream, persisting to Postgres.

### 2.4 bot-manager
```bash
docker compose up -d bot-manager
```
**Verify:** health check, bot launch capability.

### 2.5 api-gateway
```bash
docker compose up -d api-gateway
```
**Verify:** routing, auth enforcement, WebSocket proxy.

### 2.6 dashboard
```bash
docker compose up -d dashboard
```
**Verify:** pages load, no console errors.

### Phase 2 Gate
**All services healthy. Basic operations verified.**

---

## Phase 3: Chains (services talk to each other)

### 3.1 Transcription chain (THE HOT PATH)
```
audio → bot (per-speaker) → transcription-service → segments → Redis → collector → Postgres → API
```
**Test with mock meeting.** Verify transcript with speaker labels in API response.

### 3.2 Bot chain
```
launch bot → bot joins mock meeting → per-speaker transcription → segments appear
```
**Verify:** resource usage, speaker events, recording upload.

### 3.3 API chain
```
authenticate → CRUD → launch bot → poll transcript → get result with speakers
```

### Phase 3 Gate
**All chains work end-to-end.**

---

## Phase 4: Stress (one service at a time)

### 4.1 transcription-service
```bash
cd services/transcription-service && bash tests/test_stress.sh
```

### 4.2 WhisperLive (if using shared mode)
```bash
cd services/WhisperLive && bash tests/test_stress.sh
```

### 4.3 Bot scaling
Multiple concurrent bots against mock meetings. Resource usage per bot.

### Phase 4 Gate
**No regression >20% from baselines.**

---

## Phase 5: Audit (code, not runtime)

```bash
make audit    # security + config + architecture + staleness
```

Plus agent-driven:
- Docs validation (follow every README)
- Security deep review
- Dev experience assessment

### Phase 5 Gate
**0 errors. Warnings reviewed by human.**

---

## Phase 6: Report → Human

```
Phase 1: X/Y services pass (N total tests)
Phase 2: X/Y services healthy
Phase 3: X/Y chains work
Phase 4: baselines held / regressed
Phase 5: N new findings, M resolved
```

**Human decides: commit, fix more, or investigate.**

---

## Key learnings from previous cycles

1. **Audio format matters.** Bot sends Float32Array, not Int16 PCM. Sending wrong format = hallucinated garbage, not an error. Silent corruption.
2. **Test compose needs exact config.** DNS (`extra_hosts`), API keys matching between services, correct env vars. Budget 3-4 iterations for new test compose files.
3. **WhisperLive's LIFO is not a bug.** Under load, it intentionally skips old audio to stay current. "Missing segments" at high concurrency = LIFO working as designed, not silent failure.
4. **Per-worker queue limit (~20) is the transcription-service bottleneck.** Not GPU, not memory. Software limit.
5. **GPU memory reported by nvidia-smi is machine-wide.** Filter to per-process: `nvidia-smi --query-compute-apps=pid,used_memory --format=csv`.
6. **Missing README = failed gate.** Not optional. 4 services were missing READMEs and should have blocked Phase 1.
7. **DOM is still needed for speaker names.** Audio tracks give you separation, DOM gives you labels. One-time lookup per participant, not continuous polling.
8. **Edge TTS generates good test audio.** Three distinct neural voices (Jenny, Guy, Sonia), different content, transcribes perfectly. Use for mock meeting dev environment.
