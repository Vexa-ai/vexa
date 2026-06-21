"""The runtime kernel — orchestrates a workload through the runtime.v1 lifecycle over a Backend,
emitting RuntimeEvents on every transition. `profile` is opaque (P11): the kernel maps it to a
runnable via a registry (policy/config), but the contract never sees the command.

Persistence is via the WorkloadStore port: the (spec, status) pair lives in the store (InMemory by
default, Redis for durability) so the runtime survives a restart. Live backend handles are NOT
serializable, so they live in a process-local map keyed by workloadId; on a fresh process they are
simply absent, and the reloaded statuses describe what was running before the restart.

Quotas (O-RT-2): create() rejects the N+1th active workload for an owner via the store's
count_for_owner."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Optional

from .backend import Backend, WorkloadHandle
from .clock import Clock, SystemClock
from .models import RuntimeEvent, RuntimeState, StopReason, WorkloadSpec, WorkloadStatus
from .process_backend import ProcessBackend
from .profiles import ProfileRegistry, Runnable, default_registry
from .store import (
    InMemoryStore,
    OwnerResolver,
    WorkloadRecord,
    WorkloadStore,
    default_owner,
)


class QuotaExceeded(Exception):
    """Raised by create() when an owner is already at their active-workload cap."""

    def __init__(self, owner: str, cap: int) -> None:
        self.owner = owner
        self.cap = cap
        super().__init__(f"owner {owner!r} at quota cap ({cap})")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Runtime:
    def __init__(
        self,
        backend: Optional[Backend] = None,
        profiles: Optional[dict | ProfileRegistry] = None,
        on_event: Optional[Callable[[RuntimeEvent], None]] = None,
        grace_sec: float = 5.0,
        store: Optional[WorkloadStore] = None,
        clock: Optional[Clock] = None,
        owner_resolver: OwnerResolver = default_owner,
        owner_quota: Optional[int] = None,
    ) -> None:
        self.backend: Backend = backend or ProcessBackend()
        # `profiles` accepts a ProfileRegistry, a plain {name: Runnable|command} dict (legacy/tests),
        # or None (the real default registry). We normalize to a ProfileRegistry.
        self.profiles: ProfileRegistry = _coerce_registry(profiles)
        self.on_event = on_event or (lambda e: None)
        self.grace_sec = grace_sec
        self.store: WorkloadStore = store if store is not None else InMemoryStore()
        self.clock: Clock = clock or SystemClock()
        self.owner_resolver = owner_resolver
        self.owner_quota = owner_quota
        # Live, non-serializable backend handles. Empty on a fresh process (post-restart).
        self._handles: dict[str, WorkloadHandle] = {}

    def _emit(self, workload_id: str, state: RuntimeState, **kw) -> RuntimeEvent:
        ev = RuntimeEvent(workloadId=workload_id, state=state, at=_now(), **kw)
        self.on_event(ev)
        return ev

    def _persist(self, spec: WorkloadSpec, status: WorkloadStatus) -> None:
        self.store.set(WorkloadRecord(spec=spec, status=status, owner=self.owner_resolver(spec)))

    def _record(self, workload_id: str) -> WorkloadRecord:
        record = self.store.get(workload_id)
        if record is None:
            raise KeyError(workload_id)
        return record

    # ── runtime.v1 operations ────────────────────────────────────────────────
    def create(self, spec: WorkloadSpec) -> WorkloadStatus:
        runnable = self.profiles.resolve(spec.profile)
        if runnable is None:
            raise ValueError(f"unknown profile: {spec.profile!r}")

        # Quota check (O-RT-2): reject the N+1th active workload for this owner.
        if self.owner_quota is not None:
            owner = self.owner_resolver(spec)
            if self.store.count_for_owner(owner) >= self.owner_quota:
                raise QuotaExceeded(owner, self.owner_quota)

        status = WorkloadStatus(
            workloadId=spec.workloadId, profile=spec.profile,
            state=RuntimeState.starting, backend=self.backend.name,
        )
        self._persist(spec, status)
        self._emit(spec.workloadId, RuntimeState.starting)
        try:
            self._handles[spec.workloadId] = self.backend.start(spec.workloadId, runnable, spec.env)
        except Exception:
            status.state = RuntimeState.stopped
            status.stopReason = StopReason.start_failed
            status.stoppedAt = _now()
            self._persist(spec, status)
            self._emit(spec.workloadId, RuntimeState.stopped, stopReason=StopReason.start_failed)
            return status
        status.state = RuntimeState.running
        status.startedAt = _now()
        status.ports = {}
        self._persist(spec, status)
        self._emit(spec.workloadId, RuntimeState.running, ports={})
        return status

    def get(self, workload_id: str) -> WorkloadStatus:
        record = self._record(workload_id)
        status = record.status
        handle = self._handles.get(workload_id)
        # reflect a workload that exited on its own (only observable while we hold a live handle)
        if status.state == RuntimeState.running and handle is not None:
            code = self.backend.exit_code(handle)
            if code is not None:
                status.state = RuntimeState.stopped
                status.exitCode = code
                status.stoppedAt = _now()
                status.stopReason = StopReason.completed if code == 0 else StopReason.failed
                self._persist(record.spec, status)
                self._emit(workload_id, RuntimeState.stopped, exitCode=code, stopReason=status.stopReason)
        return status

    def list(self) -> list[WorkloadStatus]:
        return [self.get(r.spec.workloadId) for r in self.store.list()]

    def stop(self, workload_id: str, reason: StopReason = StopReason.stopped) -> WorkloadStatus:
        record = self._record(workload_id)
        status = record.status
        if status.state in (RuntimeState.stopped, RuntimeState.destroyed):
            return status
        status.state = RuntimeState.stopping
        self._persist(record.spec, status)
        self._emit(workload_id, RuntimeState.stopping)
        h = self._handles.get(workload_id)
        if h is not None:
            self.backend.terminate(h)                               # graceful SIGTERM + grace window
            deadline = time.time() + self.grace_sec
            while self.backend.exit_code(h) is None and time.time() < deadline:
                time.sleep(0.02)
            if self.backend.exit_code(h) is None:
                self.backend.kill(h)                                # force after grace
            code = self.backend.exit_code(h)
        else:
            code = None                                             # no live handle (post-restart stop)
        status.state = RuntimeState.stopped
        status.exitCode = code
        status.stoppedAt = _now()
        status.stopReason = reason
        self._persist(record.spec, status)
        self._emit(workload_id, RuntimeState.stopped, exitCode=code, stopReason=reason)
        return status

    def destroy(self, workload_id: str) -> WorkloadStatus:
        record = self._record(workload_id)
        h = self._handles.get(workload_id)
        if h is not None:
            self.backend.cleanup(h)
        status = record.status
        status.state = RuntimeState.destroyed
        self._persist(record.spec, status)
        self._emit(workload_id, RuntimeState.destroyed)
        return status


def _coerce_registry(profiles) -> ProfileRegistry:
    """Normalize the `profiles` arg into a ProfileRegistry.

    None → the real default registry (meeting-bot + agent).
    ProfileRegistry → used as-is.
    dict → a registry where each value is a Runnable (a bare command list is wrapped)."""
    if profiles is None:
        return default_registry()
    if isinstance(profiles, ProfileRegistry):
        return profiles
    runnables: dict[str, Runnable] = {}
    for name, value in profiles.items():
        if isinstance(value, Runnable):
            runnables[name] = value
        elif isinstance(value, list):
            runnables[name] = Runnable(command=value)
        else:
            raise TypeError(f"profile {name!r}: expected Runnable or command list, got {type(value)}")
    return ProfileRegistry(runnables)
