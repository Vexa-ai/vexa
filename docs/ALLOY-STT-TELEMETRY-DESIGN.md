# ALLOY STT Queue Telemetry

Status: implemented in source; focused offline and contract verification complete;
disposable-Redis, clean-image, and real-Meet acceptance pending

## 1. Purpose

Add one owner-scoped aggregate STT load indicator to the Vexa Terminal so an operator can see
whether transcription is keeping up with incoming meeting audio across that owner's active
meetings. Here, global means one Terminal view across per-meeting bot snapshots; it does not mean a
global concurrency limit across bot processes or the Whisper service.

When explicitly enabled, the indicator replaces the low-value `reset layout` footer text and
remains visible on every Terminal screen. When disabled, the upstream footer remains unchanged.

This is an ALLOY customization. It must be opt-in, preserve upstream Vexa behavior when disabled, and follow the conventions documented in `docs/ALLOY-CUSTOMIZATIONS.md`.

## 2. User-visible contract

The compact footer indicator shows an aggregate across all active meetings owned by the current user:

```text
STT 2 · 1 active · 2 waiting · 18.4s queued · lag 26.0s · RTF 1.42 · health red
```

Definitions:

- the number after `STT`: meetings that currently publish a valid STT telemetry snapshot.
- `active`: Whisper requests currently executing.
- `waiting`: audio channels with one pending request waiting behind an active request.
- `queued`: total duration of queued, not-yet-started audio.
- `lag`: worst meeting lag, measured from the end of the latest captured audio to the end of the latest successfully processed audio.
- `RTF`: worst current rolling real-time factor among active meetings. `1.0` means one second of audio takes one second to process.
- `health`: the server-computed worst health across the returned meetings.

When no meeting publishes telemetry, the footer shows:

```text
STT idle
```

If telemetry cannot be loaded, the footer shows:

```text
STT unavailable
```

The last valid values remain visible in a muted style where possible. API failure must never be presented as zero load.

Clicking the compact indicator opens details for each active meeting:

| Field | Meaning |
| --- | --- |
| Meeting | Native meeting identifier, for example `giq-hzmp-vnn` |
| Active | Whisper requests currently executing |
| Wait | Channels with pending audio |
| Queued audio | Pending audio duration |
| Lag | Capture-to-transcription lag |
| RTF | Rolling processing-to-audio ratio |
| Superseded | Pending windows replaced by newer pending audio under backpressure |
| Updated | Age of the latest telemetry snapshot |
| Error | Latest STT error, when present |

## 3. Health presentation

Meeting API owns these thresholds:

- Red: an STT error is present, snapshot age is greater than 5 seconds, or lag is greater than 15 seconds.
- Amber: otherwise, snapshot age is greater than 3 seconds, lag is at least 5 seconds, RTF is above
  `1.0`, or `active_requests > 0` while `processed_windows == 0` (the first request has not yet
  established completed STT flow).
- Green: otherwise.
- Muted: no valid active snapshots (`STT idle`).

For aggregate health, the worst meeting determines the color.

The thresholds are server-owned contract behavior. The Terminal renders the sealed aggregate
health rather than re-deriving it. Per-meeting detail labels are presentation-only and do not alter
the aggregate. No health threshold changes queue behavior, chunk cadence, concurrency, retries, or
transcription output.

## 4. Non-goals

This change does not:

- tune Whisper performance;
- change STT scheduling or backpressure behavior;
- add or remove audio windows;
- change transcript content or speaker attribution;
- expose other users' meetings;
- turn telemetry into an append-only analytics/event system;
- replace existing infrastructure diagnostics;
- add a new WebSocket protocol;
- make the browser query Redis directly.

## 5. Architecture

### 5.1 Ownership

The bot process is the only producer of a meeting snapshot, but execution truth comes from the
boundary that owns each transition:

- the per-channel scheduler owns pending, supersede, and turn-rotation state;
- `TranscriptionClient` owns the semaphore slot lifecycle and reports start/end only after real
  slot acquisition;
- the tracker consolidates those events into one immutable per-meeting snapshot.

`ALLOY_STT_MAX_CONCURRENCY` is therefore a per-bot client limit. It does not coordinate parallel
meetings or impose a Lite-wide/model-wide semaphore.

The Terminal must not infer queue state from transcript arrival, elapsed wall time, DOM state, or UI status labels.

Responsibilities are separated as follows:

- Per-channel scheduling layer: records pending and superseded transitions.
- STT client observer: records real slot acquisition, completion, failure, and processing time.
- Queue tracker: owns the consolidated per-meeting counters and RTF state.
- Telemetry publisher: builds and rate-limits immutable per-meeting snapshots.
- Redis: stores the latest per-meeting snapshot with a short TTL.
- Server API: authenticates the user, limits exact Redis reads to owner-owned active meetings,
  validates snapshots against the sealed contract, and computes the aggregate and its health.
- Terminal store: polls and retains the latest valid response.
- Footer component: renders the server aggregate and per-meeting details.

This keeps responsibilities narrow and avoids duplicating queue calculations across backend and UI.

### 5.2 Data flow

```text
audio channel
    |
    v
real STT scheduler/backpressure
    |
    +--> update in-memory counters
             |
             v
       ALLOY snapshot publisher
             |
             v
Redis: alloy:stt:telemetry:v1:{meeting_id}, TTL 15s
             |
             v
authenticated owner-scoped API
             |
             v
Terminal global store, polling every 1s while visible
             |
             v
global footer indicator + per-meeting details
```

## 6. Snapshot schema

Redis key:

```text
alloy:stt:telemetry:v1:{meeting_id}
```

Value:

```json
{
  "version": 1,
  "meeting_id": "137",
  "native_meeting_id": "giq-hzmp-vnn",
  "updated_at_ms": 1785100000000,
  "active_requests": 1,
  "active_audio_sec": 3.2,
  "waiting_channels": 1,
  "queued_audio_sec": 12.4,
  "latest_captured_audio_end_ms": 1785100000000,
  "latest_processed_audio_end_ms": 1785099981000,
  "lag_sec": 19.0,
  "rtf_ema": 1.42,
  "processed_windows": 42,
  "superseded_windows": 7,
  "last_error": null
}
```

Schema rules:

- `version` is required and allows compatible evolution.
- Counters are current state, not cumulative events, except `processed_windows` and `superseded_windows`.
- `waiting_channels` counts channels with pending audio, not raw chunks.
- `queued_audio_sec` excludes the currently executing request; `active_audio_sec` describes that request separately.
- `lag_sec` is derived from audio timeline positions, not from wall-clock silence. Lag must not grow while no new audio is captured.
- `last_error` contains a bounded, user-safe error code and message. It must not contain secrets or unbounded stack traces.
- Snapshot TTL is 15 seconds so dead bot state disappears automatically.

The publisher writes immediately when it starts, refreshes at most once per second during normal
operation, and permits only one write in flight. Stop waits only for a bounded interval, then
deletes the meeting key. Production composition does not promise a separate immediate write for
every tracker error or recovery; those states appear in the next successful snapshot.

## 7. RTF calculation

For each completed Whisper request:

```text
request_rtf = processing_duration_sec / audio_duration_sec
```

The snapshot exposes an exponential moving average:

```text
rtf_ema = 0.2 * request_rtf + 0.8 * previous_rtf_ema
```

Rules:

- Requests with zero or invalid audio duration are excluded.
- RTF uses the audio duration actually submitted to Whisper.
- Queue wait time is not included in RTF; it is represented by `lag_sec` and `queued_audio_sec`.
- The aggregate API reports the maximum meeting `rtf_ema`, not an average that could hide one overloaded meeting.

## 8. Backpressure metrics

The telemetry observes the existing ALLOY per-channel backpressure implementation.

Required transitions:

- Acquiring a real `TranscriptionClient` slot increments `active_requests`.
- A channel receiving audio while already active sets or replaces that channel's pending request.
- Replacing an older pending request increments `superseded_windows`.
- Promoting a pending request removes it from `waiting_channels`; its duration moves into
  `active_audio_sec` only when the STT client reports real slot acquisition.
- Completion or failure decrements `active_requests`.
- Failure updates `last_error` without corrupting queue counters.
- Successful recovery clears `last_error`.

The telemetry layer must not become another queue and must not retain audio payloads.

## 9. API and access control

Backend endpoint in Meeting API and Gateway:

```text
GET /alloy/stt/status
```

Terminal server proxy:

```text
GET /api/alloy/stt/status
```

The endpoint:

1. derives the current authenticated user from the server session;
2. obtains only owner-owned active meeting IDs through the dedicated owner boundary;
3. performs one bounded `MGET` for those exact versioned Redis keys;
4. rejects browser-supplied meeting ownership claims;
5. returns aggregate and per-meeting snapshots.

Transcript and workspace shares do not grant telemetry access. Invalid, incompatible,
version-mismatched, non-finite, or key/payload-mismatched snapshots are silently omitted while
valid neighbors remain available.

Response shape:

```json
{
  "version": 1,
  "enabled": true,
  "available": true,
  "updated_at_ms": 1785100000500,
  "aggregate": {
    "meetings": 2,
    "active_requests": 1,
    "waiting_channels": 2,
    "queued_audio_sec": 18.4,
    "lag_sec": 26.0,
    "rtf": 1.42,
    "health": "red"
  },
  "meetings": [],
  "error": null
}
```

Aggregation rules:

- Counts and queued durations are summed.
- Lag and RTF use the maximum meeting value.
- Health uses the worst meeting state.
- Expired Redis keys are omitted.
- A stale but not-yet-expired snapshot is returned with red health and its real age.

The existing admin overview is not reused as the browser endpoint because it is administrator-scoped and can expose workloads belonging to other users.

The sealed contract also carries disabled and dependency-unavailable goldens. In production,
however, exact opt-in composition leaves the Meeting API and Gateway routes absent when
`ALLOY_STT_TELEMETRY` is not exactly `1`.

## 10. Terminal polling

The Terminal uses one global store shared by all screens.

Polling behavior:

- poll every 1 second while the document is visible;
- make no telemetry request while the document is hidden;
- refresh immediately when the document becomes visible;
- never start one poller per screen or component;
- within one active polling generation, make at most one request and let timer/visibility triggers
  reuse its current promise;
- stop invalidates that generation without requiring cancellation of its network call; restart may
  start a fresh request while the old call remains pending, and generation fencing ignores the old
  result;
- retain the last valid snapshot across transient failures;
- clean up timers and listeners when the store is disposed.

The footer renders `Aggregate` and its `health` exactly as returned by Meeting API. It may classify
individual detail rows for presentation, but it must not summarize them into a competing aggregate.

Polling is preferred over extending the WebSocket protocol because:

- the data changes on a seconds-scale, not a frame-scale;
- the payload is a replaceable current snapshot;
- missed polls do not lose state;
- reconnect and page reload need no replay protocol;
- Redis TTL already provides liveness;
- WebSocket support would still require snapshot initialization, authorization, routing, heartbeat, reconnect, and stale-state semantics.

The API call must remain lightweight and bounded. A one-second poll is acceptable only for compact Redis lookups and owner-scoped aggregation, not for container inspection or expensive database scans.

## 11. Feature flag

The customization is enabled only when:

```text
ALLOY_STT_TELEMETRY=1
```

When disabled:

- bot snapshot publishing is disabled;
- Meeting API and Gateway do not register their telemetry routes;
- the Terminal creates no hook, subscription, timer, or fetch and preserves the upstream reset footer;
- no STT scheduling behavior changes.

All new diagnostics use the `[ALLOY]` prefix. Source comments that identify the customization use the `ALLOY:` prefix.

## 12. Failure semantics

- Redis publish unavailable: transcription continues; publishing records a bounded warning and retries on the next scheduled snapshot.
- Redis read unavailable: Meeting API returns the sealed unavailable `StatusResponse`; the footer
  shows `STT unavailable` and retains the last valid snapshot.
- API transport unavailable: the footer shows `STT unavailable` and retains the last valid snapshot.
- Bot exits: Redis TTL removes its snapshot; the meeting disappears from the active aggregate.
- Malformed, non-finite, or key/payload-mismatched snapshot: silently omit that meeting and do not
  fabricate zero values.
- Unsupported schema version: silently omit that snapshot.
- STT request failure: preserve real queue counts, set `last_error`, and render red health.

Telemetry failure must never stop, restart, slow down, or mark the transcription pipeline successful or failed.

## 13. Verification strategy

Verification starts with exact narrow nodes. Broad suites and live gates are final boundaries only.

Delivered focused evidence:

1. Queue-transition tests at the actual gmeet-pipeline and STT-client boundaries:
   - one active request;
   - one same-channel pending request;
   - replacement increments `superseded_windows`;
   - completion promotes the pending request;
   - counters return to zero;
   - RTF starts after slot acquisition and excludes queue waiting.
2. Sealed contract and golden validation for `Snapshot`, `Aggregate`, and `StatusResponse`.
3. Strict owner-scoped API tests:
   - two users with separate meetings;
   - each response contains only the authenticated user's snapshots;
   - transcript/workspace shares do not widen telemetry access;
   - invalid neighbors are silently omitted.
4. Terminal store and runtime opt-in tests:
   - one global poller;
   - one-second visible cadence;
   - hidden pause and visible refresh;
   - last-valid-state retention on failure.
5. Footer component test:
   - aggregate rendering;
   - server-owned health color;
   - `STT idle`;
   - `STT unavailable`;
   - click-to-expand per-meeting details.

Pending evidence:

1. Explicit integration lanes against a disposable Redis database:
   - writes and reads the sealed schema;
   - refreshes TTL;
   - cleanup removes ended meeting telemetry.
2. Clean Dockerfile build from the integrated source and proof that the running image matches it.
3. Bounded local end-to-end run:
   - real bot;
   - real Redis;
   - real Faster Whisper path;
   - recorded meeting audio;
   - visible queue growth and recovery;
   - no claim of stability based only on synthetic dispatch.
4. Real Google Meet transcription in Russian, English, and a code-switch sequence. Bundled local
   STT must use the multilingual make override
   `WHISPER_MODEL=Systran/faster-whisper-small`; the default and documented `.en` variants are
   English-only. This selects the backend model and does not pin the request language.

Focused tests may control an external STT completion boundary to make queue timing deterministic,
but their results claim only the production boundaries they execute. The disposable Redis and live
meeting rows remain open.

## 14. DRY and SOLID constraints

The implementation applies DRY and SOLID proportionately:

- one queue-metrics owner prevents duplicate calculations;
- one versioned snapshot interface separates producer, transport, and presentation;
- one global polling store prevents duplicate timers and requests;
- UI components depend on the API contract, not Redis or queue internals;
- telemetry is injected beside scheduling behavior rather than mixed into transcription decisions.

No speculative abstraction or unrelated refactoring is authorized.

## 15. Documentation and release boundaries

Implementation must update:

- `docs/ALLOY-CUSTOMIZATIONS.md`;
- `deploy/lite/README.md`;
- the implementation evidence plan;
- one per-change changelog fragment.

The current local Vexa image must not be described as matching or verifying the integrated source
merely because focused checks are green. The earlier clean Dockerfile build did not complete within
its bounded build window. A new bounded clean build, runtime provenance check, disposable Redis
lane, and live acceptance run remain separate evidence gates.

No Git staging, commit, push, branch mutation, package installation, or remote deployment is part of this design approval.
