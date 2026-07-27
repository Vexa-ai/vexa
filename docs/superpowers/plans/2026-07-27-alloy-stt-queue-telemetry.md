# ALLOY STT Queue Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Implementation subagents and worktrees are not authorized for this dirty local ALLOY checkout. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an owner-scoped global STT queue monitor that exposes real Whisper load, backlog, lag, RTF, and failures on every Vexa Terminal screen.

**Architecture:** The real gmeet scheduling boundary maintains per-meeting metrics and publishes a rate-limited current-state snapshot to Redis. Meeting API owner-scopes active meetings and aggregates their snapshots; gateway forwards the authenticated endpoint; one Terminal poller reads it every second while visible and renders a compact footer plus per-meeting details.

**Tech Stack:** TypeScript, Node.js, Redis, Python 3.11+, FastAPI, React 19, Next.js 15, Vitest, pytest, pnpm, Docker Lite deployment.

**Approved specification:** `docs/ALLOY-STT-TELEMETRY-DESIGN.md`

## Global Constraints

- Enable all new behavior only with `ALLOY_STT_TELEMETRY=1`.
- Prefix source comments that identify local customization with `ALLOY:` and diagnostics with `[ALLOY]`.
- Preserve upstream behavior when the flag is disabled.
- Do not change STT scheduling, chunk cadence, transcript text, speaker attribution, retry policy, or queue capacity as part of telemetry.
- Do not add packages, models, runtimes, system tools, or internet downloads.
- Do not expose Redis to the browser.
- Do not reuse the administrator-wide overview endpoint.
- Derive ownership only from gateway-injected `X-User-Id`; never trust meeting IDs supplied by the browser.
- Telemetry failure must not fail, restart, pause, or slow the transcription pipeline.
- Use one snapshot per active meeting at `alloy:stt:telemetry:{meeting_id}` with a 15-second TTL.
- Publish at most once per second during ordinary activity, plus immediate start, stop, error, and recovery snapshots.
- Poll once per second only while the Terminal document is visible.
- Preserve all pre-existing dirty files and edits. Patch overlapping files narrowly.
- Do not stage, commit, push, switch branches, or perform other Git mutations without a separate direct user instruction.
- Apply DRY and SOLID proportionately: one queue-metrics owner, one versioned snapshot interface, one Redis publisher, one owner-scoped aggregator, and one global poller. Do not introduce speculative abstractions or unrelated refactors.
- UI/unit tests may control external clock, HTTP, and STT completion seams, but must execute the real production tracker, scheduler, publisher, aggregator, polling store, and component code.
- A real Redis integration node and a real bot/Whisper live acceptance node are mandatory before any stability claim.

## File Map

| File | Responsibility |
| --- | --- |
| `core/meetings/modules/gmeet-pipeline/src/alloy-stt-telemetry.ts` | Versioned snapshot types and the single in-memory queue metrics owner |
| `core/meetings/modules/gmeet-pipeline/src/alloy-stt-telemetry.test.ts` | Deterministic tracker transition tests using production tracker code |
| `core/meetings/modules/gmeet-pipeline/src/gmeet-pipeline.ts` | Emits metrics at the real STT scheduling/backpressure boundary |
| `core/meetings/modules/gmeet-pipeline/src/alloy-channel-backpressure.test.ts` | Proves real scheduler metrics while actual requests are gated |
| `core/meetings/modules/gmeet-pipeline/src/index.ts` | Exports the telemetry contract used by bot composition |
| `core/meetings/services/bot/src/adapters/alloy-stt-redis.ts` | Rate-limited fault-isolated Redis snapshot publisher |
| `core/meetings/services/bot/src/adapters/alloy-stt-redis.live.test.ts` | Real Redis TTL, overwrite, immediate terminal-state, and recovery evidence |
| `core/meetings/services/bot/src/index.ts` | Creates the tracker/publisher only when the ALLOY flag is enabled |
| `core/meetings/services/bot/src/pipeline.ts` | Passes the optional tracker into the real gmeet pipeline |
| `deploy/lite/bin/vexa-bot-launch` | Explicitly forwards the opt-in flag to dynamically launched bots |
| `core/meetings/services/meeting-api/src/meeting_api/collector/alloy_stt_status.py` | Parses snapshots, computes health, and aggregates only verified meetings |
| `core/meetings/services/meeting-api/src/meeting_api/collector/app.py` | Adds the owner-scoped `GET /alloy/stt/status` route |
| `core/meetings/services/meeting-api/src/meeting_api/app.py` | Wires the optional telemetry reader through the unified app factory |
| `core/meetings/services/meeting-api/src/meeting_api/__main__.py` | Supplies the production Redis client to the reader |
| `core/meetings/services/meeting-api/tests/test_alloy_stt_status.py` | Owner isolation, stale/malformed/error, and aggregation tests |
| `core/meetings/services/meeting-api/tests/test_alloy_stt_status_live_redis.py` | Real Redis read/TTL integration test |
| `core/gateway/services/gateway/src/gateway/app.py` | Authenticated forwarding route to Meeting API |
| `core/gateway/services/gateway/tests/test_proxy.py` | Verbatim forwarding and injected identity regression test |
| `clients/terminal/src/workbench/alloySttTelemetry.ts` | API types, validation, health state, and one visibility-aware polling store |
| `clients/terminal/src/workbench/AlloySttStatus.tsx` | Compact footer and expandable per-meeting details |
| `clients/terminal/src/workbench/Workbench.tsx` | Mounts one global monitor in place of `reset layout` when enabled |
| `clients/terminal/src/workbench/__tests__/alloySttTelemetry.test.ts` | Poll cadence, visibility, stale retention, and parsing tests |
| `clients/terminal/src/workbench/__tests__/AlloySttStatus.test.tsx` | Footer states, colors, details, and fallback tests |
| `docs/ALLOY-CUSTOMIZATIONS.md` | Registers the customization and rollback flag |
| `deploy/lite/README.md` | Documents local activation and operational interpretation |

---

### Task 1: Versioned Queue Telemetry Tracker

**Files:**

- Create: `core/meetings/modules/gmeet-pipeline/src/alloy-stt-telemetry.ts`
- Create: `core/meetings/modules/gmeet-pipeline/src/alloy-stt-telemetry.test.ts`
- Modify: `core/meetings/modules/gmeet-pipeline/src/index.ts`

**Interfaces:**

- Produces:

```ts
export type AlloySttTelemetryError = {
  code: string;
  message: string;
};

export type AlloySttTelemetrySnapshotV1 = {
  version: 1;
  meeting_id: string;
  native_meeting_id: string;
  updated_at_ms: number;
  active_requests: number;
  active_audio_sec: number;
  waiting_channels: number;
  queued_audio_sec: number;
  latest_captured_audio_end_ms: number | null;
  latest_processed_audio_end_ms: number | null;
  lag_sec: number;
  rtf_ema: number | null;
  processed_windows: number;
  superseded_windows: number;
  last_error: AlloySttTelemetryError | null;
};

export interface AlloySttTelemetryTracker {
  captured(channelId: string, audioEndMs: number): void;
  queued(channelId: string, audioSec: number): void;
  superseded(channelId: string, audioSec: number): void;
  started(channelId: string, audioSec: number): void;
  completed(input: {
    channelId: string;
    audioSec: number;
    audioEndMs: number;
    processingDurationMs: number;
  }): void;
  failed(channelId: string, error: AlloySttTelemetryError): void;
  recovered(): void;
  snapshot(): AlloySttTelemetrySnapshotV1;
}

export function createAlloySttTelemetryTracker(input: {
  meetingId: string;
  nativeMeetingId: string;
  now?: () => number;
}): AlloySttTelemetryTracker;
```

- Invariant: tracker stores durations and timestamps only; it never stores audio payloads.

- [ ] **Step 1: Write exact tracker RED tests**

Cover these named cases in `alloy-stt-telemetry.test.ts`:

```ts
test("ALLOY tracker moves queued audio into active and returns to idle", () => {});
test("ALLOY tracker replaces one pending channel without inflating wait depth", () => {});
test("ALLOY tracker computes lag from audio timeline rather than wall-clock silence", () => {});
test("ALLOY tracker computes EMA RTF from submitted audio duration", () => {});
test("ALLOY tracker preserves counters on failure and clears error on recovery", () => {});
```

Assert exact values, including:

```ts
assert.equal(snapshot.waiting_channels, 1);
assert.equal(snapshot.queued_audio_sec, 4.25);
assert.equal(snapshot.superseded_windows, 1);
assert.equal(snapshot.lag_sec, 3);
assert.equal(snapshot.rtf_ema, 0.8);
```

- [ ] **Step 2: Run the tracker RED**

Run:

```powershell
pnpm --filter @vexa/gmeet-pipeline exec tsx src/alloy-stt-telemetry.test.ts
```

Expected: FAIL because the production tracker module does not exist.

Expected duration: under 10 seconds.

Stop threshold: 30 seconds. If exceeded, terminate the node process and diagnose only this test.

- [ ] **Step 3: Implement the minimal tracker**

Use:

```ts
const pendingByChannel = new Map<string, number>();
const activeByChannel = new Map<string, number>();
let rtfEma: number | null = null;

const sum = (values: Iterable<number>) =>
  Array.from(values).reduce((total, value) => total + value, 0);

const lagSec = () => {
  if (latestCapturedAudioEndMs === null || latestProcessedAudioEndMs === null) return 0;
  return Math.max(0, (latestCapturedAudioEndMs - latestProcessedAudioEndMs) / 1000);
};
```

On `queued`, replace the channel's pending duration instead of adding another queue element. On `superseded`, increment `superseded_windows` once and replace that channel's pending duration. On `completed`, update:

```ts
const requestRtf = processingDurationMs / 1000 / audioSec;
rtfEma = rtfEma === null ? requestRtf : 0.2 * requestRtf + 0.8 * rtfEma;
```

Round only at presentation time; keep full precision in the snapshot.

- [ ] **Step 4: Run the tracker GREEN**

Run the exact command from Step 2.

Expected: all five named tests PASS.

- [ ] **Step 5: Export the public contract**

Export `createAlloySttTelemetryTracker`, `AlloySttTelemetryTracker`, and `AlloySttTelemetrySnapshotV1` from the package entrypoint. Do not export internal mutable state.

- [ ] **Step 6: Build the focused package**

Run:

```powershell
pnpm --filter @vexa/gmeet-pipeline run build
```

Expected: exit 0.

Expected duration: under 30 seconds.

Stop threshold: 60 seconds.

---

### Task 2: Instrument the Real GMeet Scheduling Boundary

**Files:**

- Modify: `core/meetings/modules/gmeet-pipeline/src/gmeet-pipeline.ts`
- Modify: `core/meetings/modules/gmeet-pipeline/src/alloy-channel-backpressure.test.ts`

**Interfaces:**

- Consumes: `AlloySttTelemetryTracker` from Task 1.
- Produces: optional pipeline configuration:

```ts
alloySttTelemetry?: AlloySttTelemetryTracker;
```

- [ ] **Step 1: Extend the existing real scheduler test with telemetry assertions**

Use the existing controlled STT completion gate, but pass the production tracker into the production pipeline. Add one named scenario:

```ts
test("ALLOY scheduler publishes real active pending superseded and completed transitions", async () => {});
```

Required sequence:

```text
first same-channel request starts -> active=1 wait=0
second same-channel request arrives -> active=1 wait=1
third same-channel request arrives -> active=1 wait=1 superseded=1
first completes -> active=1 wait=0
newest pending completes -> active=0 wait=0
```

The test may gate only the external STT completion promise. It must call the real production queue and tracker.

- [ ] **Step 2: Run the scheduler RED**

Run:

```powershell
pnpm --filter @vexa/gmeet-pipeline exec tsx src/alloy-channel-backpressure.test.ts
```

Expected: the new telemetry assertions FAIL because the scheduler does not emit transitions.

Expected duration: under 15 seconds.

Stop threshold: 45 seconds.

- [ ] **Step 3: Add transition calls beside real queue mutations**

At the existing production points:

```ts
telemetry?.captured(channelId, audioEndMs);
telemetry?.started(channelId, audioSec);
telemetry?.queued(channelId, audioSec);
telemetry?.superseded(channelId, audioSec);
telemetry?.completed({ channelId, audioSec, audioEndMs, processingDurationMs });
telemetry?.failed(channelId, { code, message });
```

Record `processingDurationMs` around the actual Whisper request. Do not include queue wait time.

Use `try/finally` so active counters are released on both success and failure. Do not catch or transform the production STT error merely for telemetry.

- [ ] **Step 4: Run the scheduler GREEN**

Run the exact command from Step 2.

Expected: existing backpressure assertions and the new telemetry scenario PASS.

- [ ] **Step 5: Run the tracker plus scheduler affected boundary**

Run:

```powershell
pnpm --filter @vexa/gmeet-pipeline exec tsx src/alloy-stt-telemetry.test.ts
pnpm --filter @vexa/gmeet-pipeline exec tsx src/alloy-channel-backpressure.test.ts
pnpm --filter @vexa/gmeet-pipeline run build
```

Expected: all exit 0.

---

### Task 3: Fault-Isolated Redis Snapshot Publisher and Bot Wiring

**Files:**

- Create: `core/meetings/services/bot/src/adapters/alloy-stt-redis.ts`
- Create: `core/meetings/services/bot/src/adapters/alloy-stt-redis.live.test.ts`
- Modify: `core/meetings/services/bot/src/index.ts`
- Modify: `core/meetings/services/bot/src/pipeline.ts`
- Modify: `deploy/lite/bin/vexa-bot-launch`

**Interfaces:**

- Consumes: `AlloySttTelemetrySnapshotV1`.
- Produces:

```ts
export type AlloySttSnapshotReason = "periodic" | "start" | "stop" | "error" | "recovery";

export function createAlloySttRedisPublisher(input: {
  redis: Pick<RedisClientType, "set">;
  ttlSec?: number;
  minIntervalMs?: number;
  now?: () => number;
  log?: (message: string, fields?: Record<string, unknown>) => void;
}): {
  publish(snapshot: AlloySttTelemetrySnapshotV1, reason: AlloySttSnapshotReason): Promise<void>;
};
```

- Redis command:

```ts
await redis.set(
  `alloy:stt:telemetry:${snapshot.meeting_id}`,
  JSON.stringify(snapshot),
  { EX: 15 },
);
```

- [ ] **Step 1: Write a real Redis integration RED**

The test reads `ALLOY_TEST_REDIS_URL`, connects with the production Redis client, uses a unique meeting id, and performs real `SET`, `GET`, `TTL`, overwrite, and cleanup.

Named cases:

```ts
test("ALLOY publisher stores a versioned snapshot with TTL in real Redis", async () => {});
test("ALLOY publisher throttles periodic writes but sends error and recovery immediately", async () => {});
test("ALLOY publisher failure never rejects the transcription caller", async () => {});
```

For the third case, stop using the Redis connection after setup and assert `publish()` resolves while recording one `[ALLOY]` warning. This controls the external failure seam but executes the real publisher.

- [ ] **Step 2: Run the Redis publisher RED**

Run:

```powershell
$env:ALLOY_TEST_REDIS_URL='redis://127.0.0.1:6379/0'
pnpm --filter @vexa/bot exec tsx src/adapters/alloy-stt-redis.live.test.ts
```

Expected: FAIL because the publisher does not exist.

Expected duration: under 20 seconds.

Stop threshold: 45 seconds. A refused connection is `BLOCKED_LOCAL_REDIS`, not a code PASS; start or expose the existing local Vexa Redis before continuing.

- [ ] **Step 3: Implement the publisher**

Required behavior:

```ts
if (reason === "periodic" && now() - lastWriteAt < minIntervalMs) return;
try {
  await redis.set(key, JSON.stringify(snapshot), { EX: ttlSec });
  lastWriteAt = now();
} catch (error) {
  log("[ALLOY] STT telemetry publish failed", {
    meetingId: snapshot.meeting_id,
    error: error instanceof Error ? error.message : String(error),
  });
}
```

Rate-limit state is per meeting publisher instance. Do not use a process-global timestamp that couples concurrent meetings.

- [ ] **Step 4: Run the real Redis GREEN**

Run the exact command from Step 2.

Expected: all three cases PASS and the test deletes its unique key.

- [ ] **Step 5: Wire the publisher at the bot composition root**

When `process.env.ALLOY_STT_TELEMETRY === "1"`:

```ts
const tracker = createAlloySttTelemetryTracker({
  meetingId: String(config.meeting_id),
  nativeMeetingId: config.native_meeting_id,
});
const publisher = createAlloySttRedisPublisher({ redis: redisClient });
```

Pass `tracker` into the real gmeet pipeline. Publish its current snapshot on:

```text
bot start -> reason start
ordinary tracker changes -> reason periodic
STT error -> reason error
first subsequent success -> reason recovery
bot shutdown -> reason stop
```

When disabled, pass `undefined` and allocate no timer.

- [ ] **Step 6: Forward the opt-in flag to spawned bots**

In `vexa-bot-launch`, include:

```sh
-e ALLOY_STT_TELEMETRY="${ALLOY_STT_TELEMETRY:-0}"
```

Preserve all existing ALLOY flags and user changes in this dirty file.

- [ ] **Step 7: Run focused bot boundaries**

Run:

```powershell
pnpm --filter @vexa/bot exec tsx src/adapters/alloy-stt-redis.live.test.ts
pnpm --filter @vexa/bot exec tsx src/pipeline.test.ts
pnpm --filter @vexa/bot run build
```

Expected: all exit 0.

Expected duration: under 90 seconds total.

Stop threshold: 150 seconds.

---

### Task 4: Owner-Scoped Meeting API Aggregator

**Files:**

- Create: `core/meetings/services/meeting-api/src/meeting_api/collector/alloy_stt_status.py`
- Create: `core/meetings/services/meeting-api/tests/test_alloy_stt_status.py`
- Create: `core/meetings/services/meeting-api/tests/test_alloy_stt_status_live_redis.py`
- Modify: `core/meetings/services/meeting-api/src/meeting_api/collector/app.py`
- Modify: `core/meetings/services/meeting-api/src/meeting_api/app.py`
- Modify: `core/meetings/services/meeting-api/src/meeting_api/__main__.py`

**Interfaces:**

- Produces:

```py
class AlloySttSnapshotReader(Protocol):
    async def read_many(self, meeting_ids: list[int]) -> list[dict]: ...

class RedisAlloySttSnapshotReader:
    def __init__(self, redis_client): ...
    async def read_many(self, meeting_ids: list[int]) -> list[dict]: ...

def aggregate_alloy_stt_status(
    snapshots: list[dict],
    *,
    now_ms: int,
) -> dict: ...
```

- Route:

```text
GET /alloy/stt/status
```

- [ ] **Step 1: Write aggregation and ownership RED tests**

Named cases:

```py
async def test_alloy_stt_status_returns_only_owner_active_meetings(): ...
async def test_alloy_stt_status_uses_sum_for_counts_and_max_for_lag_and_rtf(): ...
async def test_alloy_stt_status_marks_error_stale_and_lag_thresholds_red(): ...
async def test_alloy_stt_status_skips_malformed_and_unsupported_snapshots(): ...
async def test_alloy_stt_status_disabled_preserves_upstream_behavior(): ...
```

The route test must call the shipped FastAPI router. It may seed the existing production-compatible test store, but it must not duplicate route or aggregation logic in the test.

Owner case:

```text
user 10 owns meetings 101 and 102
user 20 owns meeting 201
Redis contains snapshots for all three
GET as user 10 returns only 101 and 102
```

- [ ] **Step 2: Run the owner/aggregation RED**

Run:

```powershell
Push-Location core/meetings/services/meeting-api
uv run pytest tests/test_alloy_stt_status.py -q
Pop-Location
```

Expected: FAIL because the reader, aggregator, and route do not exist.

Expected duration: under 30 seconds.

Stop threshold: 60 seconds.

- [ ] **Step 3: Implement strict snapshot parsing and aggregation**

Accept only:

```py
snapshot.get("version") == 1
snapshot.get("meeting_id") == str(verified_meeting_id)
isinstance(snapshot.get("native_meeting_id"), str)
```

Reject booleans where numeric fields are expected. Clamp no values silently; malformed records are omitted and reported with `[ALLOY]`.

Health:

```py
if last_error or age_sec > 5 or lag_sec > 15:
    health = "red"
elif rtf is not None and rtf > 1 or lag_sec >= 5:
    health = "amber"
else:
    health = "green"
```

Aggregate:

```py
active_requests = sum(item["active_requests"] for item in meetings)
waiting_channels = sum(item["waiting_channels"] for item in meetings)
queued_audio_sec = sum(item["queued_audio_sec"] for item in meetings)
lag_sec = max((item["lag_sec"] for item in meetings), default=0)
rtf = max((item["rtf_ema"] for item in meetings if item["rtf_ema"] is not None), default=None)
```

- [ ] **Step 4: Add the owner-scoped route**

Use the existing trusted header resolution and active status set:

```py
user_id = _resolve_user_id(x_user_id)
running = await store.list_meetings(
    user_id,
    status=_RUNNING_STATUSES,
    slim=True,
)
verified_ids = [int(meeting["id"]) for meeting in running]
snapshots = await alloy_stt_reader.read_many(verified_ids)
```

Do not accept a `meeting_ids` query/body parameter.

When `ALLOY_STT_TELEMETRY != "1"` or no reader is supplied:

```json
{"enabled": false, "generated_at_ms": 0, "aggregate": null, "meetings": []}
```

- [ ] **Step 5: Wire the production Redis reader**

`__main__.py` already owns the real `redis.asyncio` client. Create one `RedisAlloySttSnapshotReader(redis_client)` and pass it into `create_app`; thread the optional dependency through `meeting_api.app.create_app` into `collector.build_router`.

Use one bounded `MGET` for verified keys:

```py
keys = [f"alloy:stt:telemetry:{meeting_id}" for meeting_id in meeting_ids]
raw_values = await redis_client.mget(keys) if keys else []
```

- [ ] **Step 6: Run owner/aggregation GREEN**

Run the exact command from Step 2.

Expected: all named tests PASS.

- [ ] **Step 7: Add and run real Redis integration**

The live test connects to `ALLOY_TEST_REDIS_URL`, writes two versioned snapshots with real TTLs, calls `RedisAlloySttSnapshotReader.read_many`, verifies ordering/missing keys, and deletes its keys.

Run:

```powershell
$env:ALLOY_TEST_REDIS_URL='redis://127.0.0.1:6379/0'
Push-Location core/meetings/services/meeting-api
uv run pytest tests/test_alloy_stt_status_live_redis.py -q
Pop-Location
```

Expected: PASS against real Redis.

Expected duration: under 20 seconds.

Stop threshold: 45 seconds.

- [ ] **Step 8: Run the focused Meeting API affected boundary**

Run:

```powershell
Push-Location core/meetings/services/meeting-api
uv run pytest tests/test_alloy_stt_status.py tests/test_alloy_stt_status_live_redis.py tests/test_slim_meetings_list.py tests/test_xtenant_isolation.py -q
Pop-Location
```

Expected: all selected nodes PASS.

Expected duration: under 90 seconds.

Stop threshold: 150 seconds.

---

### Task 5: Authenticated Gateway Forwarding

**Files:**

- Modify: `core/gateway/services/gateway/src/gateway/app.py`
- Modify: `core/gateway/services/gateway/tests/test_proxy.py`

**Interfaces:**

- Consumes: Meeting API `GET /alloy/stt/status`.
- Produces: public authenticated gateway `GET /alloy/stt/status`.

- [ ] **Step 1: Write the gateway RED**

Add:

```py
async def test_alloy_stt_status_is_authenticated_and_forwarded_with_user_identity(): ...
```

Assert:

```text
missing API key -> 401 and no downstream request
valid API key -> downstream path /alloy/stt/status
client-supplied X-User-Id is stripped
gateway-resolved X-User-Id is injected
body/status are returned verbatim
```

- [ ] **Step 2: Run the gateway RED**

Run:

```powershell
Push-Location core/gateway/services/gateway
uv run pytest tests/test_proxy.py::test_alloy_stt_status_is_authenticated_and_forwarded_with_user_identity -q
Pop-Location
```

Expected: FAIL because the route is absent.

- [ ] **Step 3: Add the thin forwarding route**

```py
@app.get("/alloy/stt/status")
async def alloy_stt_status(request: Request):
    return await _forward("GET", _meeting("/alloy/stt/status"), request)
```

Do not add custom auth or identity parsing; `_forward` remains the single security funnel.

- [ ] **Step 4: Run the gateway GREEN and affected proxy boundary**

Run:

```powershell
Push-Location core/gateway/services/gateway
uv run pytest tests/test_proxy.py::test_alloy_stt_status_is_authenticated_and_forwarded_with_user_identity -q
uv run pytest tests/test_proxy.py tests/test_edge_guard.py -q
Pop-Location
```

Expected: all selected nodes PASS.

Expected duration: under 60 seconds.

Stop threshold: 120 seconds.

---

### Task 6: One Visibility-Aware Terminal Polling Store

**Files:**

- Create: `clients/terminal/src/workbench/alloySttTelemetry.ts`
- Create: `clients/terminal/src/workbench/__tests__/alloySttTelemetry.test.ts`

**Interfaces:**

- Produces:

```ts
export type AlloySttApiState =
  | { kind: "loading"; lastValid: AlloySttStatusResponse | null }
  | { kind: "disabled"; lastValid: null }
  | { kind: "ready"; value: AlloySttStatusResponse }
  | { kind: "unavailable"; lastValid: AlloySttStatusResponse | null; message: string };

export function subscribeAlloySttTelemetry(
  listener: (state: AlloySttApiState) => void,
): () => void;

export function getAlloySttTelemetrySnapshot(): AlloySttApiState;
```

- Fetch target: `/api/alloy/stt/status`.

- [ ] **Step 1: Write polling and parser RED tests**

Named cases:

```ts
test("ALLOY store starts exactly one poller for multiple subscribers", async () => {});
test("ALLOY store polls every second while visible", async () => {});
test("ALLOY store pauses while hidden and refreshes immediately when visible", async () => {});
test("ALLOY store retains last valid data when fetch fails", async () => {});
test("ALLOY store rejects malformed response instead of fabricating zero", async () => {});
test("ALLOY store exposes disabled without continued polling", async () => {});
```

Use Vitest fake time only for the browser clock and a controlled `fetch` result only for the external HTTP seam. Execute the real production store and parser.

- [ ] **Step 2: Run the store RED**

Run:

```powershell
pnpm --filter @vexa/terminal exec vitest run src/workbench/__tests__/alloySttTelemetry.test.ts
```

Expected: FAIL because the store does not exist.

Expected duration: under 20 seconds.

Stop threshold: 45 seconds.

- [ ] **Step 3: Implement one module-level store**

Required lifecycle:

```ts
const POLL_MS = 1000;
const listeners = new Set<(state: AlloySttApiState) => void>();
let timer: ReturnType<typeof setTimeout> | null = null;
let requestGeneration = 0;

function schedule(): void {
  if (document.visibilityState !== "visible" || listeners.size === 0) return;
  timer = setTimeout(() => void refresh(), POLL_MS);
}
```

On the first subscriber, attach one `visibilitychange` listener and refresh immediately. On the last unsubscribe, clear the timer and remove the visibility listener. Increment `requestGeneration` so late results from an obsolete request are ignored.

Validate required fields and schema version. Keep the last valid response when a later request fails.

- [ ] **Step 4: Run the store GREEN**

Run the exact command from Step 2.

Expected: all six tests PASS.

---

### Task 7: Global Footer Indicator and Per-Meeting Details

**Files:**

- Create: `clients/terminal/src/workbench/AlloySttStatus.tsx`
- Create: `clients/terminal/src/workbench/__tests__/AlloySttStatus.test.tsx`
- Modify: `clients/terminal/src/workbench/Workbench.tsx`

**Interfaces:**

- Consumes: `subscribeAlloySttTelemetry` and `getAlloySttTelemetrySnapshot`.
- Produces:

```tsx
export function AlloySttStatus(props: {
  fallback: React.ReactNode;
}): React.ReactElement;
```

- [ ] **Step 1: Write component RED tests**

Named cases:

```ts
test("ALLOY footer renders STT idle with no active snapshots", () => {});
test("ALLOY footer renders compact aggregate and worst health color", () => {});
test("ALLOY footer opens per-meeting details on click", () => {});
test("ALLOY footer renders unavailable with muted last valid values", () => {});
test("ALLOY footer renders reset-layout fallback when feature is disabled", () => {});
```

Assert accessible button text and role. Do not assert fragile generated class names.

- [ ] **Step 2: Run the component RED**

Run:

```powershell
pnpm --filter @vexa/terminal exec vitest run src/workbench/__tests__/AlloySttStatus.test.tsx
```

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement compact formatting**

Render:

```text
STT · {meetings} mtg · active {active_requests} · wait {waiting_channels} ·
audio {queued_audio_sec.toFixed(1)}s · lag {Math.round(lag_sec)}s ·
RTF {rtf === null ? "—" : rtf.toFixed(2)}
```

Use semantic state attributes:

```tsx
<button
  type="button"
  aria-expanded={open}
  aria-controls="alloy-stt-details"
  data-health={health}
>
```

Details list one row per meeting and include `updated`, `superseded`, and bounded error text.

- [ ] **Step 4: Replace only the footer control**

In `Workbench.tsx`, retain the existing reset-layout button as the `fallback` node and replace its direct rendering with:

```tsx
<AlloySttStatus
  fallback={
    <button onClick={() => layout.resetLayout()} title="Reset layout">
      reset layout
    </button>
  }
/>
```

Mount no additional poller in screen-specific surfaces.

- [ ] **Step 5: Run the component GREEN and Terminal affected boundary**

Run:

```powershell
pnpm --filter @vexa/terminal exec vitest run src/workbench/__tests__/AlloySttStatus.test.tsx
pnpm --filter @vexa/terminal exec vitest run src/workbench/__tests__/alloySttTelemetry.test.ts
pnpm --filter @vexa/terminal exec vitest run src/workbench/__tests__/layout.preview.test.ts
pnpm --filter @vexa/terminal run build
```

Expected: all selected tests PASS and Next build exits 0.

Expected duration: 2-5 minutes.

Stop threshold: 8 minutes.

---

### Task 8: Deployment Flag and Operator Documentation

**Files:**

- Modify: `docs/ALLOY-CUSTOMIZATIONS.md`
- Modify: `deploy/lite/README.md`
- Modify: `deploy/lite/Makefile` only if its local run/build target explicitly enumerates ALLOY environment variables

**Interfaces:**

- Produces the documented activation:

```text
ALLOY_STT_TELEMETRY=1
```

- [ ] **Step 1: Register the customization**

Document:

```text
owner: gmeet scheduler -> Redis snapshot -> Meeting API -> gateway -> Terminal footer
default: disabled
rollback: set ALLOY_STT_TELEMETRY=0 and restart the Lite container/bots
Redis key: alloy:stt:telemetry:{meeting_id}
TTL: 15 seconds
poll cadence: 1 second while visible
```

- [ ] **Step 2: Add the operator interpretation**

Explain:

```text
active: Whisper calls executing now
wait: channels waiting behind an active call
audio: queued audio seconds
lag: newest captured audio end minus newest processed audio end
RTF: processing seconds divided by submitted audio seconds
superseded: older pending windows replaced under backpressure
```

State that telemetry errors do not stop transcription and that `STT unavailable` must not be interpreted as zero backlog.

- [ ] **Step 3: Preserve dirty deployment edits**

Before patching `Makefile` or `vexa-bot-launch`, compare their current dirty diff and add only the telemetry flag. Do not rewrite or reorder unrelated ALLOY flags.

- [ ] **Step 4: Run documentation/config focused checks**

Run:

```powershell
rg -n "ALLOY_STT_TELEMETRY|alloy:stt:telemetry|STT unavailable" docs/ALLOY-CUSTOMIZATIONS.md deploy/lite/README.md deploy/lite/Makefile deploy/lite/bin/vexa-bot-launch
Push-Location deploy/lite
python -m pytest tests/test_env_file_hygiene.py -q
Pop-Location
```

Expected: all four contract terms are present in their owners and env hygiene PASSes.

---

### Task 9: Independent Review, Affected Evidence, and Real Live Acceptance

**Files:**

- Review all files listed in the File Map.
- Create: `docs/reviews/2026-07-27-alloy-stt-queue-telemetry-review.md`
- Update: `docs/ALLOY-CUSTOMIZATIONS.md` only if review findings require a documented contract correction.

**Interfaces:**

- Consumes all preceding tasks.
- Produces independent review evidence and the only permitted basis for a completion claim.

- [ ] **Step 1: Request independent read-only review**

Reviewer must check:

```text
spec coverage
real scheduler instrumentation
no duplicate/fake metric calculations
counter balance on success/failure/cancellation
per-meeting rate limiting
Redis TTL and fault isolation
X-User-Id owner isolation
no browser Redis access
one Terminal poller
stale/unavailable honesty
flag-disabled upstream behavior
dirty-file preservation
no unrelated refactor
```

Record Critical, Important, Minor, and open questions with exact file/line references.

- [ ] **Step 2: Fix every Critical/Important finding through named RED/GREEN**

For each finding:

```text
add or identify one exact failing node
confirm expected RED reason
apply minimal fix
run exact GREEN
rerun only the affected boundary
request re-review of the changed scope
```

Do not proceed with an open Critical or Important finding.

- [ ] **Step 3: Run the complete affected boundary**

Run sequentially:

```powershell
pnpm --filter @vexa/gmeet-pipeline exec tsx src/alloy-stt-telemetry.test.ts
pnpm --filter @vexa/gmeet-pipeline exec tsx src/alloy-channel-backpressure.test.ts

$env:ALLOY_TEST_REDIS_URL='redis://127.0.0.1:6379/0'
pnpm --filter @vexa/bot exec tsx src/adapters/alloy-stt-redis.live.test.ts
pnpm --filter @vexa/bot exec tsx src/pipeline.test.ts

Push-Location core/meetings/services/meeting-api
uv run pytest tests/test_alloy_stt_status.py tests/test_alloy_stt_status_live_redis.py tests/test_slim_meetings_list.py tests/test_xtenant_isolation.py -q
Pop-Location

Push-Location core/gateway/services/gateway
uv run pytest tests/test_proxy.py tests/test_edge_guard.py -q
Pop-Location

pnpm --filter @vexa/terminal exec vitest run src/workbench/__tests__/alloySttTelemetry.test.ts src/workbench/__tests__/AlloySttStatus.test.tsx src/workbench/__tests__/layout.preview.test.ts

pnpm --filter @vexa/gmeet-pipeline run build
pnpm --filter @vexa/bot run build
pnpm --filter @vexa/terminal run build
```

Expected duration: 4-8 minutes.

Stop threshold: 12 minutes. Stop the active command, retain its exact output, and diagnose the single failing boundary. Do not restart the entire sequence unchanged.

- [ ] **Step 4: Build the local Lite image once**

Purpose: publish the verified source into the locally running product image.

Run:

```powershell
Push-Location deploy/lite
$env:ALLOY_STT_TELEMETRY='1'
make build
Pop-Location
```

Expected duration: 8-12 minutes.

Stop threshold: 15 minutes. If the threshold is reached, terminate the correlated build process tree, retain the last active Docker stage, and diagnose that stage. Do not claim the image was published and do not run an unchanged rebuild.

- [ ] **Step 5: Start the local stack with telemetry enabled**

Use the existing Lite start target and preserve all already approved ALLOY flags:

```powershell
Push-Location deploy/lite
$env:ALLOY_STT_TELEMETRY='1'
make up
Pop-Location
```

Confirm through the product API, not container self-report:

```powershell
Invoke-RestMethod -Headers @{ 'X-API-Key' = '<locally configured test key>' } `
  -Uri 'http://localhost:8100/alloy/stt/status'
```

Secret entry remains local. Do not write the API key into the plan, source, logs, or chat.

- [ ] **Step 6: Run bounded real meeting acceptance**

Use:

```text
one real Vexa bot in a Google Meet
the existing recorded meeting played with shared tab audio
real Redis
real Faster Whisper
Terminal at http://localhost:3001
```

Observe for 10 minutes:

```text
footer visible on Meetings, Infra, Routines, and meeting detail screens
active rises while Whisper executes
wait/audio rise only when pending work exists
lag does not grow during silence
RTF changes after completed requests
per-meeting details use the native meeting id
snapshot age updates
Redis key TTL remains near 15 seconds during activity
stopping the bot removes the row after TTL
transcript continues if telemetry API is temporarily unavailable
```

Stop threshold: 15 minutes. If transcript or telemetry stalls, capture one timestamped API response, Redis snapshot, bot log interval, and Terminal state; then stop and diagnose. Do not repeat unchanged.

- [ ] **Step 7: Record honest completion evidence**

The review artifact must include:

```text
exact commands and durations
pass/fail counts
real Redis URL class without credentials
meeting native id
snapshot key and TTL evidence
observed max active/wait/audio/lag/RTF
error/recovery behavior
build result
open findings
dirty Git/index state
external mutations performed
```

Permitted final states:

```text
complete
implementation complete / live evidence blocked_external
in progress
```

Do not claim complete from unit tests, a successful image build, or the presence of footer text alone.

## Self-Review Result

- Spec coverage: all 15 design sections map to Tasks 1-9.
- Placeholder scan: no implementation step delegates unspecified error handling, testing, ownership, or schema decisions.
- Type consistency: snapshot field names are identical across tracker, Redis, Meeting API, gateway, store, and component.
- Security consistency: browser supplies no meeting IDs; gateway injects identity; Meeting API derives verified active meeting IDs.
- Evidence consistency: controlled seams are limited to external timing/transport boundaries; real Redis and real bot/Whisper acceptance remain mandatory.
- Git consistency: no task stages or commits files without a new direct user instruction.
