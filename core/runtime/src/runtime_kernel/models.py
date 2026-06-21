"""runtime.v1 shapes as Pydantic models. The JSON Schema in contracts/runtime.v1 is the SOURCE OF
TRUTH (ADR-0001); these hand-written models are validated against it in tests (no codegen pipeline)."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class RuntimeState(str, Enum):
    starting = "starting"
    running = "running"
    stopping = "stopping"
    stopped = "stopped"
    destroyed = "destroyed"


class StopReason(str, Enum):
    completed = "completed"
    stopped = "stopped"
    idle_timeout = "idle_timeout"
    failed = "failed"
    oom = "oom"
    start_failed = "start_failed"
    max_lifetime = "max_lifetime"


class BackendKind(str, Enum):
    docker = "docker"
    k8s = "k8s"
    process = "process"


class Resources(BaseModel):
    model_config = {"extra": "forbid"}
    cpu: Optional[float] = None
    memoryMb: Optional[int] = None
    gpu: Optional[int] = None


class WorkloadSpec(BaseModel):
    """create() input — the kernel runs `profile` + `env` opaquely (P11)."""
    model_config = {"extra": "forbid"}
    workloadId: str
    profile: str
    env: dict[str, str]
    callbackUrl: Optional[str] = None
    resources: Optional[Resources] = None
    idleTimeoutSec: Optional[int] = None
    maxLifetimeSec: Optional[int] = None
    backend: Optional[BackendKind] = None


class WorkloadStatus(BaseModel):
    model_config = {"extra": "forbid"}
    workloadId: str
    profile: str
    state: RuntimeState
    backend: BackendKind
    ports: Optional[dict[str, int]] = None
    startedAt: Optional[str] = None
    stoppedAt: Optional[str] = None
    exitCode: Optional[int] = None
    stopReason: Optional[StopReason] = None
    node: Optional[str] = None


class RuntimeEvent(BaseModel):
    """The lifecycle callback emitted on each transition."""
    model_config = {"extra": "forbid"}
    workloadId: str
    state: RuntimeState
    at: str
    ports: Optional[dict[str, int]] = None
    exitCode: Optional[int] = None
    stopReason: Optional[StopReason] = None
