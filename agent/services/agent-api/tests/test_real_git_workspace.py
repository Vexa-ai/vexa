"""O-AG-2 — the ``RealGitWorkspace`` adapter against REAL git on a local temp repo (no network).

Proves the ``WorkspacePort`` contract end-to-end with real ``git``:
  clone → write a workspace.v1-conformant entity → commit returns a real sha → read round-trips,
and the no-op contract: an empty change → ``commit`` returns ``""``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_api import contracts
from agent_api.adapters import RealGitWorkspace, parse_entity
from agent_api.models import WorkspaceWrite


@pytest.fixture
def origin_repo(tmp_path: Path) -> str:
    """A local source repo (the 'user workspace') with one commit — the clone source, no network."""
    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=origin, check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.name", "seed")
    run("config", "user.email", "seed@example.com")
    (origin / "README.md").write_text("# company-memory\n")
    run("add", "-A")
    run("commit", "-m", "seed")
    return str(origin)


def _entity() -> WorkspaceWrite:
    fm = {"type": "meeting", "id": "meeting-42", "title": "Meeting 42", "tags": ["transcript"]}
    contracts.validate_entity_frontmatter(fm)  # the write is workspace.v1-conformant by construction
    return WorkspaceWrite(path="kg/entities/meeting/meeting-42.md", frontmatter=fm, body="- Alice: hi")


def test_clone_write_commit_read_roundtrip(tmp_path: Path, origin_repo: str):
    ws = RealGitWorkspace(tmp_path / "work")
    ws.clone(origin_repo, "main")

    write = _entity()
    ws.write(write)
    sha = ws.commit("upsert meeting entity meeting-42")

    # commit returns a real 40-hex sha
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)
    # git agrees the commit landed
    log = subprocess.run(
        ["git", "log", "--format=%H %s", "-1"],
        cwd=tmp_path / "work", check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert log == f"{sha} upsert meeting entity meeting-42"

    # read round-trips the entity: frontmatter + body survive the markdown serialization
    text = ws.read(write.path)
    assert text is not None
    fm, body = parse_entity(text)
    assert fm == write.frontmatter
    assert body == write.body
    # and the round-tripped frontmatter still conforms to workspace.v1
    contracts.validate_entity_frontmatter(fm)


def test_empty_change_commit_is_noop_empty_string(tmp_path: Path, origin_repo: str):
    """The no-op contract: nothing staged → ``commit`` returns ``""`` (mirrors real git)."""
    ws = RealGitWorkspace(tmp_path / "work")
    ws.clone(origin_repo, "main")
    assert ws.commit("nothing to commit") == ""


def test_read_absent_path_returns_none(tmp_path: Path, origin_repo: str):
    ws = RealGitWorkspace(tmp_path / "work")
    ws.clone(origin_repo, "main")
    assert ws.read("kg/entities/contact/nobody.md") is None
