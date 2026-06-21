"""The meeting WS → agent bridge (O-AG-3).

The bridge is the meetings → agent control-plane edge. It subscribes the meetings transcript egress
(a bus delivering ``transcript.v1`` payloads), VALIDATES each one at the ``meetings ⊥ agent`` seam
— the transcript.v1 schema is read BY PATH (mirroring ``contracts.py``), NEVER by importing meetings
code — and, on a configured trigger, emits an ``invoke.v1`` Invocation and spawns the ``agent``
workload via the ``RuntimePort`` (env built by ``spawn.build_worker_env`` per ``spec-agent.json``).

A non-conformant payload is DROPPED at the seam: it never reaches the trigger logic or the runtime.
"""
from __future__ import annotations

import logging
from typing import Callable, Iterable, Protocol, runtime_checkable

from jsonschema.exceptions import ValidationError

from . import contracts
from .config import Settings
from .ports import RuntimePort
from .spawn import build_worker_env

logger = logging.getLogger("agent_api.bridge")


@runtime_checkable
class TranscriptBus(Protocol):
    """The meetings transcript egress, as a bus. ``poll`` yields ``transcript.v1`` payloads (dicts)."""

    def poll(self) -> Iterable[dict]:
        """Yield the transcript.v1 payloads currently available on the egress."""
        ...


# A resolver maps a meeting_id → the user's workspace.v1 git repo URL. In production this is a lookup
# (the meeting's owner → their workspace); the eval passes a fixed mapping. Kept as a seam, not baked in.
WorkspaceResolver = Callable[[str], str]


def _invocation_for(payload: dict, resolver: WorkspaceResolver, workspace_ref: str) -> dict | None:
    """Map a VALIDATED transcript.v1 payload to an invoke.v1 Invocation, or None if it is not a trigger.

    The trigger rule (this increment): a ``session_end`` envelope → a ``meeting.completed`` run. A
    ``transcription`` batch is content flowing through, not itself a trigger. The Invocation is
    validated against invoke.v1 before it leaves this function.
    """
    if payload.get("type") != "session_end":
        return None
    contracts.validate_session_end(payload)  # belt-and-suspenders at the seam
    session_uid = payload["session_uid"]
    # SessionEnd carries no meeting_id; the egress keys the run on the session uid.
    meeting_id = payload.get("meeting_id", session_uid)
    invocation = {
        "on": "meeting.completed",
        "meeting": {"meeting_id": str(meeting_id), "session_uid": session_uid},
        "workspace_repo": resolver(str(meeting_id)),
        "workspace_ref": workspace_ref,
    }
    contracts.validate_invocation(invocation)  # the trigger we emit is invoke.v1-conformant
    return invocation


class TranscriptBridge:
    """Wires a transcript egress to runtime.v1 agent spawns, validating at the meetings⊥agent seam."""

    def __init__(
        self,
        settings: Settings,
        runtime: RuntimePort,
        resolver: WorkspaceResolver,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._resolver = resolver
        self.dropped: list[dict] = []   # payloads rejected at the seam (observability)
        self.invoked: list[dict] = []   # invoke.v1 Invocations that fired a spawn

    def handle(self, payload: dict) -> str | None:
        """Process one transcript.v1 payload. Returns the spawned workloadId, or None (no trigger /
        dropped). A non-conformant payload is dropped at the seam — it never reaches the runtime."""
        # 1) Validate at the seam. Transcription validates fully; SessionEnd is checked in the mapper.
        try:
            if payload.get("type") == "transcription":
                contracts.validate_transcription(payload)
            elif payload.get("type") == "session_end":
                contracts.validate_session_end(payload)
            else:
                # unknown shape — not ours to act on, drop it.
                raise ValidationError(f"unexpected transcript.v1 type: {payload.get('type')!r}")
        except ValidationError as e:
            logger.info("bridge DROP non-conformant payload at seam: %s", e.message)
            self.dropped.append(payload)
            return None

        # 2) Is this a trigger?
        try:
            invocation = _invocation_for(payload, self._resolver, self._settings.workspace_ref)
        except ValidationError as e:
            logger.info("bridge DROP — could not build a conformant invoke.v1: %s", e.message)
            self.dropped.append(payload)
            return None
        if invocation is None:
            return None

        # 3) Spawn the agent worker via runtime.v1, env per spec-agent.json.
        env = build_worker_env(self._settings, invocation["workspace_repo"])
        workload_id = f"agent-{invocation['meeting']['session_uid']}"
        acked = self._runtime.spawn(workload_id, self._settings.agent_profile, env)
        self.invoked.append(invocation)
        logger.info(
            "bridge SPAWN agent workload=%s on=%s meeting=%s",
            acked, invocation["on"], invocation["meeting"]["meeting_id"],
        )
        return acked

    def run_once(self, bus: TranscriptBus) -> list[str]:
        """Drain the bus once; return the workloadIds spawned this pass."""
        spawned: list[str] = []
        for payload in bus.poll():
            wid = self.handle(payload)
            if wid is not None:
                spawned.append(wid)
        return spawned
