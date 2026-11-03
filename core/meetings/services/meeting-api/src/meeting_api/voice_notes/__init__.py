"""voice_notes — a voice note's audio door (#1783).

A voice note is a meeting with no platform to join: the person records in their browser, and the
audio reaches the same bot pipeline a meeting's does. ``POST /bots {"platform": "voice_note"}``
creates the meeting row and spawns the bot (bot_spawn); this module is the one route that differs —
the WebSocket the browser streams its capture.v1 audio frames over, through the gateway:

    WS /voice-notes/{native_meeting_id}/stream
    POST /voice-notes/recorder   (the MCP tool record_voice_note: opens the recorder in Minutes)

Each binary frame is appended to the note's Redis stream (``voice_note:meeting:{id}:audio``, field
``f``), which the bot reads in order (the stream is the buffer: audio sent while the bot boots waits
there). A text ``{"type": "stop"}`` appends the end marker (field ``end``) and the bot finalizes the
note like a meeting end. The note's length is capped (``VOICE_NOTE_MAX_DURATION_SEC``), counted in
samples across reconnects. The stream lives minutes past its last write and is dropped when the
note ends or the meeting is erased.

Front door (P6): import from here, never a deep module path.
"""
from __future__ import annotations

from .recorder import UI_ACTION as RECORDER_UI_ACTION, build_recorder_router
from .router import (
    CLOSE_BAD_FRAME,
    CLOSE_CAP_REACHED,
    CLOSE_NOT_FOUND,
    CLOSE_RATE_LIMITED,
    END_TTL_SEC,
    InMemoryAudioStreamWriter,
    AudioStreamWriter,
    RedisAudioStreamWriter,
    build_router,
    drop_voice_note_stream,
    max_duration_sec,
    idle_timeout_sec,
    stream_ttl_sec,
)

__all__ = [
    "CLOSE_BAD_FRAME",
    "CLOSE_CAP_REACHED",
    "CLOSE_NOT_FOUND",
    "CLOSE_RATE_LIMITED",
    "END_TTL_SEC",
    "RECORDER_UI_ACTION",
    "AudioStreamWriter",
    "InMemoryAudioStreamWriter",
    "RedisAudioStreamWriter",
    "build_recorder_router",
    "build_router",
    "drop_voice_note_stream",
    "idle_timeout_sec",
    "max_duration_sec",
    "stream_ttl_sec",
]
