"""The agent-run core — transcript.v1 → governed action → (would-)commit to the workspace.

This is the inner hexagon: it depends only on the ports (``WorkspacePort``, ``RuntimePort``,
``AgentDecisionPort``) and the contract validators, never on a transport. That is what lets the L2
unit test drive it with in-memory fakes (ARCHITECTURE.md §5).

The wiring this core proves is GOVERNANCE around the decider, not the decider itself:
  1. read a transcript.v1 payload (validated at the seam),
  2. ask an ``AgentDecisionPort`` what to do (default: a DETERMINISTIC rule; the real LLM slots in
     behind the same seam, OUT OF SCOPE here), and
  3. re-validate the emitted write against workspace.v1 BEFORE it can touch the user repo — a
     decider (even a hallucinating LLM) cannot bypass the contract (P8) — then commit it.
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
from .ports import AgentDecisionPort, RuntimePort, WorkspacePort


def _slug(text: str) -> str:
    """A filesystem-safe slug for the entity path (kg/entities/<type>/<slug>.md)."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "untitled"


class DeterministicDecider(AgentDecisionPort):
    """The default ``AgentDecisionPort`` — the original fixed rule, now an explicit adapter.

    A Transcription opens/updates a ``meeting`` entity whose title is the meeting_id and whose body
    is the concatenated speaker-attributed text. The frontmatter is built to satisfy workspace.v1
    EntityFrontmatter; ``run()`` re-validates it against the schema before write regardless.
    """

    def decide(self, payload: dict) -> AgentAction:
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
    decider: AgentDecisionPort | None = None,
    runtime: RuntimePort | None = None,
) -> AgentRunResult:
    """Execute one agent run end-to-end against the ports.

    Wiring proved here: transcript.v1 (consumed, validated) → an ``AgentDecisionPort`` decides →
    workspace.v1 (produced, RE-validated) → commit via WorkspacePort. The decider is the LLM seam;
    it defaults to the deterministic rule so existing behavior is unchanged. The RuntimePort is
    accepted so a composition root can spawn the run as a worker; in-process it is optional.

    The validation at ``validate_entity_frontmatter`` is the GOVERNANCE seam: it runs on whatever
    the decider returned, so no decision — deterministic or LLM — can write a non-conformant entity.
    """
    if decider is None:
        decider = DeterministicDecider()

    workspace.clone(request.workspace_repo, ref)
    action = decider.decide(request.transcript)

    committed = False
    if action.kind is ActionKind.upsert_entity and action.write is not None:
        # The emitted document MUST conform to workspace.v1 before it touches the user repo (P8).
        # This runs for EVERY decider, so a hallucinating LLM cannot bypass the contract.
        contracts.validate_entity_frontmatter(action.write.frontmatter)
        workspace.write(action.write)
        commit_id = workspace.commit(action.summary)
        committed = bool(commit_id)

    return AgentRunResult(workload_id=request.workload_id, action=action, committed=committed)
