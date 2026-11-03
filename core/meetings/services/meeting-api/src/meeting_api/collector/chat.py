"""In-meeting chat as the collector sees it: transcript.v1 segments with ``source:'chat'``.

The bot captures a chat line as a point-in-time segment (``speaker`` = the sender, ``text`` = the
message, ``start == end`` = when it was sent). There is no separate chat store — chat is a slice of
the transcript, and this module is the one place that knows how to cut and shape it:

  * ``chat_message(seg)`` — one segment → the sealed api.v1 ``ChatMessage``
    ``{sender, text, timestamp, is_from_bot}`` (``GET /bots/{platform}/{native}/chat``).
  * ``chat_frame(seg, meeting_id, native_id, platform)`` — the same message as the ws.v1
    ``ChatMessage`` frame published on ``va:meeting:{id}:chat`` (the channel the gateway ``/ws``
    fans in); ``meeting`` names the meeting the way the ``transcript`` frame does, so a client
    multiplexing several meetings can route it.

``timestamp`` is epoch MILLISECONDS — what the api.v1 consumers render with ``new Date(ts)``.
``is_from_bot`` is always ``false``: transcript.v1 carries no author-is-the-bot marker, so a line the
bot sent and its own reader captured is indistinguishable here from a participant's.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

CHAT_SOURCE = "chat"

# A segment time at or above this is already an epoch (seconds) rather than a session offset.
_EPOCH_THRESHOLD_S = 1_000_000_000


def chat_channel(meeting_id: int) -> str:
    """The pubsub channel the gateway ``/ws`` subscribes per meeting for chat frames."""
    return f"va:meeting:{meeting_id}:chat"


def is_chat(seg: dict) -> bool:
    return isinstance(seg, dict) and seg.get("source") == CHAT_SOURCE


def _timestamp_ms(seg: dict) -> Optional[int]:
    start = seg.get("start")
    try:
        start = float(start) if start is not None else None
    except (TypeError, ValueError):
        start = None
    if start is not None and start >= _EPOCH_THRESHOLD_S:
        return round(start * 1000)
    absolute = seg.get("absolute_start_time")
    if isinstance(absolute, str) and absolute:
        try:
            return round(datetime.fromisoformat(absolute.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            pass
    return round(start * 1000) if start is not None else None


def chat_message(seg: dict) -> dict:
    """A ``source:'chat'`` segment → api.v1 ``ChatMessage``."""
    return {
        "sender": seg.get("speaker") or "",
        "text": seg.get("text") or "",
        "timestamp": _timestamp_ms(seg) or 0,
        "is_from_bot": False,
    }


def chat_messages(segments: list) -> list[dict]:
    """Every chat segment among ``segments`` as ``ChatMessage`` rows, oldest first."""
    chats = [s for s in segments or () if is_chat(s)]
    rows = [(chat_message(s), str(s.get("segment_id") or "")) for s in chats]
    rows.sort(key=lambda r: (r[0]["timestamp"], r[1]))
    return [msg for msg, _sid in rows]


def chat_frame(seg: dict, *, meeting_id: int, native_id: Optional[str],
               platform: Optional[str]) -> dict:
    """A ``source:'chat'`` segment → the ws.v1 ``ChatMessage`` frame."""
    return {
        "type": "chat_message",
        "meeting": {"id": meeting_id, "native_id": native_id, "platform": platform},
        **chat_message(seg),
    }
