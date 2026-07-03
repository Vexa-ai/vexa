"""shared — common foundations (config, models, ports, adapters, units, spawn, …).

Convenience re-exports of the commonly used names; prefer importing from the
explicit module (e.g. ``from shared.config import Settings``).
"""
from shared.config import Settings, load_settings
from shared.core import run
from shared.models import (
    ActionKind,
    AgentAction,
    AgentRunRequest,
    AgentRunResult,
    WorkspaceWrite,
)
from shared.ports import RuntimePort, TranscriptSource, WorkspacePort
from shared.spawn import build_worker_env
from shared import models, ports

__all__ = [
    "Settings",
    "load_settings",
    "run",
    "ActionKind",
    "AgentAction",
    "AgentRunRequest",
    "AgentRunResult",
    "WorkspaceWrite",
    "WorkspacePort",
    "RuntimePort",
    "TranscriptSource",
    "build_worker_env",
    "models",
    "ports",
]
