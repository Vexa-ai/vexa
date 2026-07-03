"""Enforcement — the runtime's reaper. Mirrors 0.11's `lifecycle.idle_loop` stop-decision, reimplemented
against the kernel + the Clock port so it is deterministic in evals (no wall-clock sleeps).

Two limits, both per-spec (runtime.v1):
  • idleTimeoutSec  — stop a workload that has had no activity (no /touch) for this long.
  • maxLifetimeSec  — stop a workload that has been alive this long, regardless of activity.

When a limit trips, the Enforcer stops the workload through the Runtime with the matching StopReason
(idle_timeout | max_lifetime — both already in the sealed runtime.v1 enum, NO schema change). Activity
is tracked in Clock epochs (independent of the ISO `startedAt` the contract shows), so a FakeClock can
drive the sweep frame-by-frame.

A profile may pin idleTimeoutSec=0 (meeting-bot — lifetime managed externally); 0 disables the idle
limit, matching 0.11's `idle_timeout: 0` semantics."""
from __future__ import annotations

from typing import Optional

from .clock import Clock, SystemClock
from .models import RuntimeState, StopReason


class Enforcer:
    def __init__(self, runtime, clock: Optional[Clock] = None) -> None:
        self.runtime = runtime
        self.clock = clock or runtime.clock or SystemClock()
        # workload_id -> {"started": epoch, "last_active": epoch}
        self._tracked: dict[str, dict[str, float]] = {}

    def track(self, workload_id: str) -> None:
        """Register a workload as running now. Call after create()."""
        now = self.clock.now()
        self._tracked[workload_id] = {"started": now, "last_active": now}

    def touch(self, workload_id: str) -> None:
        """Heartbeat — reset the idle clock (the /touch in 0.11)."""
        if workload_id in self._tracked:
            self._tracked[workload_id]["last_active"] = self.clock.now()

    def forget(self, workload_id: str) -> None:
        self._tracked.pop(workload_id, None)

    def _effective_limits(self, status, spec) -> tuple[Optional[int], Optional[int]]:
        """Resolve (idleTimeoutSec, maxLifetimeSec) — spec wins; profile defaults fill the gaps."""
        idle = spec.idleTimeoutSec
        max_life = spec.maxLifetimeSec
        profile = self.runtime.profiles.get(spec.profile)
        if profile is not None:
            if idle is None:
                idle = profile.idle_timeout_sec
            if max_life is None:
                max_life = profile.max_lifetime_sec
        return idle, max_life

    def sweep(self) -> list[str]:
        """One enforcement tick. Stop every running workload past a limit; return their ids."""
        now = self.clock.now()
        stopped: list[str] = []
        for record in self.runtime.store.list():
            status = record.status
            if status.state is not RuntimeState.running:
                continue
            wid = status.workloadId
            track = self._tracked.get(wid)
            if track is None:
                continue  # never registered (e.g. created before enforcer attached)

            idle, max_life = self._effective_limits(status, record.spec)
            reason: Optional[StopReason] = None

            if max_life and now - track["started"] >= max_life:
                reason = StopReason.max_lifetime
            elif idle and now - track["last_active"] >= idle:
                reason = StopReason.idle_timeout

            if reason is not None:
                self.runtime.stop(wid, reason=reason)
                self.forget(wid)
                stopped.append(wid)
        return stopped
