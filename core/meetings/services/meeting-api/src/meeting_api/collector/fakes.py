"""In-process fakes satisfying the collector's ports — for the ingestion eval AND the gateway
conformance harness (both drive the SAME shipped ``create_app`` / ``ingest`` with these).

  * ``InMemoryTranscriptStore`` — a dict-backed ``TranscriptStore``. ``seed_meeting`` plants a
    meeting (mirrors a ``meetings`` row + its ``data`` JSONB); ``append_segment`` accumulates
    segments by ``segment_id`` (last-write-wins, the parent's Redis-hash identity). ``get_transcript``
    emits an api.v1 ``TranscriptionResponse``-shaped dict; ``list_meetings`` emits
    ``MeetingResponse``-shaped dicts.
  * ``FakeRedisBus`` — a fakeredis-backed ``RedisBus`` wrapper: ``xadd`` to enqueue a stream
    message, ``read_segments`` drains via XREADGROUP, ``publish`` records (and forwards to
    fakeredis pubsub) the ``:mutable`` updates so a test can assert the gateway-facing payload.

These carry NO production logic — they only stand in for Postgres + Redis so the eval/conformance
run OFFLINE (no docker), exactly like the gateway lane's port-fakes.
"""
from __future__ import annotations

import json
from typing import Optional


def _segment_to_api(seg: dict) -> dict:
    """A stored segment → api.v1 ``TranscriptionSegment`` (start/end/text/language required)."""
    out = {
        "start": float(seg.get("start", 0.0)),
        "end": float(seg.get("end", 0.0)),
        "text": seg.get("text", ""),
        "language": seg.get("language"),
    }
    for k in ("speaker", "completed", "segment_id", "absolute_start_time", "absolute_end_time"):
        if seg.get(k) is not None:
            out[k] = seg[k]
    return out


class InMemoryTranscriptStore:
    """A dict-backed ``TranscriptStore``. Owner-scoped by ``user_id`` (the authorization
    boundary). Keyed internally by the synthetic ``meeting_id``."""

    def __init__(self):
        # meeting_id -> {user_id, platform, native_meeting_id, status, start_time, end_time,
        #                data, segments: {segment_id: seg}}
        self._meetings: dict[int, dict] = {}
        self._next_id = 1

    def seed_meeting(
        self,
        *,
        user_id: int,
        platform: str,
        native_meeting_id: str,
        status: str = "active",
        meeting_id: Optional[int] = None,
        start_time: Optional[str] = "2026-06-20T09:00:00Z",
        end_time: Optional[str] = None,
        bot_container_id: Optional[str] = None,
        data: Optional[dict] = None,
        created_at: str = "2026-06-20T08:59:00Z",
        updated_at: str = "2026-06-20T09:00:05Z",
        constructed_meeting_url: Optional[str] = None,
        segments: Optional[list[dict]] = None,
    ) -> int:
        mid = meeting_id if meeting_id is not None else self._next_id
        self._next_id = max(self._next_id, mid + 1)
        self._meetings[mid] = {
            "user_id": user_id,
            "platform": platform,
            "native_meeting_id": native_meeting_id,
            "status": status,
            "start_time": start_time,
            "end_time": end_time,
            "bot_container_id": bot_container_id,
            "constructed_meeting_url": constructed_meeting_url,
            "data": dict(data or {}),
            "created_at": created_at,
            "updated_at": updated_at,
            "segments": {s["segment_id"]: s for s in (segments or [])},
        }
        return mid

    def _find(self, user_id, platform, native_meeting_id) -> Optional[int]:
        for mid, m in self._meetings.items():
            if (
                m["user_id"] == user_id
                and m["platform"] == platform
                and m["native_meeting_id"] == native_meeting_id
            ):
                return mid
        return None

    async def get_transcript(self, user_id, platform, native_meeting_id) -> Optional[dict]:
        mid = self._find(user_id, platform, native_meeting_id)
        if mid is None:
            return None
        m = self._meetings[mid]
        segments = sorted(m["segments"].values(), key=lambda s: float(s.get("start", 0.0)))
        return {
            "id": mid,
            "platform": m["platform"],
            "native_meeting_id": m["native_meeting_id"],
            "constructed_meeting_url": m.get("constructed_meeting_url"),
            "status": m["status"],
            "start_time": m["start_time"],
            "end_time": m["end_time"],
            "recordings": m["data"].get("recordings", []),
            "notes": m["data"].get("notes"),
            "data": m["data"],
            "segments": [_segment_to_api(s) for s in segments],
        }

    async def list_meetings(self, user_id, *, status=None, platform=None, limit=None, offset=None):
        rows = [
            (mid, m) for mid, m in self._meetings.items()
            if m["user_id"] == user_id
            and (status is None or m["status"] == status)
            and (platform is None or m["platform"] == platform)
        ]
        # newest first (by created_at desc, then id desc as a stable tiebreak)
        rows.sort(key=lambda kv: (kv[1]["created_at"], kv[0]), reverse=True)
        if offset:
            rows = rows[offset:]
        if limit:
            rows = rows[:limit]
        return [
            {
                "id": mid,
                "user_id": m["user_id"],
                "platform": m["platform"],
                "native_meeting_id": m["native_meeting_id"],
                "constructed_meeting_url": m.get("constructed_meeting_url"),
                "status": m["status"],
                "bot_container_id": m.get("bot_container_id"),
                "start_time": m["start_time"],
                "end_time": m["end_time"],
                "data": m["data"],
                "created_at": m["created_at"],
                "updated_at": m["updated_at"],
            }
            for mid, m in rows
        ]

    async def authorize_subscribe(self, user_id, platform, native_meeting_id) -> Optional[int]:
        return self._find(user_id, platform, native_meeting_id)

    async def connect_doc(self, user_id, platform, native_meeting_id, doc):
        from .adapters import _upsert_doc

        mid = self._find(user_id, platform, native_meeting_id)
        if mid is None:
            return None
        data = self._meetings[mid]["data"]
        docs = _upsert_doc(list(data.get("docs", [])), doc)
        data["docs"] = docs
        return docs

    async def disconnect_doc(self, user_id, platform, native_meeting_id, path):
        from .adapters import _remove_doc

        mid = self._find(user_id, platform, native_meeting_id)
        if mid is None:
            return None
        data = self._meetings[mid]["data"]
        docs = _remove_doc(list(data.get("docs", [])), path)
        data["docs"] = docs
        return docs

    async def append_segment(self, meeting_id, segment) -> None:
        m = self._meetings.get(meeting_id)
        if m is None:
            # An ingested segment for an unknown meeting — seed a placeholder so the segment is
            # not lost (the parent persists by meeting_id regardless; the meeting row exists by
            # the time segments flow). Keep it owner-less until seeded.
            m = self._meetings.setdefault(meeting_id, {
                "user_id": None, "platform": None, "native_meeting_id": None,
                "status": "active", "start_time": None, "end_time": None,
                "bot_container_id": None, "constructed_meeting_url": None,
                "data": {}, "created_at": "", "updated_at": "", "segments": {},
            })
        m["segments"][segment["segment_id"]] = segment


class FakeRedisBus:
    """A ``RedisBus`` over fakeredis. Wraps a fakeredis async client for stream read/ack/publish,
    plus ``xadd`` (test-only) to enqueue stream messages and a ``published`` log of ``:mutable``
    payloads for assertions."""

    def __init__(self, client):
        self._client = client
        self.published: list[tuple[str, str]] = []  # (channel, raw_json)

    async def xadd(self, stream: str, payload: dict) -> str:
        """Enqueue one stream message (the bot's XADD). ``payload`` is the inner JSON; the stream
        field is ``payload`` (the parent's stream field name)."""
        return await self._client.xadd(stream, {"payload": json.dumps(payload)})

    async def read_segments(self, *, group, consumer, stream, count=10):
        try:
            await self._client.xgroup_create(name=stream, groupname=group, id="0", mkstream=True)
        except Exception:
            pass
        resp = await self._client.xreadgroup(
            groupname=group, consumername=consumer, streams={stream: ">"}, count=count
        )
        out: list[tuple[str, dict]] = []
        for _stream_name, messages in resp or []:
            for message_id, fields in messages:
                mid = message_id.decode() if isinstance(message_id, bytes) else message_id
                decoded = {
                    (k.decode() if isinstance(k, bytes) else k):
                    (v.decode() if isinstance(v, bytes) else v)
                    for k, v in fields.items()
                }
                out.append((mid, decoded))
        return out

    async def ack(self, *, group, stream, message_ids):
        if message_ids:
            await self._client.xack(stream, group, *message_ids)

    async def publish(self, channel, data):
        self.published.append((channel, data))
        return await self._client.publish(channel, data)
