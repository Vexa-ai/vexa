"""The Clock port — the engine's ONE dependency on the passage of time (mirrors the
meeting-api/runtime pattern: mirrored, not imported). Everything stores epoch seconds."""
from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.time()


class FakeClock:
    """Deterministic time for tests: advance() instead of sleep()."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds
