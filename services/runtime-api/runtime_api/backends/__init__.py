"""Backend abstraction for container orchestration.

All backends implement the same interface — the API layer doesn't know
whether containers run as Docker containers, K8s pods, or child processes.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional


@dataclass
class ContainerSpec:
    """Specification for creating a container."""

    name: str
    image: str
    command: list[str] | None = None
    env: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    ports: dict[str, dict] = field(default_factory=dict)
    mounts: list[str] = field(default_factory=list)
    network: str | None = None
    shm_size: int = 0
    auto_remove: bool = False
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    gpu: bool = False
    gpu_type: str | None = None  # "nvidia", "vaapi"
    node_selector: dict[str, str] = field(default_factory=dict)
    working_dir: str | None = None
    k8s_overrides: dict = field(default_factory=dict)  # opaque K8s-specific: tolerations, affinity, etc.


@dataclass
class ContainerInfo:
    """Container status information returned by inspect/list."""

    id: str
    name: str
    status: str  # running, exited, created, pending, unknown, failed
    exit_code: int | None = None
    ports: dict[str, int] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    created_at: float | None = None
    image: str | None = None


class Backend(abc.ABC):
    """Abstract base class for container backends."""

    @abc.abstractmethod
    async def create(self, spec: ContainerSpec) -> str:
        """Create and start a container. Returns container/pod ID."""

    @abc.abstractmethod
    async def stop(self, name: str, timeout: int = 10) -> bool:
        """Stop a container. Returns True if stopped or already gone."""

    @abc.abstractmethod
    async def remove(self, name: str) -> bool:
        """Remove a stopped container. Returns True if removed or not found."""

    @abc.abstractmethod
    async def inspect(self, name: str) -> Optional[ContainerInfo]:
        """Get container status. Returns None if not found."""

    @abc.abstractmethod
    async def list(self, labels: dict[str, str] | None = None) -> list[ContainerInfo]:
        """List containers matching optional label filters."""

    @abc.abstractmethod
    async def exec(self, name: str, cmd: list[str]) -> AsyncIterator[bytes]:
        """Execute a command inside a running container. Yields output bytes."""

    async def startup(self) -> None:
        """Called once on API startup. Override for backend initialization."""

    async def shutdown(self) -> None:
        """Called once on API shutdown. Override for cleanup."""

    async def listen_events(self, on_exit: callable) -> None:
        """Listen for container exit events. Override for event-driven backends.

        Args:
            on_exit: async callback(name, exit_code) called when a managed container exits.
        """
