"""L2 unit — the agent-run core, every port mocked (ARCHITECTURE.md §5).

Feeds a transcript.v1 GOLDEN through the core with in-memory fakes and asserts the full contract
wiring: the transcript is read (validated at the seam), the expected stub action is emitted, and a
workspace.v1-conformant document is (would-be) committed to the workspace per the template layout.
"""
from __future__ import annotations

import pytest
from jsonschema.exceptions import ValidationError

import contracts
from shared.core import run
from shared.models import ActionKind, AgentRunRequest

from .fakes import FakeWorkspace


def _request(transcript: dict) -> AgentRunRequest:
    return AgentRunRequest(
        workload_id="agent-run-1",
        workspace_repo="https://git.example.com/acme/company-memory.git",
        transcript=transcript,
    )


def test_run_reads_transcript_and_commits_workspace_entity(transcription_golden):
    ws = FakeWorkspace()
    result = run(_request(transcription_golden), ws, ref="main")

    # 1) the core spawned a clone of the target workspace at the requested ref
    assert ws.cloned == ("https://git.example.com/acme/company-memory.git", "main")

    # 2) it read the transcript.v1 golden and emitted the expected stub action
    assert result.action.kind is ActionKind.upsert_entity
    assert result.action.write is not None
    # meeting_id "42" in the golden → a meeting entity at the workspace-template path
    assert result.action.write.path == "kg/entities/meeting/meeting-42.md"
    assert result.action.write.frontmatter["type"] == "meeting"
    assert result.action.write.frontmatter["id"] == "meeting-42"
    # the speaker-attributed text made it into the body
    assert "Alice" in result.action.write.body and "hello world" in result.action.write.body

    # 3) it would-write to the workspace + committed
    assert result.committed is True
    assert ws.files["kg/entities/meeting/meeting-42.md"] is result.action.write
    assert ws.commits == [result.action.summary]


def test_emitted_frontmatter_conforms_to_workspace_v1(transcription_golden):
    """The document the core would commit validates against the workspace.v1 schema (P8)."""
    ws = FakeWorkspace()
    result = run(_request(transcription_golden), ws)
    assert result.action.write is not None
    # would raise ValidationError if the emitted frontmatter were non-conformant
    contracts.validate_entity_frontmatter(result.action.write.frontmatter)


def test_empty_transcript_is_noop_no_commit():
    ws = FakeWorkspace()
    empty = {"type": "transcription", "session_uid": "s", "meeting_id": "1", "segments": []}
    result = run(_request(empty), ws)
    assert result.action.kind is ActionKind.noop
    assert result.committed is False
    assert ws.commits == []


def test_non_conformant_transcript_is_rejected_at_the_seam():
    """A payload that violates transcript.v1 never reaches the action logic (the seam rejects it)."""
    ws = FakeWorkspace()
    bad = {"type": "transcription", "segments": [{"speaker": "x"}]}  # missing required fields
    with pytest.raises(ValidationError):
        run(_request(bad), ws)
