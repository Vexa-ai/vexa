"""Test fixtures — the committed transcript.v1 goldens are the spec (P8).

Goldens are loaded BY PATH from ``meetings/contracts/transcript.v1/golden/`` (the published seam),
never by importing meetings code — the same ``meetings ⊥ agent`` boundary the production code keeps.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from shared.gitenv import GIT_REPO_DISCOVERY_VARS

# The suite runs under `pnpm gates`, which the pre-push HOOK runs — and git exports GIT_DIR (and
# friends) into hook descendants. Any test git subprocess inheriting those operates on the REAL
# repo with its tmp cwd as the work tree: `git add -A && git commit` then REWRITES THE BRANCH BEING
# PUSHED (this destroyed a feature branch once — see shared/gitenv.py). Library code scrubs its own
# child envs; this session-level scrub protects the tests' OWN git helpers (the `subprocess.run
# (["git", ...])` lambdas) wholesale. Tests that need one of these vars set it explicitly
# (monkeypatch.setenv), which happens after this runs at import time.
for _var in GIT_REPO_DISCOVERY_VARS:
    os.environ.pop(_var, None)


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


@pytest.fixture(autouse=True)
def _default_subject(monkeypatch):
    """The HTTP tests exercise agent-api with no gateway in front, so set the single-user fallback
    (``VEXA_AGENT_DEFAULT_SUBJECT``) — agent-api derives the subject from it when ``X-User-Id`` is absent.
    Tests that assert per-user *isolation* pass an explicit ``X-User-Id`` header, which always wins (P20)."""
    monkeypatch.setenv("VEXA_AGENT_DEFAULT_SUBJECT", "u_jane")


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path_factory):
    """Point HOME at a per-test temp dir. The claude-code harness rewires ``$HOME/.claude/projects``
    into the workspace (``_link_chat_into_workspace``) — designed for the disposable per-turn
    container HOME. Run on a host, it operated on the developer's real ``~/.claude`` and once
    destroyed every Claude Code session transcript. No test may ever see the real HOME.

    (Git is unaffected: workspaces set ``user.name``/``user.email`` per-repo, never globally.)"""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
