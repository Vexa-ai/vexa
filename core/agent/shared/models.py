"""The agent domain's own shapes (Pydantic).

These are *internal* models — the agent's vocabulary. They are NOT a published contract:
the agent CONSUMES ``meetings/contracts/transcript.v1`` (the seam) and PRODUCES documents that
conform to ``agent/contracts/workspace.v1``. These models are how the core reasons in between.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class ActionKind(str, Enum):
    """What an agent run decided to do. This increment ships only ``upsert_entity`` (the stub);
    the LLM/tooling that would choose richer actions is a marked TODO seam in ``core``."""

    upsert_entity = "upsert_entity"
    noop = "noop"


class WorkspaceWrite(BaseModel):
    """A would-be commit to the user workspace: one entity markdown file + its frontmatter.

    ``frontmatter`` MUST validate against ``workspace.v1`` EntityFrontmatter (the core enforces this
    before the write is emitted). ``path`` follows the workspace template: ``kg/entities/<type>/<slug>.md``.
    """

    model_config = {"extra": "forbid"}
    path: str
    frontmatter: dict
    body: str = ""


class AgentAction(BaseModel):
    """The decision an agent run emits. In this increment it is a deterministic stub derived from
    the transcript; the LLM that would produce it later slots in behind the same shape."""

    model_config = {"extra": "forbid"}
    kind: ActionKind
    summary: str
    write: Optional[WorkspaceWrite] = None


class AgentRunRequest(BaseModel):
    """The input to an agent run: a transcript.v1 payload (validated on the way in) + which
    workspace to act on. The run does not own the transcript shape — it borrows the published seam."""

    model_config = {"extra": "forbid"}
    workload_id: str
    workspace_repo: str
    transcript: dict  # a transcript.v1 Transcription payload; validated by the TranscriptSource port


class AgentRunResult(BaseModel):
    """The outcome of a run: the action taken + whether a workspace write was committed."""

    model_config = {"extra": "forbid"}
    workload_id: str
    action: AgentAction
    committed: bool
