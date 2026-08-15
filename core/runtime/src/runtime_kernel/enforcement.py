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
limit, matching 0.11's `idle_timeout: 0` semantics.

**Stopped-container reclamation (`sweep_stopped`)** — a workload whose process EXITED is reflected to
`stopped` (exit code captured) by `Runtime.get()`, but the substrate object is NOT removed: Docker
`stop`/self-exit leaves the container in the `exited` state, and only `destroy()` (→ `cleanup`, the
force-delete) reclaims it. `sweep_stopped` destroys every workload that has stayed `stopped` past a
retention window, so finished bots cannot accumulate as exited containers. The retention window keeps
the exit-code evidence readable long enough for the control plane's liveness probe / reconcile to
observe it before the reclaim."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import time

from .clock import Clock, SystemClock
from .models import RuntimeState, StopReason

# How long a stopped (exited) workload keeps its container before the reaper destroys it. The
# retention preserves the exit-code evidence the control plane's liveness probe reads; past it the
# container is force-deleted so finished bots cannot accumulate as `exited` containers.
DEFAULT_STOPPED_RETENTION_SEC = 300


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

    def sweep_stopped(self, retention_sec: Optional[float] = None) -> list[str]:
        """Destroy workloads that have stayed `stopped` past the retention window; return their ids.

        Self-sufficient: each record goes through ``Runtime.get()`` first, which reflects a
        workload whose process exited on its own (exit code observed, ``stoppedAt`` stamped) — so
        an exited bot is reaped even when no control-plane probe ever touched it. Past the
        retention the workload is ``destroy()``ed, which reclaims the substrate object (Docker
        force-delete) — the container is REMOVED, not left in the `exited` state. Best-effort per
        workload: a failed destroy is retried on the next sweep.
        """
        retention = retention_sec if retention_sec is not None else DEFAULT_STOPPED_RETENTION_SEC
        # `stoppedAt` is wall-clock ISO (the contract's timestamp), so age is measured against real
        # time — the Enforcer's epoch clock drives the idle/lifetime sweep, not this one.
        now = time.time()
        destroyed: list[str] = []
        for record in self.runtime.store.list():
            wid = record.status.workloadId
            try:
                status = self.runtime.get(wid)  # reflects a self-exited workload (exit code captured)
            except KeyError:
                continue
            if status.state is not RuntimeState.stopped:
                continue
            if not status.stoppedAt:
                continue
            try:
                stopped_epoch = datetime.fromisoformat(
                    status.stoppedAt.replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, TypeError):
                continue
            if now - stopped_epoch < retention:
                continue
            try:
                self.runtime.destroy(wid)
                destroyed.append(wid)
            except Exception:  # noqa: BLE001 — reclaim is best-effort; retried next sweep
                continue
        return destroyed
