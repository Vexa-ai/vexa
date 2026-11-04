"""runtime.v1 shapes as Pydantic models. The JSON Schema in contracts/runtime.v1 is the SOURCE OF
TRUTH (ADR-0001); these hand-written models are validated against it in tests (no codegen pipeline)."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .pod_scheduling import validate_avoid_nodes


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
    """The workload's resource intent. ONE value per dimension: on Kubernetes ``cpu``/``memoryMb``
    set BOTH the container's request and its limit (Guaranteed QoS) — sealed v1 models no separate
    request/limit semantics. A deployment may give a profile a CPU limit above its request
    (``profiles.ProfileResources``); that is kernel config and never part of this shape. The floors
    mirror the JSON Schema's ``minimum: 0``, so a negative value is rejected at parse time and
    never reaches a backend."""
    model_config = {"extra": "forbid"}
    cpu: Optional[float] = Field(default=None, ge=0)
    memoryMb: Optional[int] = Field(default=None, ge=0)
    gpu: Optional[int] = Field(default=None, ge=0)


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
    #: Nodes the k8s backend should place this workload away from (a preferred anti-affinity): a
    #: meeting's next join attempt after Google Meet refused one on these nodes. Ignored by docker
    #: and process. At most 10, each a node name (pod_scheduling.validate_avoid_nodes).
    avoidNodes: Optional[list[str]] = None

    @field_validator("avoidNodes")
    @classmethod
    def _avoid_nodes(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        return None if value is None else validate_avoid_nodes(value)


class PendingReason(str, Enum):
    """Why a workload the backend accepted is not running yet (runtime.v1 ``PendingReason``)."""
    unschedulable = "unschedulable"   # no node can take it now (k8s: PodScheduled=False/Unschedulable)
    image_pull = "image_pull"         # placed, its image cannot be pulled yet (ErrImagePull/BackOff)
    starting = "starting"             # placed or about to be: image pulling, container being created


class AutoscalerVerdict(str, Enum):
    """The cluster autoscaler's own latest verdict on an unschedulable workload."""
    scale_up = "scale_up"             # TriggeredScaleUp: a node is being added for it
    no_scale_up = "no_scale_up"       # NotTriggerScaleUp: no node group can be grown for it


class Pending(BaseModel):
    """The substrate's placement facts for a workload that is not running yet (k8s: a Pending Pod).
    Facts only — what the scheduler and the autoscaler said — never a verdict on what it means."""
    model_config = {"extra": "forbid"}
    reason: PendingReason
    since: Optional[str] = None
    nominated: Optional[bool] = None
    autoscaler: Optional[AutoscalerVerdict] = None
    detail: Optional[str] = None


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
    #: Present while a running workload still waits for its substrate (k8s backend only).
    pending: Optional[Pending] = None


class RuntimeEvent(BaseModel):
    """The lifecycle callback emitted on each transition."""
    model_config = {"extra": "forbid"}
    workloadId: str
    state: RuntimeState
    at: str
    ports: Optional[dict[str, int]] = None
    exitCode: Optional[int] = None
    stopReason: Optional[StopReason] = None
