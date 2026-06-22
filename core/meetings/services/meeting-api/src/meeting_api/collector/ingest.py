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

from .ports import RedisBus, TranscriptStore

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
    # Fix inverted timestamps.
    if end < start:
        start, end = end, start
    # Drop ~zero-length COMPLETED segments (garbage finals). A pending DRAFT (completed=False) legitimately
    # has no end yet — `start == end` is its in-progress placeholder — so it MUST pass: it is the live
    # "being spoken" text the dashboard renders as a pending draft (filtering it left transcripts
    # confirmed-only, with no live in-progress text).
    if completed and end - start < 1e-3:
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
        "absolute_start_time": raw.get("absolute_start_time"),
        "absolute_end_time": raw.get("absolute_end_time"),
        "updated_at": _now_iso(),
    }


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
    if msg_type not in ("transcription", "transcript"):
        # session_start / session_end / speaker events are out of scope for this segment unit.
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
        await store.append_segment(meeting_id, seg)
        persisted.append(seg)

    if persisted:
        # Publish a change-only mutable update (bot's live-path shape). ``confirmed`` carries the
        # completed segments, ``pending`` the drafts — the dashboard renders both.
        confirmed = [s for s in persisted if s["completed"]]
        pending = [s for s in persisted if not s["completed"]]
        speaker = persisted[0].get("speaker") or ""
        # FAULT-ISOLATED (P18): the segments are already persisted (durable). A transient redis blip on
        # the live :mutable publish must NOT propagate out of ingest() — that would abort the batch
        # BEFORE consume_segments acks it, leaving the whole batch unacked + re-raised. Surface it and
        # return the persisted count (the dashboard recovers the segment on its next REST refresh).
        try:
            await redis.publish(
                _mutable_channel(meeting_id),
                json.dumps({
                    "type": "transcript",
                    "meeting": {"id": meeting_id},
                    "speaker": speaker,
                    "confirmed": confirmed,
                    "pending": pending,
                    "ts": _now_iso(),
                }),
            )
        except Exception as e:  # noqa: BLE001 — publish is best-effort; persistence already succeeded
            try:
                from ..obs import log_event

                log_event("segment_publish_failed", audience="system", level="warning",
                          span="collector.ingest",
                          fields={"meeting_id": meeting_id, "error": str(e)})
            except Exception:
                pass

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
