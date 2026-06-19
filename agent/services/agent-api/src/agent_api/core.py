"""The agent-run core — transcript.v1 → governed action → (would-)commit to the workspace.

This is the inner hexagon: it depends only on the ports (``WorkspacePort``, ``RuntimePort``) and
the contract validators, never on a transport. That is what lets the L2 unit test drive it with
in-memory fakes (ARCHITECTURE.md §5).

THIS INCREMENT proves the contract wiring, not the LLM:
  1. read a transcript.v1 payload (validated at the seam),
  2. derive a DETERMINISTIC stub action (an upsert of a `meeting` entity), and
  3. emit a workspace.v1-conformant write + commit it through the WorkspacePort.

>>> TODO(LLM seam): the deterministic ``_decide_action`` below is the placeholder for the
    LLM + tooling loop (Claude + Read/Write/Edit/Bash tools over the mounted workspace). It slots
    in behind the exact same ``AgentAction`` shape — the contract wiring proved here does not change.
"""
from __future__ import annotations

import re

from . import contracts
from .models import (
    ActionKind,
    AgentAction,
    AgentRunRequest,
    AgentRunResult,
    WorkspaceWrite,
)
from .ports import RuntimePort, WorkspacePort


def _slug(text: str) -> str:
    """A filesystem-safe slug for the entity path (kg/entities/<type>/<slug>.md)."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "untitled"


def _decide_action(request: AgentRunRequest) -> AgentAction:
    """Derive the stub action from the transcript. >>> TODO: replace with the LLM/tooling loop.

    The deterministic rule: a Transcription opens/updates a ``meeting`` entity whose title is the
    meeting_id and whose body is the concatenated speaker-attributed text. The frontmatter is built
    to satisfy workspace.v1 EntityFrontmatter; ``run()`` validates it against the schema before write.
    """
    payload = request.transcript
    segments = list(contracts.iter_segments(payload))  # validates transcript.v1 at the seam
    if not segments:
        return AgentAction(kind=ActionKind.noop, summary="transcript had no segments")

    meeting_id = str(payload.get("meeting_id", "unknown"))
    entity_id = f"meeting-{meeting_id}"
    body = "\n".join(f"- **{s['speaker']}**: {s['text']}" for s in segments)
    frontmatter = {
        "type": "meeting",
        "id": entity_id,
        "title": f"Meeting {meeting_id}",
        "tags": ["transcript"],
    }
    write = WorkspaceWrite(
        path=f"kg/entities/meeting/{_slug(entity_id)}.md",
        frontmatter=frontmatter,
        body=body,
    )
    return AgentAction(
        kind=ActionKind.upsert_entity,
        summary=f"upsert meeting entity {entity_id} from {len(segments)} segment(s)",
        write=write,
    )


def run(
    request: AgentRunRequest,
    workspace: WorkspacePort,
    *,
    ref: str = "main",
    runtime: RuntimePort | None = None,
) -> AgentRunResult:
    """Execute one agent run end-to-end against the ports.

    Wiring proved here: transcript.v1 (consumed, validated) → AgentAction (the agent's own shape)
    → workspace.v1 (produced, validated) → commit via WorkspacePort. The RuntimePort is accepted so
    a composition root can spawn the run as a worker; in-process it is optional.
    """
    workspace.clone(request.workspace_repo, ref)
    action = _decide_action(request)

    committed = False
    if action.kind is ActionKind.upsert_entity and action.write is not None:
        # The emitted document MUST conform to workspace.v1 before it touches the user repo (P8).
        contracts.validate_entity_frontmatter(action.write.frontmatter)
        workspace.write(action.write)
        commit_id = workspace.commit(action.summary)
        committed = bool(commit_id)

    return AgentRunResult(workload_id=request.workload_id, action=action, committed=committed)
