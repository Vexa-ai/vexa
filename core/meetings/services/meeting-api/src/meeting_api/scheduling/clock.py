"""The Clock port — the scheduler's one dependency on the passage of time.

A faithful MIRROR of the runtime kernel's `runtime/src/runtime_kernel/clock.py`. We mirror
rather than import because the runtime is a separate package outside meeting-api's import
graph (the graph gate forbids the cross-package edge, and `croniter` is not the runtime's to
lend) — the SSOT for this brick is the `schedule.v1` contract, not the runtime's code. The
shape is intentionally identical so the pattern stays recognisable and a future consolidation
is a drop-in.

The scheduler reads "now" only through this port, so evals advance time deterministically
(no wall-clock waits, no flakiness) while production uses the real wall clock.
"""
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
