"""Per-speaker silence-gap segmenter.

Ported (as a pure data structure — no DB/network side effects baked in) from the bridge's
``bot.py`` ``PcmBuffer``: Discord only sends voice packets while a user is transmitting, so a gap
with no packets ends an utterance. Accumulates per-user PCM the ``DAVEVoiceClient.on_pcm`` callback
delivers; ``drain_ready`` pops whichever users have gone silent for at least ``silence_s``.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Segment:
    user_id: int
    pcm: bytes
    start: float  # monotonic seconds, relative to the caller's own clock
    end: float


class PcmBuffer:
    """Thread-safe: ``write`` runs on the voice-receive callback, ``drain_*`` on the flusher loop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buf: dict[int, bytearray] = defaultdict(bytearray)
        self._last: dict[int, float] = {}
        self._start: dict[int, float] = {}

    def write(self, user_id: int, data: bytes, *, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            if not self._buf[user_id]:
                self._start[user_id] = now
            self._buf[user_id].extend(data)
            self._last[user_id] = now

    def _pop(self, *, only_silent: bool, silence_s: float = 0.0, now: Optional[float] = None) -> list[Segment]:
        now = time.monotonic() if now is None else now
        out: list[Segment] = []
        with self._lock:
            for user_id in list(self._buf.keys()):
                buf = self._buf[user_id]
                if not buf:
                    continue
                if only_silent and (now - self._last.get(user_id, now)) < silence_s:
                    continue
                out.append(Segment(user_id, bytes(buf), self._start.get(user_id, now), self._last.get(user_id, now)))
                self._buf[user_id] = bytearray()
        return out

    def drain_ready(self, silence_s: float, *, now: Optional[float] = None) -> list[Segment]:
        """Pop every user whose last packet is at least ``silence_s`` old (an ended utterance)."""
        return self._pop(only_silent=True, silence_s=silence_s, now=now)

    def drain_all(self) -> list[Segment]:
        """Pop everything, regardless of silence — used on leave/shutdown."""
        return self._pop(only_silent=False)
