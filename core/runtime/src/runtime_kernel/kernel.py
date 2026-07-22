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


class StartFailed(Exception):
    """Raised by create() when the backend could not START the workload (e.g. the docker
    image is absent → a 404 on container create). The honest ``stopped``/``start_failed``
    record is persisted and its RuntimeEvent emitted BEFORE this raises, so ``GET /workloads``
    and the callback stream still tell the truth; the raise carries the backend's own reason
    text so the API answers a non-201 that NAMES the cause instead of a false ``spawned`` 201
    over a workload that never came up."""

    def __init__(self, workload_id: str, reason: str) -> None:
        self.workload_id = workload_id
        self.reason = reason
        super().__init__(reason)


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

    def _handle_for(self, workload_id: str) -> Optional[WorkloadHandle]:
        """The live handle for a workload — re-derived from the substrate via the backend's
        optional ``find`` when the in-process map lost it (a restarted runtime). Without this, a
        post-restart ``stop``/``destroy`` would report success while the real container kept
        running (the orphaned-live-bot defect)."""
        h = self._handles.get(workload_id)
        if h is None:
            finder = getattr(self.backend, "find", None)
            if finder is not None:
                try:
                    h = finder(workload_id)
                except Exception:  # noqa: BLE001 — a failed lookup means "no handle", never a crash
                    h = None
                if h is not None:
                    self._handles[workload_id] = h
        return h

    def adopt(self) -> int:
        """Boot-time re-adoption (the orphaned-live-bot fix): ask the backend for the workload
        containers it spawned that still exist on the substrate, re-attach live handles, and
        re-register any record the (in-memory) store lost across the restart — so
        ``GET /workloads/{id}`` answers truthfully after a runtime recreate instead of 404ing over
        a still-running bot, and stop/destroy reach the real container again.

        A still-running container re-registers as ``running``; an exited one as ``stopped`` with
        its real exit code (EVIDENCE the control plane's lifecycle decisions can act on). Records
        a durable store kept only get their handle re-attached. Returns the number of records
        re-registered. Never raises — adoption must not block the boot."""
        lister = getattr(self.backend, "list_workload_containers", None)
        if lister is None:
            return 0
        try:
            discovered = lister()
        except Exception:  # noqa: BLE001 — discovery failure must not block the boot
            return 0
        adopted = 0
        for info in discovered:
            wid = info.get("workload_id")
            name = info.get("name")
            if not wid or not name:
                continue
            self._handles.setdefault(wid, WorkloadHandle(id=wid, impl=name))
            if self.store.get(wid) is not None:
                continue                       # durable store kept the record — handle was the gap
            spec = WorkloadSpec(workloadId=wid, profile="adopted", env={})
            status = WorkloadStatus(
                workloadId=wid, profile=spec.profile,
                state=RuntimeState.running, backend=self.backend.name,
            )
            if not info.get("running"):
                code = info.get("exit_code")
                status.state = RuntimeState.stopped
                status.exitCode = code
                status.stoppedAt = _now()
                status.stopReason = StopReason.completed if code == 0 else StopReason.failed
            self._persist(spec, status)
            adopted += 1
        return adopted

    # ── runtime.v1 operations ────────────────────────────────────────────────
    def create(self, spec: WorkloadSpec) -> WorkloadStatus:
        # runtime.v1: workloadId is the caller-assigned IDEMPOTENCY KEY (ADR 0027). A create for a
        # workload that is still starting/running is a TOUCH — return the live status unchanged: no
        # respawn (the docker backend's name-conflict path would force-delete the RUNNING container,
        # the copilot-churn defect), no spec overwrite, no quota charge. This check runs BEFORE the
        # profile resolve and the quota gate so a keep-alive touch can never 400/429 an owner at cap.
        # get() reflects a self-exited workload to stopped (and re-derives the handle across a
        # runtime restart via backend.find), so a stale "running" record falls through to the spawn
        # path, where the backend's stale-name replace reclaims the exited container. `starting` is
        # touched too — the truthful read of a concurrent create mid-spawn; a record crashed stuck
        # in `starting` was already unrecoverable-by-create before this (stop/destroy clears it).
        try:
            existing = self.get(spec.workloadId)
        except KeyError:
            existing = None
        if existing is not None and existing.state in (RuntimeState.starting, RuntimeState.running):
            return existing

        profile = self.profiles.get(spec.profile)
        if profile is None:
            raise ValueError(f"unknown profile: {spec.profile!r}")
        runnable = profile.runnable

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
            # The profile's base_env is the deployment-wide floor (e.g. meeting-bot's BOT_SPEAKER_*
            # tuning, rendered onto the runtime pod by the chart); the per-workload spec.env is
            # layered on top so an explicit spec value always wins. Without this merge the base_env
            # never reaches the spawned pod and chart-set tuning is dead config (issue #771).
            effective_env = {**profile.base_env, **spec.env}
            self._handles[spec.workloadId] = self.backend.start(spec.workloadId, runnable, effective_env)
        except Exception as exc:
            # Record the honest terminal state (persist + emit) FIRST — GET /workloads and the
            # callback stream must still see stopped/start_failed — THEN raise so the API answers a
            # non-201 naming the cause. A caught-and-returned status here is a false "spawned" 201
            # over a workload that never came up (#718): the create response must not read as success.
            status.state = RuntimeState.stopped
            status.stopReason = StopReason.start_failed
            status.stoppedAt = _now()
            self._persist(spec, status)
            self._emit(spec.workloadId, RuntimeState.stopped, stopReason=StopReason.start_failed)
            raise StartFailed(spec.workloadId, str(exc)) from exc
        status.state = RuntimeState.running
        status.startedAt = _now()
        status.ports = {}
        self._persist(spec, status)
        self._emit(spec.workloadId, RuntimeState.running, ports={})
        return status

    def get(self, workload_id: str) -> WorkloadStatus:
        record = self._record(workload_id)
        status = record.status
        # A running record with NO in-process handle (restart) re-derives one from the substrate,
        # so the exit-reflection below stays truthful across a runtime recreate.
        handle = (
            self._handle_for(workload_id)
            if status.state == RuntimeState.running
            else self._handles.get(workload_id)
        )
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
        h = self._handle_for(workload_id)                           # re-derives post-restart handles
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
        h = self._handle_for(workload_id)                           # re-derives post-restart handles
        if h is not None:
            self.backend.cleanup(h)   # raises on an unconfirmed reclaim — destroyed is never a lie
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
