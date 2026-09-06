"""The background **db-writer** — flush live Redis segments to the durable store.

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
    hash drains empty its meeting id leaves the ``active_meetings`` set.
  * **discovery** — the ``active_meetings`` set (maintained ATOMICALLY with every hash write by
    ``append_segment`` — one transactional sadd+hset+expire) is authoritative in steady state, so a
    tick sweeps the SET alone. The self-healing ``meeting:*:segments`` key scan — for a hash written
    before the set existed (mid-upgrade) or a set/hash divergence — runs ONLY on a ``reconcile`` tick
    (startup + every ``DB_WRITER_RECONCILE_INTERVAL_S``), never per tick: the O(keyspace) scan on the
    10s hot path saturated Redis and starved the /health probe, restarting healthy pods (#893).

Additions over the parent:

  * ``finalize_meeting(...)`` — the completion hook: flush EVERYTHING left (threshold 0, mutable
    tail included) the moment the lifecycle FSM lands on a terminal status, so a completed meeting's
    transcript is durable immediately instead of eventually.
There used to be a second drain beside it — ``flush_meeting_processed(...)``, which pulled the
copilot's cleaned-notes stream ``proc:meeting:{meeting_id}`` into the meeting row's
``data['processed']`` JSONB, plus the ``processed_pending`` parking that waited for that
producer's ``view_end`` marker after a meeting finished. PRD decision 34 removed the producer:
the product runs no model calls of its own beside the agent, so nothing writes that stream and
there is no second body of a transcript to keep durable. Segments are the whole job.

Everything here talks to redis through plain client calls (hgetall/hdel/smembers/scan/xrange) that
both ``redis.asyncio`` and ``fakeredis.aioredis`` satisfy, and to the durable store through two
a getattr-guarded sink method (``upsert_segments``) implemented by BOTH
``SqlAlchemyTranscriptStore`` (prod) and ``InMemoryTranscriptStore`` (tests) — so the whole writer
is unit-tested offline, no docker.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("meeting_api.collector.db_writer")

ACTIVE_MEETINGS_KEY = "active_meetings"
IMMUTABILITY_THRESHOLD = float(os.environ.get("IMMUTABILITY_THRESHOLD", "30"))

# ── the hold is a per-LANE guarantee, not a global one (M21, 2026-08-13) ────────────────────────
# WHAT THE 30 s IS FOR. A lane that REFINES A ROW IN PLACE — the gmeet lane — can publish a draft,
# then republish the confirmed text under a DIFFERENT segment id (``speaker-streams.ts`` rotates
# ``windowStartMs`` at :350/:364/:739) and withdraw the old draft by emitting EMPTY TEXT under the
# old id. Empty text is never stored and never deletes an already-flushed row, so a draft that
# reached postgres before its id rotated is orphaned there for good. The hold is what keeps those
# drafts from ever becoming durable: they are superseded inside the window and dropped from the
# hash. That guarantee is real and is NOT touched here.
#
# WHY THE TEAMS CSRC LANE DOES NOT NEED IT. Its drafts use disjoint ids and are actively
# RETRACTED — ``transcript_retract`` → ``delete_segments`` deletes the durable row as well as the
# hash field. A confirmed row is never withdrawn by empty text. The lane is identified without
# widening the legacy Zoom/Jitsi blast radius: ``source: 'merged'`` plus the stable
# ``speaker_key: 'csrc:<id>'`` emitted only by the Teams adapter. For a confirmed Teams row the
# hold buys nothing and costs the customer the whole 30 s: measured on prod
# meeting 26088, time-to-durable was 39.4 s median of which 36.2 s is this threshold plus the
# writer's poll phase, against 2.0 s for the model to produce the text.
#
# Pendings keep the full hold in every lane, so a draft can never become a durable row that reads
# back as final.
IMMUTABILITY_THRESHOLD_TEAMS_CSRC_CONFIRMED = float(
    os.environ.get("IMMUTABILITY_THRESHOLD_TEAMS_CSRC_CONFIRMED", "0")
)


def threshold_for(seg: dict, default_threshold: float) -> float:
    """The hold this ONE segment must serve before it may become durable.

    ``default_threshold`` is the caller's threshold (the global one, or 0 at finalize) and is
    returned unchanged for every other lane. Only a CONFIRMED Teams CSRC segment — whose drafts
    are explicitly retracted — is allowed the shorter hold, and never a longer one than the caller
    asked for.
    """
    try:
        is_teams_csrc = (
            seg.get("source") == "merged"
            and str(seg.get("speaker_key", "")).startswith("csrc:")
        )
        if seg.get("completed") is True and is_teams_csrc:
            return min(default_threshold, IMMUTABILITY_THRESHOLD_TEAMS_CSRC_CONFIRMED)
    except AttributeError:  # not a mapping — the caller's threshold stands
        pass
    return default_threshold

# #527 C2: how long an ACKED transcription_segments entry is retained before it is eligible to be
# trimmed. Retention NEVER trims an entry the collector group has not read (the 2026-04-26 data loss,
# where a MAXLEN trim aged out entries a hung consumer never got to) — a behind/hung group keeps its
# full backlog, and the hang is surfaced by /health (C1) rather than papered over by trimming it.
STREAM_RETENTION_S = float(os.environ.get("STREAM_RETENTION_S", "3600"))


def _parse_stream_id(v) -> "Optional[tuple[int, int]]":
    """A redis stream id ``"<ms>-<seq>"`` → ``(ms, seq)`` for ordering; None if unparseable."""
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        v = v.decode()
    try:
        ms, seq = str(v).split("-")
        return (int(ms), int(seq))
    except Exception:
        return None


def _pending_min_id(pending) -> "Optional[tuple[int, int]]":
    """Oldest DELIVERED-but-un-acked id for the group, from an XPENDING summary, as (ms, seq)."""
    if not isinstance(pending, dict):
        return None
    return _parse_stream_id(pending.get("min"))


async def _group_last_delivered_id(redis_c, stream: str, group: str) -> "Optional[tuple[int, int]]":
    """The group's last-delivered-id — entries AFTER it are UNDELIVERED (the lag) and must survive."""
    for g in (await redis_c.xinfo_groups(stream)) or []:
        name = g.get("name") if isinstance(g, dict) else None
        if isinstance(name, (bytes, bytearray)):
            name = name.decode()
        if name == group:
            return _parse_stream_id(g.get("last-delivered-id"))
    return None


async def _trim_segments_stream(redis_c, retention_s: float, now_ms: int) -> None:
    """Trim ``transcription_segments`` to entries newer than the retention floor — but NEVER past an
    entry the collector group has not fully consumed. "Unread" is two things: DELIVERED-but-un-acked
    (pending) and UNDELIVERED (past the group's last-delivered-id, i.e. the lag). The trim floor is
    the OLDER of {retention time, oldest pending, last-delivered-id}, so an acked-and-aged entry is
    trimmed while any un-consumed entry survives (the 2026-04-26 data-loss guard)."""
    from .ingest import CONSUMER_GROUP, STREAM_NAME

    floor = (max(0, now_ms - int(retention_s * 1000)), 0)  # time-based minid we would keep from
    try:
        oldest_pending = _pending_min_id(await redis_c.xpending(STREAM_NAME, CONSUMER_GROUP))
        if oldest_pending is not None:
            floor = min(floor, oldest_pending)
        last_delivered = await _group_last_delivered_id(redis_c, STREAM_NAME, CONSUMER_GROUP)
        if last_delivered is not None:
            # keep everything STRICTLY AFTER last-delivered (the UNDELIVERED lag); the last-delivered
            # entry itself is consumed, so the exclusive floor is (ms, seq+1).
            floor = min(floor, (last_delivered[0], last_delivered[1] + 1))
    except Exception:
        return  # no group/stream yet, or XINFO/XPENDING unsupported — do not risk a blind trim
    minid = f"{floor[0]}-{floor[1]}"
    try:
        await redis_c.xtrim(STREAM_NAME, minid=minid, approximate=True)
    except TypeError:
        await redis_c.xtrim(STREAM_NAME, minid=minid)  # older redis-py signature
    except Exception:
        pass


def segments_hash_key(meeting_id) -> str:
    """The live Redis hash of in-flight segments (``ingest`` writes it; the read path merges it)."""
    return f"meeting:{meeting_id}:segments"


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

    batch: list[dict] = []
    done_fields: list = []  # flushed OR discarded — removed only after a confirmed write
    for field, value in raw.items():
        try:
            seg = json.loads(_s(value))
        except (json.JSONDecodeError, TypeError, ValueError):
            done_fields.append(field)  # unparseable — drop it (parent behavior)
            continue
        updated_at = _parse_updated_at(seg.get("updated_at"))
        seg_threshold = threshold_for(seg, threshold)
        if seg_threshold > 0 and updated_at is not None and updated_at >= (now - timedelta(seconds=seg_threshold)):
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


async def db_writer_tick(
    redis_c,
    sink,
    *,
    immutability_threshold: Optional[float] = None,
    now: Optional[datetime] = None,
    reconcile: bool = False,
) -> int:
    """ONE db-writer sweep (the loop body ``__main__`` polls): flush every discovered meeting's
    immutable segments to the durable sink. Returns the total segments stored. Per-meeting failures are contained — one bad meeting never starves the rest.

    Discovery is the ``active_meetings`` set (authoritative in steady state — ``append_segment`` SADDs
    the meeting in the SAME transaction that writes its hash). ``reconcile=True`` ADDITIONALLY runs the
    O(keyspace) ``meeting:*:segments`` scan to self-heal a set/hash divergence or a pre-set (mid-upgrade)
    hash; it is OFF the per-tick hot path (``reconcile`` defaults False) because that scan saturated
    Redis and starved the /health probe (#893) — the loop runs it only on startup + every N minutes."""
    ids: set[str] = set()
    try:
        members = await redis_c.smembers(ACTIVE_MEETINGS_KEY)
        ids.update(_s(m) for m in (members or []))
    except Exception:  # noqa: BLE001 — a due reconcile scan (below) still discovers hashes
        pass
    if reconcile:
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
        except Exception:  # noqa: BLE001 — isolate per meeting; the next tick retries
            log.exception("db-writer flush failed for meeting %s", raw_id)

    # #527 C2: bound the ingest stream in steady state (trims only acked entries past the retention
    # window; never an unread one). Isolated — a trim failure never blocks the durable flush above.
    try:
        now_ms = int((now.timestamp() if now is not None else datetime.now(timezone.utc).timestamp()) * 1000)
        await _trim_segments_stream(redis_c, STREAM_RETENTION_S, now_ms)
    except Exception:  # noqa: BLE001
        log.exception("segment stream retention trim failed")
    return total


async def finalize_meeting(redis_c, sink, meeting_id: int) -> int:
    """The COMPLETION flush — called by the lifecycle callback the moment a meeting reaches a
    terminal status (completed/failed): flush EVERYTHING still in the hash (threshold 0 — the
    mutable tail and trailing drafts included; no more updates are coming), so the finished
    meeting's transcript is durable IMMEDIATELY.

    It used to do a second thing: drain the copilot's processed notes and, when their ``view_end``
    marker had not arrived, PARK the meeting in ``processed_pending`` for a bounded re-drain — the
    final beat ran ~10s after session_end. PRD decision 34 removed that producer, so there is
    nothing left to wait for and the flush is complete when the segments are."""
    return await flush_meeting_segments(redis_c, sink, meeting_id, immutability_threshold=0)
