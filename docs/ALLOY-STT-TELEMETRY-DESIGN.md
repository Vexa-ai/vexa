# ALLOY STT Queue Telemetry

Status: approved design, implementation not started

## 1. Purpose

Add a global, real-time STT load indicator to the Vexa Terminal so an operator can see whether transcription is keeping up with incoming meeting audio.

The indicator replaces the low-value `reset layout` footer text and remains visible on every Terminal screen.

This is an ALLOY customization. It must be opt-in, preserve upstream Vexa behavior when disabled, and follow the conventions documented in `docs/ALLOY-CUSTOMIZATIONS.md`.

## 2. User-visible contract

The compact footer indicator shows an aggregate across all active meetings owned by the current user:

```text
STT · 2 mtg · active 1 · wait 2 · audio 18.4s · lag 26s · RTF 1.42
```

Definitions:

- `mtg`: meetings that currently publish a fresh STT telemetry snapshot.
- `active`: Whisper requests currently executing.
- `wait`: audio channels with one pending request waiting behind an active request.
- `audio`: total duration of queued, not-yet-started audio.
- `lag`: worst meeting lag, measured from the end of the latest captured audio to the end of the latest successfully processed audio.
- `RTF`: worst current rolling real-time factor among active meetings. `1.0` means one second of audio takes one second to process.

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

Initial thresholds:

- Green: snapshot age is at most 3 seconds, there is no STT error, lag is below 5 seconds, and RTF is at most `1.0`.
- Amber: snapshot age is at most 3 seconds, there is no STT error, and either RTF is above `1.0` or lag is from 5 through 15 seconds.
- Red: an STT error is present, snapshot age exceeds 5 seconds, or lag exceeds 15 seconds.
- Muted: no active telemetry (`STT idle`) or the feature is disabled.

For aggregate health, the worst meeting determines the color.

Thresholds are presentation constants. They do not alter queue behavior, chunk cadence, concurrency, retries, or transcription output.

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

The bot process is the only authority for queue metrics because it owns the real STT scheduling boundary.

The Terminal must not infer queue state from transcript arrival, elapsed wall time, DOM state, or UI status labels.

Responsibilities are separated as follows:

- STT scheduling layer: records real queue transitions and processing timings.
- Telemetry publisher: builds and rate-limits immutable per-meeting snapshots.
- Redis: stores the latest per-meeting snapshot with a short TTL.
- Server API: authenticates the user, limits results to that user's meetings, and aggregates snapshots.
- Terminal store: polls and retains the latest valid response.
- Footer component: renders aggregate and per-meeting details.

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
Redis: alloy:stt:telemetry:{meeting_id}, TTL 15s
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
alloy:stt:telemetry:{meeting_id}
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

The publisher writes at most once per second during normal operation and writes immediately on start, stop, terminal error, and recovery.

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

- A request entering Whisper increments `active_requests`.
- A channel receiving audio while already active sets or replaces that channel's pending request.
- Replacing an older pending request increments `superseded_windows`.
- Starting a pending request decrements `waiting_channels` and moves its duration from `queued_audio_sec` to `active_audio_sec`.
- Completion or failure decrements `active_requests`.
- Failure updates `last_error` without corrupting queue counters.
- Successful recovery clears `last_error`.

The telemetry layer must not become another queue and must not retain audio payloads.

## 9. API and access control

Browser endpoint:

```text
GET /api/alloy/stt-status
```

The endpoint:

1. derives the current authenticated user from the server session;
2. obtains the current user's active meetings through the existing owner-scoped meeting boundary;
3. requests or reads telemetry only for those verified meeting IDs;
4. rejects browser-supplied meeting ownership claims;
5. returns aggregate and per-meeting snapshots.

Response shape:

```json
{
  "enabled": true,
  "generated_at_ms": 1785100000500,
  "aggregate": {
    "meetings": 2,
    "active_requests": 1,
    "waiting_channels": 2,
    "queued_audio_sec": 18.4,
    "lag_sec": 26.0,
    "rtf": 1.42,
    "health": "red"
  },
  "meetings": []
}
```

Aggregation rules:

- Counts and queued durations are summed.
- Lag and RTF use the maximum meeting value.
- Health uses the worst meeting state.
- Expired Redis keys are omitted.
- A stale but not-yet-expired snapshot is returned with red health and its real age.

The existing admin overview is not reused as the browser endpoint because it is administrator-scoped and can expose workloads belonging to other users.

## 10. Terminal polling

The Terminal uses one global store shared by all screens.

Polling behavior:

- poll every 1 second while the document is visible;
- stop periodic polling while the document is hidden;
- refresh immediately when the document becomes visible;
- never start one poller per screen or component;
- abort or ignore an older request when a newer lifecycle replaces it;
- retain the last valid snapshot across transient failures;
- clean up timers and listeners when the store is disposed.

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
- the API returns `enabled: false`;
- the Terminal renders no active telemetry and preserves the upstream behavior selected for the footer;
- no STT scheduling behavior changes.

All new diagnostics use the `[ALLOY]` prefix. Source comments that identify the customization use the `ALLOY:` prefix.

## 12. Failure semantics

- Redis unavailable: transcription continues; publishing records a bounded warning and retries on the next scheduled snapshot.
- API unavailable: footer shows `STT unavailable` and retains the last valid snapshot in muted form.
- Bot exits: Redis TTL removes its snapshot; the meeting disappears from the active aggregate.
- Malformed snapshot: skip that meeting, surface a bounded diagnostic, and do not fabricate zero values.
- Unsupported schema version: skip that snapshot and report an explicit compatibility error.
- STT request failure: preserve real queue counts, set `last_error`, and render red health.

Telemetry failure must never stop, restart, slow down, or mark the transcription pipeline successful or failed.

## 13. Verification strategy

Verification starts with exact narrow nodes. Broad suites and live gates are final boundaries only.

Required focused evidence:

1. Real queue-transition test at the actual gmeet-pipeline scheduling boundary:
   - one active request;
   - one same-channel pending request;
   - replacement increments `superseded_windows`;
   - completion promotes the pending request;
   - counters return to zero.
2. Real Redis integration test:
   - writes the schema;
   - refreshes TTL;
   - immediate error/stop snapshots;
   - expiration removes dead state.
3. Owner-scoped API test:
   - two users with separate meetings;
   - each response contains only the authenticated user's snapshots;
   - stale and malformed records are handled honestly.
4. Terminal store test:
   - one global poller;
   - one-second visible cadence;
   - hidden pause and visible refresh;
   - last-valid-state retention on failure.
5. Footer component test:
   - aggregate rendering;
   - health color;
   - `STT idle`;
   - `STT unavailable`;
   - click-to-expand per-meeting details.
6. Bounded local end-to-end run:
   - real bot;
   - real Redis;
   - real Faster Whisper path;
   - recorded meeting audio;
   - visible queue growth and recovery;
   - no claim of stability based only on synthetic dispatch.

Tests may control an external STT completion boundary to make queue timing deterministic, but they must execute the real production scheduler, metric collector, serializer, Redis transport, API aggregation, and UI store code.

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
- the local deployment environment example for `ALLOY_STT_TELEMETRY`;
- the affected operational runbook if one exists.

The current local Vexa image must not be described as published or verified merely because this design exists. The earlier full image build did not complete within its bounded build window. Telemetry implementation and its focused tests must be completed before a new bounded image build and live acceptance run.

No Git staging, commit, push, branch mutation, package installation, or remote deployment is part of this design approval.
