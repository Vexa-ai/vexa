"""Ports (Hexagonal / Ports & Adapters — P5).

The core depends ONLY on these protocols — "holes" that adapters fill at the composition root.
A port is what lets the L2 unit test exist (ARCHITECTURE.md §5): every external thing the core
touches (a user's git repo, the runtime kernel, the transcript bus) is reached through one of
these, so the core stays offline-provable with in-memory fakes.

Each port is a pure ``Protocol`` — no transport, no I/O, no third-party import. The real adapters
(git, an HTTP client to runtime.v1, a redis transcript stream) live elsewhere and are wired in by
the service entrypoint; none of them is imported here.
"""
from __future__ import annotations

from typing import Iterable, Optional, Protocol, runtime_checkable

from .models import WorkspaceWrite


@runtime_checkable
class WorkspacePort(Protocol):
    """Clone / read / commit a USER git repo per ``workspace.v1``.

    The workspace is the agent's durable memory — a user-owned git repo (data, not platform code).
    The agent clones it, reads existing entities, and commits new/updated ones. Access/sharing and
    envelope encryption are deferred behind this same port (ADR-0003, P15).
    """

    def clone(self, repo_url: str, ref: str) -> None:
        """Make the workspace available locally at ``ref`` (idempotent)."""
        ...

    def read(self, path: str) -> Optional[str]:
        """Return the text at ``path`` within the workspace, or None if absent."""
        ...

    def write(self, write: WorkspaceWrite) -> None:
        """Stage an entity document (frontmatter + body) at ``write.path``."""
        ...

    def commit(self, message: str) -> str:
        """Commit staged changes; return the commit id. No-op commits return ``""``."""
        ...


@runtime_checkable
class RuntimePort(Protocol):
    """Spawn / await an agent worker via ``runtime.v1`` (profile ``agent``).

    The control plane never runs the worker in-process — it asks the runtime kernel to spawn it as
    an ephemeral, stateless workload (P7). The workspace repo URL + a scoped identity token travel
    in the worker's ``env`` (see golden ``runtime.v1/spec-agent.json``), never as a kernel concept.
    """

    def spawn(self, workload_id: str, profile: str, env: dict[str, str]) -> str:
        """Create a workload; return the workloadId the kernel acknowledged."""
        ...

    def await_done(self, workload_id: str, timeout_sec: float = 0.0) -> str:
        """Block until the workload reaches a terminal ``runtime.v1`` state; return that state."""
        ...


@runtime_checkable
class TranscriptSource(Protocol):
    """Yield validated ``transcript.v1`` segments.

    This is the ``meetings ⊥ agent`` seam: the agent consumes transcript.v1 by SCHEMA (read by path),
    never by importing meetings code. An adapter validates each payload against the published JSON
    Schema before it reaches the core, so the core only ever sees conformant segments.
    """

    def segments(self, payload: dict) -> Iterable[dict]:
        """Validate a transcript.v1 payload and yield its ``TranscriptSegment`` objects in order."""
        ...
