"""The Backend port — the kernel's one dependency on HOW a workload runs. The kernel orchestrates the
lifecycle; a backend just starts/observes/stops a workload. docker/k8s implement this same Protocol."""
from __future__ import annotations

from typing import Optional, Protocol

from .models import Resources
from .profiles import Runnable


class WorkloadHandle:
    """An opaque, backend-specific handle to a started workload."""
    __slots__ = ("id", "_impl")

    def __init__(self, id: str, impl: object) -> None:
        self.id = id
        self._impl = impl


class Backend(Protocol):
    name: str

    def start(
        self,
        workload_id: str,
        runnable: Runnable,
        env: dict[str, str],
        resources: Optional[Resources] = None,
    ) -> WorkloadHandle:
        """Start the workload. ``resources`` is the effective sizing the kernel resolved (the spec's
        own, else the profile's deployment default); ``None`` means unsized — today's behaviour.

        ENFORCEMENT IS PER-SUBSTRATE and deliberately NOT claimed at parity: only the k8s backend
        acts on it (a namespace ResourceQuota can require every container to declare requests and
        limits). docker/process accept it and do not enforce — there is no quota admission on those
        substrates, and a silently-introduced memory limit would OOM-kill live workloads that run
        unbounded today."""
        ...
    def exit_code(self, h: WorkloadHandle) -> Optional[int]:
        """None while running; the exit code once exited."""
        ...
    def terminate(self, h: WorkloadHandle) -> None:
        """Graceful stop (SIGTERM)."""
        ...
    def kill(self, h: WorkloadHandle) -> None:
        """Force stop (SIGKILL)."""
        ...
    def cleanup(self, h: WorkloadHandle) -> None:
        """Reclaim resources (destroy)."""
        ...
