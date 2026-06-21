"""O-AG-2 — the GitHub-per-user ``VcsPort`` over a BROKERED token (P15).

Proves: a push uses the token fetched from the identity ``SecretsPort`` (audited), the push lands
in the target repo, and the raw token NEVER appears in a log line or the persisted git remote.
The remote here is a bare LOCAL repo (no network); the token is brokered via a fake that mirrors
identity's redacting ``BrokeredSecret`` shape.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from agent_api.adapters import GitHubVcs, RealGitWorkspace
from agent_api.models import WorkspaceWrite

from .fakes import FakeSecretsBroker

TOKEN = "ghp_SUPERSECRETtoken1234567890"


@pytest.fixture
def origin_repo(tmp_path: Path) -> str:
    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=origin, check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.name", "seed")
    run("config", "user.email", "seed@example.com")
    (origin / "README.md").write_text("# memory\n")
    run("add", "-A")
    run("commit", "-m", "seed")
    return str(origin)


@pytest.fixture
def remote_repo(tmp_path: Path) -> str:
    """A bare local repo standing in for the user's GitHub remote (no network)."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    # use a real URL-like form so the adapter exercises the token-in-URL path
    return f"file://{remote}"


def test_push_uses_brokered_token_and_lands(tmp_path: Path, origin_repo: str, remote_repo: str):
    ws = RealGitWorkspace(tmp_path / "work")
    ws.clone(origin_repo, "main")
    ws.write(WorkspaceWrite(
        path="kg/entities/meeting/m1.md",
        frontmatter={"type": "meeting", "id": "m1", "title": "M1"},
        body="hi",
    ))
    ws.commit("add m1")

    broker = FakeSecretsBroker({"workspace_git.token": TOKEN})
    vcs = GitHubVcs(broker, subject="user-7")
    sha = vcs.push(str(tmp_path / "work"), remote_repo, "main")

    # the broker was consulted with the right subject/scope (audit records metadata, never the value)
    assert broker.audit == [("user-7", "workspace_git.token", "repo:push")]
    # the push landed: the bare remote now has our commit at main
    remote_path = remote_repo.replace("file://", "")
    remote_head = subprocess.run(
        ["git", "rev-parse", "main"], cwd=remote_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert remote_head == sha


def test_token_never_logged_or_persisted(tmp_path: Path, origin_repo: str, remote_repo: str, caplog):
    ws = RealGitWorkspace(tmp_path / "work")
    ws.clone(origin_repo, "main")
    ws.write(WorkspaceWrite(
        path="kg/entities/meeting/m1.md",
        frontmatter={"type": "meeting", "id": "m1", "title": "M1"},
        body="hi",
    ))
    ws.commit("add m1")

    broker = FakeSecretsBroker({"workspace_git.token": TOKEN})
    vcs = GitHubVcs(broker, subject="user-7")
    with caplog.at_level(logging.DEBUG):
        vcs.push(str(tmp_path / "work"), remote_repo, "main")

    # P15: the raw token appears NOWHERE in the captured logs
    assert TOKEN not in caplog.text
    assert "***REDACTED***" in caplog.text  # the redacted form is what got logged

    # ...and the token was stripped from the persisted remote (can't leak to the repo/object store)
    persisted = subprocess.run(
        ["git", "remote", "get-url", "vexa-vcs"],
        cwd=tmp_path / "work", check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert TOKEN not in persisted
    assert persisted == remote_repo
