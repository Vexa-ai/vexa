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

from shared.models import AgentAction, WorkspaceWrite


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
class IdentityPort(Protocol):
    """Mint the per-dispatch SIGNED token (the chain of custody) — identity.v1 DispatchClaims.

    The dispatcher asks identity to mint a short-lived signed token carrying ``(subject, launcher,
    workspace grants, tool grants)`` at dispatch time; the Runtime injects it; every boundary VERIFIES
    it. The agent never mints. The dev adapter signs with a shared key (HS256); k8s uses SPIRE/Keycloak
    behind this same hole.
    """

    def mint(self, subject: str, launcher: str, workspaces: list[dict], tools: list[str]) -> str:
        """Return a signed dispatch token proving these grants; raises on a bad request."""
        ...


@runtime_checkable
class StreamReader(Protocol):
    """Read a dispatch's output Stream ``unit:<id>:out`` (the Stream primitive — a durable redis Stream).

    A relay (agent-api SSE view / the gateway ws) ``XREAD``s the Stream and pushes each UnitEvent to the
    user. Durable + replayable: a reader that reconnects mid-turn catches up. The dev adapter wraps redis
    ``XREAD``; the eval fakes it with a list.
    """

    def read(self, unit_id: str) -> Iterable[dict]:
        """Yield the UnitEvents on ``unit:<unit_id>:out`` as they arrive, until the turn completes."""
        ...


@runtime_checkable
class SchedulerPort(Protocol):
    """Register / list / cancel ``schedule.v1`` jobs in the runtime's durable cron.

    A scheduled routine COMPILES to a schedule.v1 job whose ``request`` POSTs a ``unit.v1`` Invocation
    back to agent-api ``/invocations`` when due. The runtime owns the cron (re-arm, retry, idempotency);
    agent-api only authors jobs — it never runs a timer in-process (P7). The same HTTP edge as RuntimePort.
    """

    def schedule(self, job: dict) -> dict:
        """Register a schedule.v1 job (one-shot ``execute_at`` or re-arming ``cron``); return the job."""
        ...

    def list_jobs(self, *, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        """List scheduled jobs (pending/executing)."""
        ...

    def cancel_job(self, job_id: str) -> Optional[dict]:
        """Cancel a pending job by id; return it, or None if unknown."""
        ...


@runtime_checkable
class AgentDecisionPort(Protocol):
    """Decide WHAT a run does from a (validated) transcript — the LLM seam.

    This is the hole the model plugs into. The core hands a ``transcript.v1`` payload to ``decide``
    and gets back an ``AgentAction`` (the agent's own shape). The default adapter is the existing
    DETERMINISTIC rule (a fixed transcript→meeting-entity mapping); the real Claude adapter — model +
    Read/Write/Edit/Bash tooling over the mounted workspace — slots in HERE behind the same signature,
    OUT OF SCOPE for this increment.

    The point of the seam is governance, not the model: whatever ``decide`` returns, ``core.run``
    re-validates the emitted frontmatter against ``workspace.v1`` before it can touch the user repo
    (P8). A decision port — even a hallucinating LLM — CANNOT bypass the contract.
    """

    def decide(self, payload: dict) -> AgentAction:
        """Given a validated transcript.v1 payload, return the action this run should take."""
        ...


@runtime_checkable
class WorkspaceStoragePort(Protocol):
    """Sync a workspace dir to/from durable object storage (S3/MinIO) — the agent's at-rest memory.

    Mirrors the parent ``workspace.py`` ``aws s3 sync`` up/down: ``sync_down`` hydrates the local
    workspace before a run, ``sync_up`` persists it after. Implementations honor an exclude list (the
    ``.claude/.session`` ephemera the parent excludes) and report the object ops performed. The
    transport (boto3 / a real ``aws s3 sync``) is the adapter's concern; the eval fakes it.
    """

    def sync_down(self, local_dir: str, *, excludes: tuple[str, ...] = ()) -> list[str]:
        """Pull objects from storage into ``local_dir``; return the keys fetched."""
        ...

    def sync_up(self, local_dir: str, *, excludes: tuple[str, ...] = ()) -> list[str]:
        """Push ``local_dir`` to storage (delete extraneous), honoring excludes; return keys put."""
        ...


@runtime_checkable
class VcsPort(Protocol):
    """Push a committed workspace to a per-user GitHub remote over a BROKERED token (P15).

    The token is fetched from the identity ``SecretsPort`` as a redacted ``BrokeredSecret`` and only
    ``reveal()``-ed at the moment the remote URL is assembled — it MUST NEVER be logged raw. This is
    the GitHub-per-user seam: each user's workspace pushes to their own repo under their own scoped
    credential, brokered and audited by identity, never a shared platform key.
    """

    def push(self, local_dir: str, remote_url: str, ref: str) -> str:
        """Push ``ref`` from ``local_dir`` to ``remote_url`` using the brokered token; return the sha."""
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
