"""The Clock port — the kernel's one dependency on the passage of time. Enforcement sweeps and the
job scheduler read "now" and sleep only through this port, so evals advance time deterministically
(no wall-clock waits, no flakiness) while production uses the real wall clock."""
from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Unix epoch seconds (like time.time())."""
        ...


class SystemClock:
    """Production clock — the real wall clock."""

    def now(self) -> float:
        return time.time()


class FakeClock:
    """Deterministic clock for evals. Time only moves when advance()/set() is called."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> float:
        self._t += float(seconds)
        return self._t

    def set(self, t: float) -> float:
        self._t = float(t)
        return self._t
