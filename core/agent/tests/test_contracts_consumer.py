"""L1 contract — the agent CONSUMES transcript.v1 + PRODUCES workspace.v1, both by schema-by-path.

Proves the seam: every transcript.v1 golden the agent might receive validates against the schema we
load by path, and the workspace.v1 goldens we'd produce validate too — WITHOUT importing any
meetings code (the ``meetings ⊥ agent`` boundary).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import contracts


def _root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "meetings/contracts/transcript.v1").is_dir():
            return parent
    raise FileNotFoundError("monorepo root not found")


def _load(rel: str) -> dict:
    return json.loads((_root() / rel).read_text())


def test_transcription_golden_conforms():
    contracts.validate_transcription(_load("meetings/contracts/transcript.v1/golden/Transcription.confirmed.json"))


@pytest.mark.parametrize(
    "name",
    ["TranscriptSegment.glow-confirmed.json", "TranscriptSegment.pending.json"],
)
def test_segment_goldens_conform(name):
    contracts.validate_segment(_load(f"meetings/contracts/transcript.v1/golden/{name}"))


@pytest.mark.parametrize(
    "name",
    ["EntityFrontmatter.meeting.json", "EntityFrontmatter.contact.json"],
)
def test_workspace_goldens_conform(name):
    contracts.validate_entity_frontmatter(_load(f"agent/contracts/workspace.v1/golden/{name}"))


def test_no_meetings_internals_imported():
    """The seam is the schema, not the code: importing meetings internals must be impossible."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("meetings")
