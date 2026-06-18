"""The runtime kernel — orchestrates a workload through the runtime.v1 lifecycle over a Backend,
emitting RuntimeEvents on every transition. `profile` is opaque (P11): the kernel maps it to a
runnable via a registry (policy/config), but the contract never sees the command."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Optional

from .backend import Backend, WorkloadHandle
from .models import RuntimeEvent, RuntimeState, StopReason, WorkloadSpec, WorkloadStatus
from .process_backend import ProcessBackend

# The opaque-profile → command registry. Real profiles are config (per deployment); this default
# carries only what tests/dev need. The contract (runtime.v1) never knows these commands.
DEFAULT_PROFILES: dict[str, list[str]] = {
    "test-sleep": ["sleep", "30"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Entry:
    __slots__ = ("spec", "handle", "status")

    def __init__(self, spec: WorkloadSpec, status: WorkloadStatus) -> None:
        self.spec = spec
        self.handle: Optional[WorkloadHandle] = None
        self.status = status


class Runtime:
    def __init__(
        self,
        backend: Optional[Backend] = None,
        profiles: Optional[dict[str, list[str]]] = None,
        on_event: Optional[Callable[[RuntimeEvent], None]] = None,
        grace_sec: float = 5.0,
    ) -> None:
        self.backend: Backend = backend or ProcessBackend()
        self.profiles = profiles if profiles is not None else dict(DEFAULT_PROFILES)
        self.on_event = on_event or (lambda e: None)
        self.grace_sec = grace_sec
        self._workloads: dict[str, _Entry] = {}

    def _emit(self, workload_id: str, state: RuntimeState, **kw) -> RuntimeEvent:
        ev = RuntimeEvent(workloadId=workload_id, state=state, at=_now(), **kw)
        self.on_event(ev)
        return ev

    # ── runtime.v1 operations ────────────────────────────────────────────────
    def create(self, spec: WorkloadSpec) -> WorkloadStatus:
        command = self.profiles.get(spec.profile)
        if command is None:
            raise ValueError(f"unknown profile: {spec.profile!r}")
        status = WorkloadStatus(
            workloadId=spec.workloadId, profile=spec.profile,
            state=RuntimeState.starting, backend=self.backend.name,
        )
        entry = _Entry(spec, status)
        self._workloads[spec.workloadId] = entry
        self._emit(spec.workloadId, RuntimeState.starting)
        try:
            entry.handle = self.backend.start(spec.workloadId, command, spec.env)
        except Exception:
            status.state = RuntimeState.stopped
            status.stopReason = StopReason.start_failed
            status.stoppedAt = _now()
            self._emit(spec.workloadId, RuntimeState.stopped, stopReason=StopReason.start_failed)
            return status
        status.state = RuntimeState.running
        status.startedAt = _now()
        status.ports = {}
        self._emit(spec.workloadId, RuntimeState.running, ports={})
        return status

    def get(self, workload_id: str) -> WorkloadStatus:
        entry = self._workloads[workload_id]
        # reflect a workload that exited on its own
        if entry.status.state == RuntimeState.running and entry.handle is not None:
            code = self.backend.exit_code(entry.handle)
            if code is not None:
                entry.status.state = RuntimeState.stopped
                entry.status.exitCode = code
                entry.status.stoppedAt = _now()
                entry.status.stopReason = StopReason.completed if code == 0 else StopReason.failed
                self._emit(workload_id, RuntimeState.stopped, exitCode=code, stopReason=entry.status.stopReason)
        return entry.status

    def list(self) -> list[WorkloadStatus]:
        return [self.get(wid) for wid in list(self._workloads)]

    def stop(self, workload_id: str, reason: StopReason = StopReason.stopped) -> WorkloadStatus:
        entry = self._workloads[workload_id]
        if entry.status.state in (RuntimeState.stopped, RuntimeState.destroyed):
            return entry.status
        entry.status.state = RuntimeState.stopping
        self._emit(workload_id, RuntimeState.stopping)
        h = entry.handle
        assert h is not None
        self.backend.terminate(h)                                   # graceful SIGTERM + grace window
        deadline = time.time() + self.grace_sec
        while self.backend.exit_code(h) is None and time.time() < deadline:
            time.sleep(0.02)
        if self.backend.exit_code(h) is None:
            self.backend.kill(h)                                    # force after grace
        code = self.backend.exit_code(h)
        entry.status.state = RuntimeState.stopped
        entry.status.exitCode = code
        entry.status.stoppedAt = _now()
        entry.status.stopReason = reason
        self._emit(workload_id, RuntimeState.stopped, exitCode=code, stopReason=reason)
        return entry.status

    def destroy(self, workload_id: str) -> WorkloadStatus:
        entry = self._workloads[workload_id]
        if entry.handle is not None:
            self.backend.cleanup(entry.handle)
        entry.status.state = RuntimeState.destroyed
        self._emit(workload_id, RuntimeState.destroyed)
        return entry.status
