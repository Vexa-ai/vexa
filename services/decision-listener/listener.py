"""
Core listener: subscribes to Redis pub/sub pattern tc:meeting:*:mutable,
maintains a per-meeting segment buffer, and triggers LLM analysis with
debounce + windowing + offset.
"""
import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from typing import Dict, Deque, Any

import redis.asyncio as aioredis

from config import (
    REDIS_HOST, REDIS_PORT, REDIS_PASSWORD,
    PUBSUB_CHANNEL_PATTERN,
    WINDOW_SEGMENTS, OFFSET_SEGMENTS, DEBOUNCE_MS,
    DECISIONS_KEY_PREFIX, DECISIONS_TTL,
)
from llm import analyze_window, is_duplicate_llm

logger = logging.getLogger(__name__)


def _word_similarity(a: str, b: str) -> float:
    """Jaccard similarity on significant words (>3 chars). Returns 0–1."""
    import re
    def tokenize(s):
        return set(w for w in re.sub(r"[^a-z0-9\s]", "", s).split() if len(w) > 3)
    wa, wb = tokenize(a), tokenize(b)
    if not wa and not wb:
        return 1.0
    intersection = len(wa & wb)
    union = len(wa | wb)
    return intersection / union if union else 0.0


# Per-meeting segment buffer: meeting_id -> deque of segment dicts (sorted by start time)
_segment_buffers: Dict[str, Deque[dict]] = defaultdict(lambda: deque(maxlen=WINDOW_SEGMENTS + OFFSET_SEGMENTS + 10))

# Per-meeting last LLM call timestamp (monotonic ms)
_last_llm_call: Dict[str, float] = {}

# Redis client (set by start_listener)
_redis: aioredis.Redis = None

# SSE broadcast queues: meeting_id -> list of asyncio.Queue
_sse_queues: Dict[str, list] = defaultdict(list)


def register_sse_queue(meeting_id: str, q: asyncio.Queue):
    _sse_queues[meeting_id].append(q)


def unregister_sse_queue(meeting_id: str, q: asyncio.Queue):
    try:
        _sse_queues[meeting_id].remove(q)
    except ValueError:
        pass


async def _broadcast(meeting_id: str, item: dict):
    """Push detected item to all SSE subscribers for this meeting."""
    for q in list(_sse_queues.get(meeting_id, [])):
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            logger.warning(f"SSE queue full for meeting {meeting_id}, dropping item")


def _extract_meeting_id(channel: str) -> str:
    """tc:meeting:42:mutable -> '42'"""
    parts = channel.split(":")
    # channel format: tc:meeting:{id}:mutable
    if len(parts) == 4:
        return parts[2]
    return channel


def _merge_segments(buffer: Deque[dict], new_segments: list):
    """
    Upsert new segments into the buffer keyed by start time.
    Segments are stored sorted by start float.
    """
    existing = {seg["start"]: seg for seg in buffer}
    for seg in new_segments:
        start = seg.get("start")
        if start is None:
            continue
        existing[float(start)] = seg
    # Rebuild deque sorted by start time
    buffer.clear()
    for seg in sorted(existing.values(), key=lambda s: float(s["start"])):
        buffer.append(seg)


def _get_window(meeting_id: str) -> list:
    """
    Return the window of segments to pass to LLM:
    - Take the last WINDOW_SEGMENTS + OFFSET_SEGMENTS from the buffer
    - Drop the last OFFSET_SEGMENTS (in-flight / unfinished)
    """
    buf = _segment_buffers[meeting_id]
    all_segs = list(buf)
    # Drop last OFFSET_SEGMENTS
    if OFFSET_SEGMENTS > 0:
        all_segs = all_segs[:-OFFSET_SEGMENTS] if len(all_segs) > OFFSET_SEGMENTS else []
    # Take last WINDOW_SEGMENTS
    return all_segs[-WINDOW_SEGMENTS:] if len(all_segs) > WINDOW_SEGMENTS else all_segs


def _is_debounced(meeting_id: str) -> bool:
    last = _last_llm_call.get(meeting_id, 0.0)
    now = time.monotonic() * 1000
    return (now - last) < DEBOUNCE_MS


async def _store_decision(redis_c: aioredis.Redis, meeting_id: str, item: dict):
    """Append decision to Redis list and refresh TTL."""
    key = DECISIONS_KEY_PREFIX.format(meeting_id=meeting_id)
    payload = json.dumps(item)
    try:
        await redis_c.rpush(key, payload)
        await redis_c.expire(key, DECISIONS_TTL)
        logger.info(f"[{meeting_id}] Stored {item['type']}: {item['summary'][:80]}")
    except Exception as e:
        logger.error(f"[{meeting_id}] Failed to store decision: {e}")


async def _handle_meeting_update(redis_c: aioredis.Redis, meeting_id: str, segments: list):
    """Called on every pub/sub message for a meeting."""
    _merge_segments(_segment_buffers[meeting_id], segments)

    if _is_debounced(meeting_id):
        logger.debug(f"[{meeting_id}] Debounced, skipping LLM call")
        return

    window = _get_window(meeting_id)
    if not window:
        logger.debug(f"[{meeting_id}] Empty window, skipping LLM call")
        return

    _last_llm_call[meeting_id] = time.monotonic() * 1000

    item = await analyze_window(window)
    if item is None:
        return

    # Dedup: two-pass check against recently stored items of the same type
    key = DECISIONS_KEY_PREFIX.format(meeting_id=meeting_id)
    try:
        recent_raw = await redis_c.lrange(key, -10, -1)
        same_type_summaries = [
            json.loads(r).get("summary", "")
            for r in recent_raw
            if json.loads(r).get("type") == item["type"]
        ]

        if same_type_summaries:
            incoming = item["summary"].strip().lower()

            # Pass 1: fast Jaccard word-overlap
            jaccard_hit = any(
                _word_similarity(s.strip().lower(), incoming) >= 0.65
                for s in same_type_summaries
            )

            if jaccard_hit:
                logger.debug(f"[{meeting_id}] Jaccard duplicate, skipping: {item['summary'][:60]}")
                return

            # Pass 2: LLM semantic check (only runs if Jaccard didn't catch it)
            if await is_duplicate_llm(item["summary"], same_type_summaries):
                logger.debug(f"[{meeting_id}] LLM duplicate, skipping: {item['summary'][:60]}")
                return
    except Exception as e:
        logger.warning(f"[{meeting_id}] Dedup check failed, allowing item through: {e}")

    await _store_decision(redis_c, meeting_id, item)
    await _broadcast(meeting_id, item)


async def start_listener():
    """Main pub/sub loop. Runs forever."""
    global _redis
    _redis = aioredis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )
    # Separate connection for pub/sub (pubsub connections are stateful)
    pubsub_redis = aioredis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )

    logger.info(f"Starting pub/sub listener on pattern: {PUBSUB_CHANNEL_PATTERN}")
    logger.info(f"Window={WINDOW_SEGMENTS} segments, Offset={OFFSET_SEGMENTS}, Debounce={DEBOUNCE_MS}ms")

    while True:
        try:
            pubsub = pubsub_redis.pubsub()
            await pubsub.psubscribe(PUBSUB_CHANNEL_PATTERN)
            logger.info("Subscribed to Redis pub/sub")

            async for message in pubsub.listen():
                if message["type"] != "pmessage":
                    continue
                channel = message.get("channel", "")
                meeting_id = _extract_meeting_id(channel)
                try:
                    payload = json.loads(message["data"])
                    segments = payload.get("payload", {}).get("segments", [])
                    if segments:
                        asyncio.create_task(
                            _handle_meeting_update(_redis, meeting_id, segments)
                        )
                except Exception as e:
                    logger.error(f"Failed to parse pub/sub message on {channel}: {e}")

        except Exception as e:
            logger.error(f"Pub/sub loop error: {e}. Reconnecting in 5s...", exc_info=True)
            await asyncio.sleep(5)


async def get_redis() -> aioredis.Redis:
    """Return the shared Redis client (for use by HTTP handlers)."""
    return _redis
