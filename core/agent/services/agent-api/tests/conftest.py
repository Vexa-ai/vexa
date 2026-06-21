"""Test fixtures — the committed transcript.v1 goldens are the spec (P8).

Goldens are loaded BY PATH from ``meetings/contracts/transcript.v1/golden/`` (the published seam),
never by importing meetings code — the same ``meetings ⊥ agent`` boundary the production code keeps.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _repo_root() -> Path:
    marker = Path("meetings/contracts/transcript.v1/golden")
    for parent in Path(__file__).resolve().parents:
        if (parent / marker).is_dir():
            return parent
    raise FileNotFoundError("monorepo root with transcript.v1 goldens not found")


def _golden(name: str) -> dict:
    path = _repo_root() / "meetings/contracts/transcript.v1/golden" / name
    return json.loads(path.read_text())


@pytest.fixture
def transcription_golden() -> dict:
    """A confirmed transcript.v1 Transcription envelope (the agent's input)."""
    return _golden("Transcription.confirmed.json")
