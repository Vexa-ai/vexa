"""The background **db-writer** — flush live Redis segments (and processed notes) to the durable store.

RESTORES the parent loop the 0.12 carve dropped (0.10 ``meeting_api/collector/db_writer.py``
``process_redis_to_postgres``): the consumer (``ingest.py``) lands live segments in the Redis hash
``meeting:{id}:segments`` and the read path merges Postgres + that hash — but WITHOUT this writer
nothing ever moved segments INTO Postgres, so the ``transcriptions`` table stayed empty and a redis
eviction/restart lost the meeting's transcript forever (the release-blocking data-loss defect).

Parent semantics, kept exactly:

  * **cadence** — one tick per ``DB_WRITER_INTERVAL_S`` (parent ``BACKGROUND_TASK_INTERVAL``, 10s);
    the tick is a single explicit function (``db_writer_tick``) the eval drives directly, wrapped in
    the ``while True: tick; sleep`` poll by ``__main__`` like its three loop siblings.
  * **mutable-last-segment** — only segments whose ``updated_at`` is older than
    ``IMMUTABILITY_THRESHOLD`` (30s) are flushed; the still-mutable tail (drafts being refined)
    stays in Redis until it settles. A later rewrite of an already-flushed segment re-enters the
    hash and is flushed again — the sink upserts on ``(meeting_id, segment_id)`` so it lands as an
    UPDATE, never a duplicate.
  * **trim policy** — flushed (and empty-text) hash fields are HDEL'd **only after** the sink
    confirms the durable write; a failed write leaves the hash intact for the next tick. When a
    hash drains empty its meeting id leaves the ``active_meetings`` set. A permanent withdrawal
    refusal is different from a transient sink failure: it purges the live hash and queue entry so
    revoked transcript PII is neither persisted nor retained for retry.
  * **discovery** — the ``active_meetings`` set (maintained by ``append_segment``), UNIONed with a
    ``meeting:*:segments`` key scan so hashes written before the set existed (mid-upgrade) or after
    a set/hash divergence are still drained — self-healing, unlike the parent's set-only sweep.

Additions over the parent:

  * ``finalize_meeting(...)`` — the completion hook: flush EVERYTHING left (threshold 0, mutable
    tail included) the moment the lifecycle FSM lands on a terminal status, so a completed meeting's
    transcript is durable immediately instead of eventually.
  * ``flush_meeting_processed(...)`` — drain the copilot's cleaned-notes stream
    (``proc:meeting:{meeting_id}``, agent-worker the single writer, P23) into the meeting row's
    ``data['processed']`` JSONB (the documented meeting.data home; NO schema change), resuming
    from the persisted ``source_cursor``. Redis was the ONLY home of the processed doc before
    this — stopping the bot made the processed output unreachable over REST.

The persisted processed shape is ADDRESSABLE and VERSIONED (multi-consumer, per the release DoD):
``data.processed = {"views": [{id, kind, params, doc, source_cursor, updated_at}]}`` — ``params``
records the processing metadata APPLIED (provider/model/pipeline, stamped by the producing worker
on the stream entries — reproducibility), ``doc`` is the view body (``{"notes": [...]}`` for the
copilot's cleaned-transcript view), ``source_cursor`` the stream position the view reflects. A
LIST of views so multiple processings of one meeting (per-workspace views are coming) coexist;
today the collector maintains the ONE meeting-scoped copilot view, upserted by ``id``. The views
ride the sealed api.v1 responses' existing free-form ``data`` field (GET /transcripts +
GET /meetings) — no new REST surface, no contract change.

Everything here talks to redis through plain client calls (hgetall/hdel/smembers/scan/xrange) that
both ``redis.asyncio`` and ``fakeredis.aioredis`` satisfy, and to the durable store through two
getattr-guarded sink methods (``upsert_segments``, ``merge_processed_notes``) implemented by BOTH
``SqlAlchemyTranscriptStore`` (prod) and ``InMemoryTranscriptStore`` (tests) — so the whole writer
is unit-tested offline, no docker.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from .ports import TranscriptWriteRefused

log = logging.getLogger("meeting_api.collector.db_writer")

ACTIVE_MEETINGS_KEY = "active_meetings"
IMMUTABILITY_THRESHOLD = float(os.environ.get("IMMUTABILITY_THRESHOLD", "30"))

# ── the end-of-processing protocol (ADR 0027 / processed-notes.v1) ────────────────────────────────
# The copilot worker runs one final LLM beat AFTER session_end (~10s), then XADDs a `view_end`
# marker: the proc stream is COMPLETE at that entry. finalize_meeting's inline drain used to be the
# LAST drain ever (the meeting then left this writer's sweep), so the final beat's notes stayed in
# redis forever (run-46: durable cursor 1783512746260-0 < stream tail 1783512757882-0). Now a
# finalized meeting whose stream is not yet marker-complete PARKS in `processed_pending` (zset,
# score = deadline) and every tick re-drains it until the marker is seen — or the deadline passes
# (the P22 pairing: graceful marker, hard bounded guarantee for a worker that died markerless).
PROC_PENDING_KEY = "processed_pending"
PROC_PENDING_GRACE_SEC = float(os.environ.get("PROC_PENDING_GRACE_SEC", "120"))

# The one processed view the collector maintains today: the copilot's 1:1 cleaned transcript.
# Addressable by id inside data.processed.views[] so future processings (per-workspace views,
# summaries, translations) ADD views instead of overwriting this one.
PROC_VIEW_ID = "copilot-notes"
PROC_VIEW_KIND = "cleaned_transcript"


def segments_hash_key(meeting_id) -> str:
    """The live Redis hash of in-flight segments (``ingest`` writes it; the read path merges it)."""
    return f"meeting:{meeting_id}:segments"


def proc_stream_key(meeting_id) -> str:
    """The copilot's cleaned-notes stream for ONE meeting row — keyed by the NUMERIC meeting id
    (unique per row) so a re-sent bot on the same native link can never mix/clobber a previous
    meeting's processed doc (the native-id keying defect)."""
    return f"proc:meeting:{meeting_id}"


def _s(v) -> str:
    return v.decode() if isinstance(v, (bytes, bytearray)) else v


def _parse_updated_at(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        s = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


async def flush_meeting_segments(
    redis_c,
    sink,
    meeting_id: int,
    *,
    immutability_threshold: Optional[float] = None,
    now: Optional[datetime] = None,
) -> int:
    """Flush ONE meeting's immutable Redis-hash segments into the durable sink. Returns the count
    stored. Hash fields are removed ONLY after the sink confirmed the write (trim-after-confirm);
    empty-text segments are dropped from the hash without storing (parent behavior)."""
    threshold = IMMUTABILITY_THRESHOLD if immutability_threshold is None else immutability_threshold
    now = now or datetime.now(timezone.utc)
    hash_key = segments_hash_key(meeting_id)
    raw = await redis_c.hgetall(hash_key)
    if not raw:
        try:
            await redis_c.srem(ACTIVE_MEETINGS_KEY, str(meeting_id))
        except Exception:  # noqa: BLE001 — set upkeep is best-effort
            pass
        return 0

    cutoff = now - timedelta(seconds=threshold)
    batch: list[dict] = []
    done_fields: list = []  # flushed OR discarded — removed only after a confirmed write
    for field, value in raw.items():
        try:
            seg = json.loads(_s(value))
        except (json.JSONDecodeError, TypeError, ValueError):
            done_fields.append(field)  # unparseable — drop it (parent behavior)
            continue
        updated_at = _parse_updated_at(seg.get("updated_at"))
        if threshold > 0 and updated_at is not None and updated_at >= cutoff:
            continue  # still mutable — leave in the hash for the next tick
        if not (seg.get("text") or "").strip():
            done_fields.append(field)  # empty text — never stored (parent behavior)
            continue
        if not seg.get("segment_id"):
            seg = {**seg, "segment_id": _s(field)}  # the hash field IS the segment identity
        batch.append(seg)
        done_fields.append(field)

    if batch:
        # The durable write FIRST; only a confirmed write may trim redis. On a FAILED write,
        # re-arm the hash TTL before propagating: a completed meeting gets no more appends (nothing
        # re-arms the TTL), so a sink outage longer than the TTL would expire the tail unflushed
        # (#53 review, vector 2).
        try:
            await sink.upsert_segments(meeting_id, batch)
        except TranscriptWriteRefused:
            await redis_c.delete(hash_key)
            await redis_c.srem(ACTIVE_MEETINGS_KEY, str(meeting_id))
            return 0
        except Exception:
            import os as _os
            try:
                await redis_c.expire(hash_key, int(_os.environ.get("REDIS_SEGMENT_TTL", "3600")))
            except Exception:  # noqa: BLE001 — best-effort re-arm; the original error matters more
                pass
            raise
    if done_fields:
        await redis_c.hdel(hash_key, *done_fields)
    remaining = await redis_c.hlen(hash_key)
    if not remaining:
        try:
            await redis_c.srem(ACTIVE_MEETINGS_KEY, str(meeting_id))
        except Exception:  # noqa: BLE001
            pass
    return len(batch)


async def flush_meeting_processed(redis_c, sink, meeting_id: int) -> int:
    """Drain NEW entries of the meeting's processed-notes stream (``proc:meeting:{meeting_id}``,
    written by the agent worker) into the copilot view of the meeting row's
    ``data['processed']['views']`` JSONB via the sink, resuming from the view's persisted
    ``source_cursor`` (exclusive). Notes are merged by their ``id`` (== segment_id), so a refining
    re-emit updates in place. ``params`` (provider/model/pipeline, stamped by the worker on each
    entry) ride along into the view for reproducibility. Returns the count of notes merged."""
    merge = getattr(sink, "merge_processed_view", None)
    cursor_of = getattr(sink, "processed_view_cursor", None)
    if merge is None or cursor_of is None:
        return 0
    cursor = await cursor_of(meeting_id, PROC_VIEW_ID)
    start = "-" if not cursor else f"({cursor}"
    try:
        rows = await redis_c.xrange(proc_stream_key(meeting_id), min=start, max="+")
    except Exception:  # noqa: BLE001 — a missing/typed-over key must not break the segments flush
        return 0
    if not rows:
        return 0
    notes: list[dict] = []
    params: Optional[dict] = None
    last_id = cursor
    for entry_id, fields in rows:
        last_id = _s(entry_id)
        decoded = {_s(k): _s(v) for k, v in fields.items()}
        raw_params = decoded.get("params")
        if raw_params:
            try:
                parsed = json.loads(raw_params)
                if isinstance(parsed, dict):
                    params = parsed  # last writer wins — the params APPLIED to the newest notes
            except (json.JSONDecodeError, ValueError):
                pass
        raw_note = decoded.get("note")
        if not raw_note:
            continue
        try:
            note = json.loads(raw_note)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(note, dict):
            notes.append(note)
    if notes or last_id != cursor:
        try:
            await merge(
                meeting_id,
                view_id=PROC_VIEW_ID, kind=PROC_VIEW_KIND,
                notes=notes, source_cursor=last_id, params=params,
            )
        except TranscriptWriteRefused:
            # Withdrawal is permanent, not a retryable sink failure. Remove transcript-derived
            # content from both the live stream and the completion re-drain queue.
            await redis_c.delete(proc_stream_key(meeting_id))
            await redis_c.zrem(PROC_PENDING_KEY, str(meeting_id))
            return 0
    return len(notes)


async def _processed_complete(redis_c, sink, meeting_id: int) -> bool:
    """Whether the meeting's processed stream is DRAINED THROUGH its ``view_end`` marker: the
    stream's last entry is the marker AND the persisted view cursor sits exactly on it. A sink
    that can't persist views has nothing to wait for (vacuously complete)."""
    cursor_of = getattr(sink, "processed_view_cursor", None)
    if cursor_of is None:
        return True
    try:
        rows = await redis_c.xrevrange(proc_stream_key(meeting_id), max="+", min="-", count=1)
    except Exception:  # noqa: BLE001 — unreadable stream ⇒ not provably complete
        return False
    if not rows:
        return False  # nothing written (yet) — a just-armed copilot may still deliver
    entry_id, fields = rows[0]
    decoded = {_s(k): _s(v) for k, v in fields.items()}
    if decoded.get("type") != "view_end":
        return False
    return await cursor_of(meeting_id, PROC_VIEW_ID) == _s(entry_id)


async def db_writer_tick(
    redis_c,
    sink,
    *,
    immutability_threshold: Optional[float] = None,
    now: Optional[datetime] = None,
) -> int:
    """ONE db-writer sweep (the loop body ``__main__`` polls): flush every discovered meeting's
    immutable segments to the durable sink, then drain its processed-notes stream. Returns the total
    segments stored. Per-meeting failures are contained — one bad meeting never starves the rest."""
    ids: set[str] = set()
    try:
        members = await redis_c.smembers(ACTIVE_MEETINGS_KEY)
        ids.update(_s(m) for m in (members or []))
    except Exception:  # noqa: BLE001 — the scan below still discovers hashes
        pass
    try:
        async for key in redis_c.scan_iter(match="meeting:*:segments"):
            parts = _s(key).split(":")
            if len(parts) == 3:
                ids.add(parts[1])
    except Exception:  # noqa: BLE001
        pass

    total = 0
    for raw_id in ids:
        try:
            meeting_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        try:
            total += await flush_meeting_segments(
                redis_c, sink, meeting_id,
                immutability_threshold=immutability_threshold, now=now,
            )
            await flush_meeting_processed(redis_c, sink, meeting_id)
        except Exception:  # noqa: BLE001 — isolate per meeting; the next tick retries
            log.exception("db-writer flush failed for meeting %s", raw_id)

    # Finalized-but-incomplete processed streams (ADR 0027): re-drain each parked meeting until its
    # view_end marker is drained-through, or its deadline passes. These meetings have LEFT the sweep
    # above (hash drained, out of active_meetings) — without this pass the final beat's notes would
    # never reach the durable row.
    try:
        pending = await redis_c.zrange(PROC_PENDING_KEY, 0, -1, withscores=True)
    except Exception:  # noqa: BLE001 — the pending pass is additive; never break the main sweep
        pending = []
    now_ts = (now or datetime.now(timezone.utc)).timestamp()
    for member, deadline in pending or []:
        raw_id = _s(member)
        try:
            meeting_id = int(raw_id)
        except (TypeError, ValueError):
            await redis_c.zrem(PROC_PENDING_KEY, member)
            continue
        try:
            await flush_meeting_processed(redis_c, sink, meeting_id)
            if await _processed_complete(redis_c, sink, meeting_id):
                await redis_c.zrem(PROC_PENDING_KEY, raw_id)
            elif now_ts >= float(deadline):
                # P18: the give-up is a reportable state, not silence — everything that DID arrive
                # was flushed above; what never arrived is attributed to the worker, loudly.
                log.warning(
                    "processed view for meeting %s never saw view_end within %ss — "
                    "flushed what arrived, giving up the pending re-drain",
                    raw_id, PROC_PENDING_GRACE_SEC,
                )
                await redis_c.zrem(PROC_PENDING_KEY, raw_id)
        except Exception:  # noqa: BLE001 — isolate per meeting; the next tick retries
            log.exception("pending processed re-drain failed for meeting %s", raw_id)
    return total


async def finalize_meeting(redis_c, sink, meeting_id: int) -> int:
    """The COMPLETION flush — called by the lifecycle callback the moment a meeting reaches a
    terminal status (completed/failed): flush EVERYTHING still in the hash (threshold 0 — the
    mutable tail and trailing drafts included; no more updates are coming) and drain the processed
    notes, so the finished meeting's transcript + processed doc are durable IMMEDIATELY.

    The processed stream is NOT necessarily complete here — the copilot's final beat runs ~10s
    AFTER session_end (ADR 0027). Unless the ``view_end`` marker is already drained-through, the
    meeting PARKS in ``processed_pending``; ``db_writer_tick`` keeps re-draining it until the
    marker (or the bounded deadline). Never processed ⇒ the deadline simply expires the parking."""
    stored = await flush_meeting_segments(redis_c, sink, meeting_id, immutability_threshold=0)
    await flush_meeting_processed(redis_c, sink, meeting_id)
    if not await _processed_complete(redis_c, sink, meeting_id):
        try:
            deadline = datetime.now(timezone.utc).timestamp() + PROC_PENDING_GRACE_SEC
            await redis_c.zadd(PROC_PENDING_KEY, {str(meeting_id): deadline})
        except Exception:  # noqa: BLE001 — parking is the safety net, never fail the finalize
            log.exception("could not park meeting %s for the pending processed re-drain", meeting_id)
    return stored
