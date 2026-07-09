"""O-AG-1 — the LLM-governance loop (FakeLLM only; NO real model).

These evals prove the GOVERNANCE around the decider, not the decider. The LLM is the untrusted
party: ``core.run`` accepts any ``AgentDecisionPort`` (the model seam), but RE-validates whatever it
returns against ``workspace.v1`` before a write can touch the user repo (P8). A FakeLLM lets us
script malicious / non-conformant / noop / valid decisions and assert the seam holds.

The key assertion: a FakeLLM returning NON-CONFORMANT workspace.v1 frontmatter is REJECTED at the
``validate_entity_frontmatter`` seam — the model cannot bypass the contract.
"""
from __future__ import annotations

import pytest
from jsonschema.exceptions import ValidationError

from shared.core import run
from shared.models import ActionKind, AgentAction, AgentRunRequest, WorkspaceWrite

from .fakes import FakeLLM, FakeWorkspace


def _request(transcript: dict) -> AgentRunRequest:
    return AgentRunRequest(
        workload_id="agent-run-llm",
        workspace_repo="https://git.example.com/acme/company-memory.git",
        transcript=transcript,
    )


# A minimal conformant transcript.v1 Transcription — the decider ignores it (FakeLLM is scripted),
# but the core still validates it at the meetings⊥agent seam on the way in.
_TRANSCRIPT = {
    "type": "transcription",
    "session_uid": "s",
    "meeting_id": "7",
    "segments": [
        {
            "segment_id": "s:ch-0:1:0",
            "speaker": "Alice",
            "text": "hi",
            "start": 0.0,
            "end": 1.0,
            "completed": True,
        }
    ],
}


def test_llm_non_conformant_frontmatter_is_rejected_at_the_seam():
    """THE KEY ASSERTION: a hallucinating LLM emits frontmatter missing required workspace.v1 keys
    (no ``type``/``title``); the governance seam rejects it before any write reaches the workspace."""
    bad_action = AgentAction(
        kind=ActionKind.upsert_entity,
        summary="malicious: write a non-conformant entity",
        # missing the required `type` and `title` → violates workspace.v1 EntityFrontmatter
        write=WorkspaceWrite(path="kg/entities/x/evil.md", frontmatter={"id": "evil"}, body="x"),
    )
    llm = FakeLLM(bad_action)
    ws = FakeWorkspace()

    with pytest.raises(ValidationError):
        run(_request(_TRANSCRIPT), ws, decider=llm)

    # the contract held: nothing was written or committed despite the LLM "deciding" to
    assert ws.files == {}
    assert ws.commits == []


def test_llm_noop_commits_nothing():
    """A ``noop`` decision from the LLM touches the workspace not at all."""
    llm = FakeLLM(AgentAction(kind=ActionKind.noop, summary="nothing to do"))
    ws = FakeWorkspace()

    result = run(_request(_TRANSCRIPT), ws, decider=llm)

    assert result.action.kind is ActionKind.noop
    assert result.committed is False
    assert ws.files == {} and ws.commits == []


def test_llm_valid_decision_commits_exactly_one_entity():
    """A conformant decision flows through the seam and commits exactly one entity."""
    good_action = AgentAction(
        kind=ActionKind.upsert_entity,
        summary="upsert contact entity from the meeting",
        write=WorkspaceWrite(
            path="kg/entities/contact/alice.md",
            frontmatter={"type": "contact", "id": "alice", "title": "Alice"},
            body="met in meeting 7",
        ),
    )
    llm = FakeLLM(good_action)
    ws = FakeWorkspace()

    result = run(_request(_TRANSCRIPT), ws, decider=llm)

    assert result.committed is True
    assert list(ws.files) == ["kg/entities/contact/alice.md"]
    assert ws.commits == ["upsert contact entity from the meeting"]
    # the LLM was actually consulted with the validated transcript
    assert llm.calls == [_TRANSCRIPT]
