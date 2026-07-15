"""Segment ingestion — the deterministic unit behind the collector's redis-stream worker.

The deployed collector runs a background loop (``collector/consumer.py`` XREADGROUP →
``collector/processors.py``) that drains the ``transcription_segments`` stream, persists each
segment, and (with the bot's live path) publishes change-only updates to
``tc:meeting:{id}:mutable`` (``services/redis.md`` — the pubsub the gateway ``/ws`` fans in).

This carve splits that into a pure, explicitly-driven core:

  * ``ingest(store, redis, message)`` — process ONE stream message: parse the JSON ``payload``,
    append each valid segment to the store, publish one ``:mutable`` update per meeting. Returns
    the number of segments persisted.
  * ``consume_segments(store, redis, ...)`` — drain a batch from the bus (``read_segments`` →
    ``ingest`` each → ``ack``). No background loop: the eval calls this explicitly, like the
    runtime scheduler's ``tick()`` — same in ⇒ same out.

The ``:mutable`` payload mirrors the bot's live publisher
(``services/vexa-bot_new/src/adapters/transcript-redis.ts``):
``{type:"transcript", meeting:{id}, speaker, confirmed, pending, ts}``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from .ports import RedisBus, TranscriptStore, TranscriptWriteRefused

# Stream / consumer-group defaults (parent ``collector/config.py``).
STREAM_NAME = "transcription_segments"
CONSUMER_GROUP = "collector_group"
CONSUMER_NAME = "collector-main"


def _mutable_channel(meeting_id: int) -> str:
    """The pubsub channel the gateway ``/ws`` subscribes to (``services/redis.md``)."""
    return f"tc:meeting:{meeting_id}:mutable"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_segment(raw: dict) -> Optional[dict]:
    """Validate + normalize one stream segment into the store's segment shape, or ``None`` when
    it is malformed (missing start/end/segment_id, or a zero-length COMPLETED segment) — the parent's
    ``process_stream_message`` segment filtering."""
    if not isinstance(raw, dict):
        return None
    if raw.get("start") is None or raw.get("end") is None:
        return None
    try:
        start = float(raw["start"])
        end = float(raw["end"])
    except (TypeError, ValueError):
        return None
    completed = bool(raw.get("completed", False))
    source = raw.get("source")
    # Fix inverted timestamps.
    if end < start:
        start, end = end, start
    # Drop ~zero-length COMPLETED segments (garbage finals). A pending DRAFT (completed=False) legitimately
    # has no end yet — `start == end` is its in-progress placeholder — so it MUST pass: it is the live
    # "being spoken" text the dashboard renders as a pending draft (filtering it left transcripts
    # confirmed-only, with no live in-progress text). A `chat` segment (transcript.v1 Source) is a
    # legitimate POINT-IN-TIME event — start == end by contract — so it passes too.
    if completed and end - start < 1e-3 and source != "chat":
        return None
    segment_id = raw.get("segment_id")
    if not segment_id:
        return None
    return {
        "segment_id": segment_id,
        "start": start,
        "end": end,
        "text": raw.get("text") or "",
        "language": raw.get("language"),
        "speaker": raw.get("speaker"),
        "completed": completed,
        "source": source,
        "absolute_start_time": raw.get("absolute_start_time"),
        "absolute_end_time": raw.get("absolute_end_time"),
        "updated_at": _now_iso(),
    }


def _transcript_stream(meeting_id: int) -> str:
    """The per-meeting transcript STREAM the collector owns as SINGLE writer (P23) — read by the copilot
    worker (``serve_meeting``) and the terminal live SSE.

    P0 (cross-tenant leak fix): keyed by the meetings-domain numeric ROW id, NOT the native meeting id.
    The native id is NOT unique — it collides across DIFFERENT users (a shared ``tc:meeting:{native}``
    leaked one user's transcript to another) AND across ONE user's repeated meeting rows. The row id is
    unique per (user, platform, native, run), so ``tc:meeting:{meeting_id}`` isolates every meeting. The
    native id still rides in the wire payload for display (``_to_native_wire``)."""
    return f"tc:meeting:{meeting_id}"


def _to_native_wire(native: str, seg: dict) -> dict:
    """Shape ONE persisted segment into the per-segment wire the native feed carries — a
    ``transcription`` envelope with a single segment, exactly the shape the worker + terminal SSE consume."""
    raw = float(seg.get("start") or 0.0)
    end = float(seg.get("end") or raw)
    return {
        "type": "transcription", "session_uid": native, "meeting_id": native,
        "segments": [{
            "speaker": seg.get("speaker") or "Speaker", "text": (seg.get("text") or "").strip(),
            "start": round(raw, 1), "end": round(max(raw, end), 1),
            "abs_start_ms": round(raw * 1000),
            "absolute_start_time": seg.get("absolute_start_time"),
            "completed": bool(seg.get("completed")), "language": seg.get("language") or "en",
            "segment_id": seg.get("segment_id"),
        }],
    }


async def _resolve_native(store: TranscriptStore, meeting_id: int) -> Optional["tuple[str, str]"]:
    """numeric meeting_id → (native_id, platform), via the store (it owns the meetings table). Best-effort."""
    fn = getattr(store, "native_for", None)
    if fn is None:
        return None
    try:
        return await fn(meeting_id)
    except Exception:  # noqa: BLE001 — resolution is best-effort; a miss leaves the feed unwritten
        return None


def _log_publish_failure(meeting_id: int, e: Exception) -> None:
    try:
        from ..obs import log_event

        log_event("segment_publish_failed", audience="system", level="warning",
                  span="collector.ingest", fields={"meeting_id": meeting_id, "error": str(e)})
    except Exception:  # noqa: BLE001
        pass


async def ingest(store: TranscriptStore, redis: RedisBus, message: dict) -> int:
    """Process ONE ``transcription_segments`` stream message.

    ``message`` is the decoded stream fields (``{"payload": "<json>"}``). Parses the payload,
    appends each valid segment to ``store``, then publishes one ``:mutable`` update per meeting
    so the gateway ``/ws`` fan-in forwards it live. Returns the count of persisted segments.

    Trusted internal stream (the bot is the producer): ``meeting_id`` comes from the payload.
    """
    payload_raw = message.get("payload")
    if not payload_raw:
        return 0
    try:
        data = json.loads(payload_raw) if isinstance(payload_raw, (str, bytes)) else payload_raw
    except (json.JSONDecodeError, ValueError):
        return 0

    msg_type = data.get("type", "transcription")
    if msg_type == "session_end":
        # P23/P0: the collector owns tc:meeting:{meeting_id} (the numeric ROW id, cross-tenant safe) —
        # emit the session_end marker the copilot worker + terminal SSE read off it (the agent relay used
        # to do this; the agent now only consumes). Key the marker by the numeric row id (never the
        # native id, which collides across users/rows). The wire ``uid`` stays the native/session id for
        # display. When no numeric id is present (an older bot that only sent a native/uid) there is no
        # row to key on → skip; the copilot reaps on idle anyway.
        mid_raw = data.get("meeting_id")
        try:
            meeting_id = int(mid_raw) if mid_raw is not None else None
        except (TypeError, ValueError):
            meeting_id = None
        if meeting_id is not None:
            uid = data.get("native_meeting_id") or data.get("uid") or data.get("session_uid") or str(meeting_id)
            try:
                await redis.xadd(_transcript_stream(meeting_id), {"type": "session_end", "uid": uid})
            except Exception as e:  # noqa: BLE001 — best-effort; never abort the batch
                _log_publish_failure(meeting_id, e)
        return 0
    if msg_type not in ("transcription", "transcript"):
        # session_start / speaker events are out of scope for this segment unit.
        return 0

    try:
        meeting_id = int(data.get("meeting_id"))
    except (TypeError, ValueError):
        return 0

    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list):
        return 0

    persisted: list[dict] = []
    for raw in raw_segments:
        seg = _coerce_segment(raw)
        if seg is None:
            continue
        persisted.append(seg)

    if persisted:
        # Publish a change-only mutable update (bot's live-path shape). ``confirmed`` carries the
        # completed segments, ``pending`` the drafts — the dashboard renders both.
        confirmed = [s for s in persisted if s["completed"]]
        pending = [s for s in persisted if not s["completed"]]
        speaker = persisted[0].get("speaker") or ""
        # Stamp the NATIVE meeting id (and platform) so the agent-api live relay can re-key
        # numeric→native WITHOUT a user-scoped /meetings lookup (which fails for any meeting not owned
        # by the relay's bot key → segments never reach the terminal's native channel). The collector
        # owns the mapping (it persists by meeting_id); best-effort — a miss leaves it numeric-only.
        # PREFER the native id the producer STAMPED on the segment (P23: one writer, no re-derivation —
        # and no DB lookup that can miss, which left tc:meeting:{native} empty and the copilot starved).
        # Fall back to the store mapping only for older bots that don't stamp it.
        native_id = data.get("native_meeting_id")
        platform_native = data.get("platform")
        if not native_id:
            pair = await _resolve_native(store, meeting_id)
            native_id, platform_native = pair if pair else (None, None)
        try:
            # One shared lease spans the complete message: the consent state is checked once, all
            # segments land in one Redis transaction, and every resulting live write happens before
            # withdrawal can acquire the exclusive barrier. A refused lease publishes nothing.
            async with store.transcript_write_lease(meeting_id) as writer:
                await writer.append_segments(persisted)

                # FAULT-ISOLATED (P18): the segments are already persisted. A transient live-publish
                # blip must not abort the source batch before it is acknowledged.
                try:
                    await redis.publish(
                        _mutable_channel(meeting_id),
                        json.dumps({
                            "type": "transcript",
                            "meeting": {
                                "id": meeting_id,
                                "native_id": native_id,
                                "platform": platform_native,
                            },
                            "speaker": speaker,
                            "confirmed": confirmed,
                            "pending": pending,
                            "ts": _now_iso(),
                        }),
                    )
                except Exception as e:  # noqa: BLE001 — persistence already succeeded
                    _log_publish_failure(meeting_id, e)

                # P23/P0: the collector is the SINGLE writer of the numeric-row transcript feed.
                stream = _transcript_stream(meeting_id)
                wire_uid = native_id or str(meeting_id)
                for seg in persisted:
                    if not (seg.get("text") or "").strip():
                        continue
                    try:
                        await redis.xadd(stream, _to_native_wire(wire_uid, seg))
                    except Exception as e:  # noqa: BLE001 — persistence already succeeded
                        _log_publish_failure(meeting_id, e)
        except TranscriptWriteRefused:
            return 0

    return len(persisted)


async def consume_segments(
    store: TranscriptStore,
    redis: RedisBus,
    *,
    stream: str = STREAM_NAME,
    group: str = CONSUMER_GROUP,
    consumer: str = CONSUMER_NAME,
    count: int = 10,
) -> int:
    """Drain ONE batch from the bus: read → ingest each → ack. Returns the total segments
    persisted across the batch. No background loop — the caller drives it (eval ``tick``)."""
    batch = await redis.read_segments(group=group, consumer=consumer, stream=stream, count=count)
    total = 0
    acked: list[str] = []
    for message_id, fields in batch:
        total += await ingest(store, redis, fields)
        acked.append(message_id)
    if acked:
        await redis.ack(group=group, stream=stream, message_ids=acked)
    return total
