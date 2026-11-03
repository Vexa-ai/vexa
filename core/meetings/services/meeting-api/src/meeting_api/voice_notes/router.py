"""The voice-note audio WebSocket — ``WS /voice-notes/{native_meeting_id}/stream`` (#1783).

Protocol (one socket per recording stretch; a dropped socket may reconnect and continue):

  server → ``{"type": "ready", "max_duration_sec": N, "received_sec": s}`` once accepted
  client → binary capture.v1 audio frames (no-name shape: Int32 track, Float64 capture ms, mono
           16 kHz Float32 PCM), at most one second each
  server → ``{"type": "progress", "received_sec": s}`` about once a second of audio
  client → ``{"type": "stop"}``; server appends the end marker, answers
           ``{"type": "stopped", "received_sec": s}`` and closes 1000

Refusals are typed: a close code plus, once accepted, an ``{"type": "error", "error": <code>}``
frame first. 4404 ``not_found`` (no live voice note of the caller's by that id), 4400
``bad_frame`` (not a capture.v1 audio frame, or longer than a second), 4413 ``max_duration``
(the cap is reached: the note is ended for the person, as if they had pressed Stop), 4429
``rate_limited`` (text frames past their budget).

Limits on what one socket can put into Redis:

* audio is taken at no more than ``PACE_FACTOR`` (1.5x) realtime past a ``PACE_BURST_SEC`` burst,
  measured on the wall clock: a sender that is faster waits (the socket is read more slowly), so
  the cap is reached no sooner than the cap's own duration divided by 1.5;
* a keepalive entry is written at most once per ``ALIVE_MIN_INTERVAL_SEC`` (every ping is answered);
* text frames share a small token bucket (``TEXT_FRAME_BURST``, refilled ``TEXT_FRAMES_PER_SEC``);
* the stream and its sample count expire ``stream_ttl_sec()`` after the last write (minutes), and
  ``END_TTL_SEC`` after the end marker; the lifecycle drops both when the note ends, and erasing
  the meeting drops them too (``drop``).

The caller is the person the gateway signed for (``x-user-id``, verified by the identity guard
before this route runs); the meeting must be theirs, a voice note, and not yet ended.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import struct
import time
from typing import Awaitable, Callable, Optional, Protocol, runtime_checkable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from ..bot_spawn.ports import MeetingRepo
from ..bot_spawn.workload_redis import voice_note_stream

PLATFORM = "voice_note"
SAMPLE_RATE = 16_000
HEADER_BYTES = 12
NAME_FLAG = 0x80000000
#: One frame carries at most a second of audio (a browser sends ~100–250 ms).
MAX_FRAME_SAMPLES = SAMPLE_RATE
#: A plausible capture time, epoch ms: 2001-09-09 to 2100-01-01.
EPOCH_MS_MIN = 1e12
EPOCH_MS_MAX = 4.1e12
CLOSE_BAD_FRAME = 4400
CLOSE_NOT_FOUND = 4404
CLOSE_CAP_REACHED = 4413
CLOSE_RATE_LIMITED = 4429
DEFAULT_MAX_DURATION_SEC = 1800
DEFAULT_IDLE_TIMEOUT_SEC = 120
#: How long the audio stream and its sample count outlive their last write while a note runs. A
#: running note writes at least every ping (15 s); a note nobody writes to is ended by its bot's idle
#: timeout well before this.
STREAM_TTL_MIN_SEC = 600
#: How long the stream outlives its end marker: the bot reads the end within seconds, and the
#: lifecycle drops the stream when the note completes; this bounds a note whose bot never got there.
END_TTL_SEC = 300
#: Audio is taken at no more than this multiple of realtime, past a burst of PACE_BURST_SEC.
PACE_FACTOR = 1.5
PACE_BURST_SEC = 5.0
#: A keepalive entry is written at most this often (the browser pings every 15 s).
ALIVE_MIN_INTERVAL_SEC = 5.0
#: Text frames (stop, ping): a bucket of TEXT_FRAME_BURST, refilled TEXT_FRAMES_PER_SEC a second.
TEXT_FRAME_BURST = 20
TEXT_FRAMES_PER_SEC = 2.0


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number of seconds, got {raw!r}")
    if value < 1:
        raise ValueError(f"{name} must be at least 1, got {value}")
    return value


def max_duration_sec() -> int:
    """The longest voice note, in seconds of audio (``VOICE_NOTE_MAX_DURATION_SEC``)."""
    return _env_int("VOICE_NOTE_MAX_DURATION_SEC", DEFAULT_MAX_DURATION_SEC)


def idle_timeout_sec() -> int:
    """How long a bot waits with no audio and no Stop before it ends the note
    (``VOICE_NOTE_IDLE_TIMEOUT_SEC``)."""
    return _env_int("VOICE_NOTE_IDLE_TIMEOUT_SEC", DEFAULT_IDLE_TIMEOUT_SEC)


def stream_ttl_sec() -> int:
    """How long a running note's stream outlives its last write: minutes, and always past the idle
    timeout (by then the bot has ended the note)."""
    return max(STREAM_TTL_MIN_SEC, 2 * idle_timeout_sec())


def frame_samples(frame: bytes) -> Optional[int]:
    """The sample count of a no-name capture.v1 audio frame, or None when it is not one."""
    n = len(frame)
    if n < HEADER_BYTES or (n - HEADER_BYTES) % 4:
        return None
    track, ts = struct.unpack_from("<id", frame, 0)
    # The capture time is epoch milliseconds (capture.v1: the sender stamps it, nobody restamps).
    if track < 0 or track & NAME_FLAG or not math.isfinite(ts) or not EPOCH_MS_MIN <= ts <= EPOCH_MS_MAX:
        return None
    samples = (n - HEADER_BYTES) // 4
    if samples == 0 or samples > MAX_FRAME_SAMPLES:
        return None
    return samples


@runtime_checkable
class AudioStreamWriter(Protocol):
    """The Redis side: append frames and the end marker, and count a note's samples."""

    async def append(self, stream: str, frame: bytes, *, ttl_sec: int) -> None: ...

    async def end(self, stream: str, *, ttl_sec: int) -> None: ...

    async def alive(self, stream: str, *, ttl_sec: int) -> None:
        """A keepalive entry: the person is still there (a paused note), so the bot's idle timer resets."""
        ...

    async def add_samples(self, stream: str, samples: int, *, ttl_sec: int) -> int:
        """Add to the note's running sample count and return the new total."""
        ...

    async def samples(self, stream: str) -> int: ...

    async def drop(self, stream: str) -> None:
        """Remove the note's stream and its sample count (the note ended, or was erased)."""
        ...


class InMemoryAudioStreamWriter:
    """The offline writer (tests and the in-process app)."""

    def __init__(self) -> None:
        self.entries: dict[str, list[dict]] = {}
        self.counts: dict[str, int] = {}
        #: The last expiry each stream was given, in seconds.
        self.ttls: dict[str, int] = {}

    async def append(self, stream: str, frame: bytes, *, ttl_sec: int) -> None:
        self.entries.setdefault(stream, []).append({"f": bytes(frame)})
        self.ttls[stream] = ttl_sec

    async def end(self, stream: str, *, ttl_sec: int) -> None:
        self.entries.setdefault(stream, []).append({"end": "1"})
        self.ttls[stream] = ttl_sec

    async def alive(self, stream: str, *, ttl_sec: int) -> None:
        self.entries.setdefault(stream, []).append({"alive": "1"})
        self.ttls[stream] = ttl_sec

    async def add_samples(self, stream: str, samples: int, *, ttl_sec: int) -> int:
        self.counts[stream] = self.counts.get(stream, 0) + samples
        return self.counts[stream]

    async def samples(self, stream: str) -> int:
        return self.counts.get(stream, 0)

    async def drop(self, stream: str) -> None:
        self.entries.pop(stream, None)
        self.counts.pop(stream, None)
        self.ttls.pop(stream, None)


class RedisAudioStreamWriter:
    """The production writer over meeting-api's own async Redis connection. The stream is capped by
    length as a backstop (the sample count is the real cap), and both keys expire on their own."""

    def __init__(self, client) -> None:
        self._r = client

    async def append(self, stream: str, frame: bytes, *, ttl_sec: int) -> None:
        await self._r.xadd(stream, {"f": bytes(frame)}, maxlen=200_000, approximate=True)
        await self._r.expire(stream, ttl_sec)

    async def end(self, stream: str, *, ttl_sec: int) -> None:
        await self._r.xadd(stream, {"end": "1"})
        await self._r.expire(stream, ttl_sec)

    async def alive(self, stream: str, *, ttl_sec: int) -> None:
        await self._r.xadd(stream, {"alive": "1"}, maxlen=200_000, approximate=True)
        await self._r.expire(stream, ttl_sec)

    async def add_samples(self, stream: str, samples: int, *, ttl_sec: int) -> int:
        key = f"{stream}:samples"
        total = int(await self._r.incrby(key, samples))
        await self._r.expire(key, ttl_sec)
        return total

    async def samples(self, stream: str) -> int:
        raw = await self._r.get(f"{stream}:samples")
        return int(raw or 0)

    async def drop(self, stream: str) -> None:
        await self._r.delete(stream, f"{stream}:samples")


async def drop_voice_note_stream(writer: AudioStreamWriter, meeting_id: int) -> None:
    """Remove a meeting's voice-note stream, if it has one (a DEL on a missing key is a no-op)."""
    await writer.drop(voice_note_stream(int(meeting_id)))


def _user_id(ws: WebSocket) -> Optional[int]:
    raw = ws.headers.get("x-user-id")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


async def _refuse(ws: WebSocket, code: int, error: str, *, accepted: bool) -> None:
    if accepted:
        try:
            await ws.send_text(json.dumps({"type": "error", "error": error}))
        except Exception:  # noqa: BLE001 — the close below is the refusal
            pass
    await ws.close(code=code)


class _Bucket:
    def __init__(self, capacity: float, refill_per_sec: float, now: float) -> None:
        self.capacity, self.refill, self.tokens, self.last = capacity, refill_per_sec, capacity, now

    def take(self, now: float) -> bool:
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill)
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


def build_router(repo: MeetingRepo, writer: AudioStreamWriter, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> APIRouter:
    router = APIRouter()

    @router.get("/voice-notes/{native_meeting_id}/stream")
    async def voice_note_stream_without_upgrade(native_meeting_id: str):
        """The row is the socket's upgrade request; a plain GET is told so (426), never served."""
        return JSONResponse(status_code=426, headers={"Upgrade": "websocket"},
                            content={"detail": "this is a WebSocket: connect with an Upgrade request"})

    @router.websocket("/voice-notes/{native_meeting_id}/stream")
    async def voice_note_stream_socket(ws: WebSocket, native_meeting_id: str):
        user_id = _user_id(ws)
        meeting = None
        if user_id is not None:
            meeting = await repo.find_active(user_id, PLATFORM, native_meeting_id)
        if meeting is None:
            await ws.accept()
            await _refuse(ws, CLOSE_NOT_FOUND, "not_found", accepted=True)
            return
        cap_sec = int(((meeting.get("data") or {}).get("voice_note") or {}).get("max_duration_sec")
                      or max_duration_sec())
        cap_samples = cap_sec * SAMPLE_RATE
        stream = voice_note_stream(meeting["id"])
        ttl = stream_ttl_sec()
        await ws.accept()
        total = await writer.samples(stream)
        await ws.send_text(json.dumps({"type": "ready", "max_duration_sec": cap_sec,
                                       "received_sec": round(total / SAMPLE_RATE, 2)}))
        last_progress = total
        opened = clock()
        taken = 0                     # samples this socket took (the pace is per socket)
        last_alive: Optional[float] = None
        text_budget = _Bucket(TEXT_FRAME_BURST, TEXT_FRAMES_PER_SEC, opened)
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
                data = msg.get("bytes")
                if data is not None:
                    n = frame_samples(data)
                    if n is None:
                        await _refuse(ws, CLOSE_BAD_FRAME, "bad_frame", accepted=True)
                        return
                    if total + n > cap_samples:
                        await writer.end(stream, ttl_sec=END_TTL_SEC)
                        await _refuse(ws, CLOSE_CAP_REACHED, "max_duration", accepted=True)
                        return
                    # The pace: this frame is taken once the wall clock allows it.
                    taken += n
                    due = opened + (taken / SAMPLE_RATE - PACE_BURST_SEC) / PACE_FACTOR
                    wait = due - clock()
                    if wait > 0:
                        await sleep(wait)
                    await writer.append(stream, data, ttl_sec=ttl)
                    total = await writer.add_samples(stream, n, ttl_sec=ttl)
                    if total - last_progress >= SAMPLE_RATE:
                        last_progress = total
                        await ws.send_text(json.dumps({"type": "progress",
                                                       "received_sec": round(total / SAMPLE_RATE, 2)}))
                    continue
                text = msg.get("text")
                now = clock()
                if not text_budget.take(now):
                    await _refuse(ws, CLOSE_RATE_LIMITED, "rate_limited", accepted=True)
                    return
                try:
                    command = json.loads(text) if text else None
                except ValueError:
                    command = None
                if isinstance(command, dict) and command.get("type") == "stop":
                    await writer.end(stream, ttl_sec=END_TTL_SEC)
                    await ws.send_text(json.dumps({"type": "stopped",
                                                   "received_sec": round(total / SAMPLE_RATE, 2)}))
                    await ws.close(code=1000)
                    return
                if isinstance(command, dict) and command.get("type") == "ping":
                    if last_alive is None or now - last_alive >= ALIVE_MIN_INTERVAL_SEC:
                        last_alive = now
                        await writer.alive(stream, ttl_sec=ttl)
                    await ws.send_text(json.dumps({"type": "pong"}))
                    continue
                await ws.send_text(json.dumps({"type": "error", "error": "unknown_message"}))
        except WebSocketDisconnect:
            return

    return router
