"""transcript.v1 egress adapter — redis stream + pub/sub.

Mirrors ``services/bot/src/adapters/transcript-redis.ts`` (the TS bot's own transcript sink) wire
shape byte-for-byte, so the collector [Py] and the gateway's live ``/ws`` forward Discord segments
identically to every other platform's:

  1. STREAM ``transcription_segments`` (XADD) — the durable feed the collector consumes. The
     collector's ``ingest`` requires the envelope ``{type, meeting_id, segments:[...]}``.
  2. PUB/SUB ``tc:meeting:{meetingId}:mutable`` — the live channel the gateway forwards to the
     dashboard.

Injected with a minimal redis surface (``xadd``/``publish``) so this is offline-testable with a
fake or with ``fakeredis.aioredis`` — no live redis required.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Protocol

#: The redis stream the collector consumes (durable transcript.v1 feed).
TRANSCRIPTION_STREAM = "transcription_segments"


def mutable_channel(meeting_id: Any) -> str:
    """The pubsub channel the gateway's ``/ws`` forwards to the dashboard."""
    return f"tc:meeting:{meeting_id}:mutable"


class RedisTranscriptClient(Protocol):
    async def xadd(self, name: str, fields: dict[str, str]) -> Any: ...
    async def publish(self, channel: str, message: str) -> Any: ...


class TranscriptSink:
    """The live transcript sink. ``publish`` XADDs the durable feed AND publishes the live mutable
    channel for one segment — the same two-leg fan-out the TS bot's adapter performs."""

    def __init__(self, client: RedisTranscriptClient, meeting_id: Any, native_meeting_id: Optional[str] = None):
        self._client = client
        self._meeting_id = meeting_id
        self._native_meeting_id = native_meeting_id
        self._channel = mutable_channel(meeting_id)

    async def publish(self, segment: dict) -> None:
        payload = json.dumps(
            {
                "type": "transcription",
                "meeting_id": self._meeting_id,
                "native_meeting_id": self._native_meeting_id,
                "segments": [segment],
            }
        )
        await self._client.xadd(TRANSCRIPTION_STREAM, {"payload": payload})
        msg = json.dumps({"type": "transcript", "meeting": {"id": self._meeting_id}, "segment": segment})
        await self._client.publish(self._channel, msg)
