"""agent-api — the agent control plane (③ EXECUTION domain).

Front door (P6): import from here, never a deep path. Turns a ``transcript.v1`` input into a
governed action committed to a user workspace (``workspace.v1``), spawning the worker via
``runtime.v1`` (profile ``agent``). Consumes transcript.v1 strictly as a published schema read by
path — never imports meetings code (the ``meetings ⊥ agent`` seam).
"""
from .config import Settings, load_settings
from .core import run
from .models import (
    ActionKind,
    AgentAction,
    AgentRunRequest,
    AgentRunResult,
    WorkspaceWrite,
)
from .ports import RuntimePort, TranscriptSource, WorkspacePort
from .spawn import build_worker_env

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
]
