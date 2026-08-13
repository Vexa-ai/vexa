"""The meeting session orchestrator — replaces the bridge's ``bot.py`` control plane.

Wires the segmenter, transcription, transcript.v1 emission, lifecycle.v1 callbacks, acts.v1
handling, and leave-on-empty-channel detection over INJECTED ports, so the whole flow runs offline
in tests. This module knows nothing about ``discord``/py-cord or ``dave`` types — the real voice
join (``DAVEVoiceProtocol`` + ``DAVEVoiceClient``) and the py-cord ``Bot`` wiring live in
``bot.py``'s composition root, which drives this class from real callbacks.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from discord_bot.adapters.lifecycle_http import LifecycleSink
from discord_bot.adapters.transcript_redis import TranscriptSink
from discord_bot.audio import BYTES_PER_SEC, to_mono_wav
from discord_bot.segmenter import PcmBuffer, Segment

#: (meeting-relative WAV, language) -> transcript text, or None on a transient worker failure.
TranscribeFn = Callable[[bytes, Optional[str]], Awaitable[Optional[str]]]
#: user_id -> display name (async: may fetch a guild member on cache miss).
NameForFn = Callable[[int], Awaitable[str]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_from_epoch(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def failed_event(connection_id: str, *, failure_stage: str, completion_reason: str, reason: str) -> dict:
    """Build a lifecycle.v1 'failed' event. A free function (not just ``MeetingSession.emit_failed``)
    so ``bot.py``'s composition root can emit the same shape for failures that happen before a
    ``MeetingSession`` exists yet (e.g. a ``client.login()`` failure)."""
    return {
        "connection_id": connection_id,
        "status": "failed",
        "failure_stage": failure_stage,
        "completion_reason": completion_reason,
        "reason": reason,
        "exit_code": 1,
        "timestamp": _now_iso(),
    }


class MeetingSession:
    def __init__(
        self,
        *,
        invocation: dict[str, Any],
        lifecycle: LifecycleSink,
        transcript: TranscriptSink,
        transcribe: TranscribeFn,
        name_for: NameForFn,
        silence_ms: int = 800,
        min_utterance_ms: int = 400,
        everyone_left_timeout_ms: Optional[int] = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inv = invocation
        self._lifecycle = lifecycle
        self._transcript = transcript
        self._transcribe = transcribe
        self._name_for = name_for
        self._silence_s = silence_ms / 1000
        self._min_ms = min_utterance_ms
        automatic_leave = invocation.get("automaticLeave") or {}
        self._alone_timeout_s = (
            everyone_left_timeout_ms
            if everyone_left_timeout_ms is not None
            else automatic_leave.get("everyoneLeftTimeout", 120_000)
        ) / 1000
        self._now = now
        self._language = invocation.get("language")

        self.connection_id: str = invocation.get("connectionId") or ""
        self.sink = PcmBuffer()
        self._t0 = now()
        # Wall-clock anchor for self._t0, captured with the real clock (not the injectable `now`,
        # which is monotonic and carries no absolute meaning) so segment offsets can be converted
        # to absolute ISO times below.
        self._t0_wall = time.time()
        self._alone_since: Optional[float] = None
        self._stopping = False
        self._stop_reason: Optional[str] = None
        self._on_stop_requested: Optional[Callable[[], None]] = None

    # ---- lifecycle.v1 emission ------------------------------------------------------------------

    async def emit_joining(self) -> None:
        await self._lifecycle.emit({"connection_id": self.connection_id, "status": "joining", "timestamp": _now_iso()})

    async def emit_active(self) -> None:
        await self._lifecycle.emit({"connection_id": self.connection_id, "status": "active", "timestamp": _now_iso()})

    async def emit_failed(self, *, failure_stage: str, completion_reason: str, reason: str) -> None:
        await self._lifecycle.emit(
            failed_event(
                self.connection_id, failure_stage=failure_stage, completion_reason=completion_reason, reason=reason
            )
        )

    async def emit_completed(self, completion_reason: str) -> None:
        await self._lifecycle.emit(
            {
                "connection_id": self.connection_id,
                "status": "completed",
                "completion_reason": completion_reason,
                "timestamp": _now_iso(),
            }
        )

    # ---- voice receive → segmenter ---------------------------------------------------------------

    def on_pcm(self, user_id: int, pcm: bytes) -> None:
        """The ``DAVEVoiceClient.on_pcm`` callback — accumulate PCM for the silence-gap segmenter."""
        self.sink.write(user_id, pcm, now=self._now())

    async def _emit_segment(self, seg: Segment) -> None:
        duration_ms = (len(seg.pcm) / BYTES_PER_SEC) * 1000
        if duration_ms < self._min_ms:
            return  # drop blips shorter than the configured minimum
        text = await self._transcribe(to_mono_wav(seg.pcm), self._language)
        if not text:
            return  # worker unreachable (None) or genuine silence/VAD-stripped ("") — either way, nothing to emit
        name = await self._name_for(seg.user_id)
        start = seg.start - self._t0
        end = seg.end - self._t0
        segment = {
            "segment_id": f"{self.connection_id}:discord:{seg.user_id}:{int(start * 1000)}",
            "speaker": name,
            "speaker_key": f"discord:{seg.user_id}",
            "text": text,
            "start": start,
            "end": end,
            "completed": True,
            # Stamp the canonical ISO absolute time HERE, at the single producer chokepoint,
            # mirroring the TS parity reference's toBotSegment (services/bot/src/pipeline.ts).
            # The live pub/sub ingest path (meeting_api/collector/ingest.py) passes segments
            # straight through with no backfill; only the REST read path backfills. Without this,
            # a live Discord meeting renders nothing until a page reload hits the REST path.
            "absolute_start_time": _iso_from_epoch(self._t0_wall + start),
            "absolute_end_time": _iso_from_epoch(self._t0_wall + end),
        }
        if self._language:
            segment["language"] = self._language
        await self._transcript.publish(segment)

    async def flush_ready(self) -> None:
        """Poll tick: flush + transcribe + emit every user who has gone silent long enough."""
        for seg in self.sink.drain_ready(self._silence_s, now=self._now()):
            await self._emit_segment(seg)

    async def flush_all(self) -> None:
        """Flush everything regardless of silence — used on leave/shutdown."""
        for seg in self.sink.drain_all():
            await self._emit_segment(seg)

    # ---- left_alone (leave-on-empty-channel) ------------------------------------------------------

    def observe_channel_members(self, non_bot_member_count: int) -> bool:
        """Feed the current non-bot member count of the voice channel. Returns True the instant the
        channel has been empty of humans for >= everyoneLeftTimeout — the caller should then stop
        the session with completion_reason 'left_alone'. Mirrors the TS bot's aloneness window, but
        driven by Discord's authoritative channel roster instead of an audio-energy heuristic —
        Discord tells us exactly who is present, so there is no signal to approximate here."""
        now = self._now()
        if non_bot_member_count > 0:
            self._alone_since = None
            return False
        if self._alone_since is None:
            self._alone_since = now
            return False
        return (now - self._alone_since) >= self._alone_timeout_s

    # ---- acts.v1 ------------------------------------------------------------------------------

    async def handle_act(self, act: dict) -> None:
        """Core control acts.v1 promises to always honor. Voice-agent commands (speak/chat/screen/
        avatar) need ``voiceAgentEnabled`` + TTS/avatar infra this platform lane does not implement
        yet; they parse (contract-valid) but are deliberately no-ops here.
        # ponytail: voice-agent acts are no-ops; wire them when Discord TTS playback is built.
        """
        action = act.get("action")
        if action == "leave":
            self.request_stop("stopped")
        elif action == "reconfigure":
            if "language" in act:
                self._language = act["language"]

    def on_stop_requested(self, callback: Callable[[], None]) -> None:
        self._on_stop_requested = callback

    def request_stop(self, reason: str) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._stop_reason = reason
        if self._on_stop_requested:
            self._on_stop_requested()

    @property
    def stopping(self) -> bool:
        return self._stopping

    @property
    def stop_reason(self) -> Optional[str]:
        return self._stop_reason
