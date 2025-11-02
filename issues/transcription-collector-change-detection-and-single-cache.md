---
title: Transcription Collector Simplification (Change-Only Mutable Publishing, Single Redis Cache)
type: Architecture Decision & Implementation Plan
status: Approved
priority: High
components: [transcription-collector, api-gateway]
created: 2025-10-11
related: [bot-reconfiguration-identity-mismatch.md, meeting-token-and-meeting-id-auth.md]
owners: [dgrankin]
version: 1.0
---

## Executive Summary

The transcription collector emits too many `transcript.mutable` frames because every write to the Redis Hash triggers a publish, even when content is identical. We will simplify the design to:

- Use a single Redis cache per meeting (`meeting:{meeting_id}:segments`) as the live source of truth.
- Publish `transcript.mutable` only when render-relevant fields change (change-only publishing).
- Guarantee absolute timestamps on every segment by caching `MeetingSession` start time in Redis at `session_start` and reading it for all `transcription` messages.
- Persist stable segments to Postgres after final filtering/deduplication.
- Remove `transcript.finalized` pub/sub (clients already ignore it); REST remains the source of complete merged history.

This reduces Redis churn, avoids duplicate WS frames, and keeps the persistence path clean.

---

## Current Architecture (Problems)

1. Every batch stored to `meeting:{meeting_id}:segments` is published as `transcript.mutable`, regardless of whether content changed.
2. Absolute times are added only “if available,” depending on DB resolution timing for `MeetingSession.session_start_time`.
3. A separate pub-signature cache increases key count and complexity.
4. `transcript.finalized` messages are published but not used by clients per `docs/websocket.md`.

---

## Decision

Adopt a single-cache, change-only publishing model with guaranteed absolute timestamps:

1) Single cache of live segments per meeting
- Key: `meeting:{meeting_id}:segments` (Redis Hash)
- Field: `start_time_key` (rounded to 3 decimals) — see Multi-Session note below
- Value (MANDATORY fields): `session_uid`, `text`, `speaker`, `language`, `end_time`, `updated_at`, `absolute_start_time`, `absolute_end_time`
- TTL refreshed on each write (uses `REDIS_SEGMENT_TTL`).

2) Change-only publishing
- For each incoming segment, compute normalized fields: `{ text, speaker, language, round(end_time, 3), absolute_start_time, absolute_end_time }`.
- Fetch current value from the Hash; if normalized content differs, HSET and include segment in a `transcript.mutable` frame; otherwise ignore.
- Publish one `transcript.mutable` per batch with only the changed segments.

3) Absolute times are always present
- On `session_start`, write `MeetingSession.session_start_time` into Redis: `meeting_session:{session_uid}:start` (+TTL).
- On `transcription`, read from Redis first; fallback to DB only if missing; compute and STORE `absolute_start_time`/`absolute_end_time` for every segment at ingestion.
- On `session_end`, delete the `meeting_session:{session_uid}:start` key.

4) Persistence path
- Background task scans `active_meetings`, reads `meeting:{id}:segments` sorted by start.
- A segment is stable if `updated_at < now - IMMUTABILITY_THRESHOLD`.
- Perform a final speaker remap if uncertain, then filter/dedup via `TranscriptionFilter`.
- Batch insert to Postgres, then `HDEL` processed hash fields. If the hash becomes empty, `SREM active_meetings`.
- Do not publish `transcript.finalized` (clients ignore it); REST merges DB+Redis.

5) Index of active meetings
- Keep `active_meetings` (Redis Set) as a lightweight index to avoid SCAN; add meeting on write; remove when empty.

---

## Architecture / End-to-End Flow

### Ingest (Mutable, Real-Time)

1. WhisperLive → Redis Stream `transcription_segments`.
2. Collector consumes and validates (future: via MeetingToken HS256 per related ADR).
3. Resolve `meeting_id`, map speaker (from Redis ZSET of speaker events), resolve session start time from `meeting_session:{uid}:start`.
4. Compute absolute times; normalize incoming vs existing segment.
5. If changed:
   - HSET `meeting:{meeting_id}:segments` only for changed keys.
   - SADD `active_meetings` and EXPIRE the Hash.
   - Publish `transcript.mutable` with only changed segments to `tc:meeting:{meeting_id}:mutable`.
6. If identical: do nothing (no HSET, no publish).

### Persistence (Immutable, Batched)

1. Every `BACKGROUND_TASK_INTERVAL` seconds:
   - SMEMBERS `active_meetings`.
   - For each `meeting_id`, HGETALL `meeting:{meeting_id}:segments`.
   - For segments with `updated_at < now - IMMUTABILITY_THRESHOLD`:
     - Optional final speaker remap if missing/uncertain.
     - Filter/dedup with `TranscriptionFilter`.
     - Batch insert to Postgres.
     - HDEL processed start_time fields from the Hash.
   - If Hash becomes empty: SREM from `active_meetings`.
2. No `transcript.finalized` publish (not consumed; avoids extra frames).

### Retrieval (REST + WebSocket)

- WebSocket: Clients receive `transcript.mutable` frames only when content actually changes.
- REST GET /transcripts: Merge DB (immutable) with remaining Redis (mutable), compute absolute times, sort by absolute time, deduplicate adjacent identical-text overlaps.

---

## Redis Keys & Memory Safety

- `meeting:{meeting_id}:segments` (Hash) → TTL refreshed on write.
- `meeting_session:{session_uid}:start` (String) → TTL set on write; deleted on session end.
- `active_meetings` (Set) → used as index; SREM when meeting hash becomes empty.
- No separate pub-signature key; change detection compares incoming segment with the existing hash value.

This ensures bounded memory via TTLs and explicit cleanup.

---

## Session Identity and Multi-Session Handling

- Every cached segment MUST include `session_uid`. This is required to derive absolute times (and for speaker mapping provenance).
- Sequential sessions for the same meeting are supported because segments from prior sessions are either persisted and removed or expire by TTL.
- If overlapping sessions are possible in rare cases, avoid field collisions by namespacing the Hash field:
  - Option A (default, simpler): `field = f"{start_time:.3f}"` assuming no overlap; rely on persistence/TTL.
  - Option B (collision-proof): `field = f"{session_uid}:{start_time:.3f}"` to guarantee uniqueness across sessions.
- REST can trust `absolute_*` stored on ingestion; recomputation remains as a fallback for DB-only rows.

---

## Compatibility with Related ADRs (Meeting-only; Fail-Fast)

- Meeting-ID addressing (see `bot-reconfiguration-identity-mismatch.md`): mandatory; the only WS publish channel is `tc:meeting:{meeting_id}:mutable`.
- MeetingToken (HS256) (see `meeting-token-and-meeting-id-auth.md`): mandatory; collector verifies tokens and extracts `meeting_id` directly.

### Fail-Fast Requirements
- Reject ingestion if `meeting_id` or MeetingToken is missing/invalid.
- Require `session_uid` on `session_start` and `transcription` for absolute timing and provenance; if missing on `session_start`, NACK; if missing on `transcription`, accept but log warning and skip absolute-time mapping for those segments.

---

## Implementation Plan (Mandatory; No Flags)

1. Change-only publishing in `streaming/processors.py`
   - Add normalized comparison against current Hash value; HSET and publish only when different.
2. Guarantee absolute times
   - On `session_start`: cache start time in Redis with TTL.
   - On `transcription`: load start time from Redis; fallback to DB (should rarely be needed).
   - On `session_end`: delete cached start time.
3. Persistence task (`background/db_writer.py`)
   - Keep final speaker remap, filter/dedup, batch insert, and HDEL.
   - Remove `transcript.finalized` publish.
4. Keep `active_meetings` index and cleanup when empty.
5. WS channels are meeting-id only; MeetingToken verification enabled by default.

---

## Testing

- Unit: normalization diff, skip of identical payloads, TTL refresh, session start cache read/write.
- Integration: live stream → mutable publish only on change; persistence moves stable segments to DB and removes from Redis; REST returns merged view.
- Load: high-frequency partial updates don’t spam WS; Redis memory stable under TTL.
- Failure: missing session start in Redis (DB fallback); Redis connection blips; idempotent behavior on reconnect.

---

## Acceptance Criteria

- `transcript.mutable` frames are emitted only when `text/speaker/language/end_time/absolute_*` change.
- All live segments include `absolute_start_time` and `absolute_end_time`.
- Stable segments move to Postgres and are removed from Redis.
- No `transcript.finalized` frames emitted.
- Redis memory bounded; no key leaks after meeting completion.

---

## Risks & Mitigations

- Risk: Temporary unavailability of session start in Redis on first transcription message.
  - Mitigation: DB fallback; cache immediately after first success.
- Risk: Clients relying on `transcript.finalized` frames.
  - Mitigation: Doc states clients ignore finalized; verify no consumers rely on it before removal.

---

## References

- `vexa/services/transcription-collector/streaming/processors.py`
- `vexa/services/transcription-collector/background/db_writer.py`
- `vexa/docs/websocket.md`
- `vexa/issues/bot-reconfiguration-identity-mismatch.md`
- `vexa/issues/meeting-token-and-meeting-id-auth.md`


