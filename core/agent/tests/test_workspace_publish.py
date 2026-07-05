"""workspace_publish — publish a vexa-born workspace to GitHub (counterpart of attach/swap).

Proves the publish lifecycle on REAL git over a LOCAL bare repo as the push target (no network;
the GitHub creation call is an injected fake, mirroring how workspace_attach tests inject CloneFn):
  create+push full history → re-publish is a plain push → divergence fails loud (no force push) →
  attached workspaces are refused → tokens never persist and never leak into errors (P15).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from control_plane.workspace_attach import swap_workspace
from control_plane.workspace_publish import (
    PUBLISH_REMOTE,
    PublishError,
    RepoExistsError,
    publish_workspace,
)

TOKEN = "ghp_SECRET_token_123"


def _run(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _workspace(root: Path, subject: str, commits: int = 2) -> Path:
    """A vexa-born (seeded-style) active workspace with real history at <root>/<subject>."""
    ws = root / subject
    ws.mkdir(parents=True)
    _run(ws, "init", "-q", "-b", "main")
    _run(ws, "config", "user.email", "t@t")
    _run(ws, "config", "user.name", "t")
    for i in range(commits):
        (ws / "CLAUDE.md").write_text(f"root v{i}\n")
        _run(ws, "add", "-A")
        _run(ws, "commit", "-q", "-m", f"c{i}")
    return ws


def _bare(path: Path) -> Path:
    path.mkdir(parents=True)
    _run(path, "init", "-q", "--bare", "-b", "main")
    return path


def test_publish_creates_repo_and_pushes_full_history(tmp_path):
    """The create path: the injected creator is called with the caller's args and its returned URL is
    pushed to — full history, head sha returned, repo_url token-free."""
    root = tmp_path / "workspaces"
    ws = _workspace(root, "u1", commits=3)
    bare = _bare(tmp_path / "remote.git")
    calls: list[tuple] = []

    def fake_create(name, private, token, org):
        calls.append((name, private, token, org))
        return str(bare)

    res = publish_workspace(root, "u1", token=TOKEN, repo_name="my-workspace",
                            private=True, create_repo=fake_create)

    assert calls == [("my-workspace", True, TOKEN, None)]
    assert res.created is True and res.pushed_ref == "main"
    assert res.head_sha == _run(ws, "rev-parse", "HEAD")
    # FULL history landed on the remote
    assert _run(bare, "rev-parse", "main") == res.head_sha
    assert _run(bare, "rev-list", "--count", "main") == "3"


def test_publish_to_remote_url_skips_creation(tmp_path):
    """remote_url given → no creation call, plain push to the pre-created (empty) repo."""
    root = tmp_path / "workspaces"
    _workspace(root, "u1")
    bare = _bare(tmp_path / "pre.git")

    def never_create(*a):  # pragma: no cover - must not run
        raise AssertionError("create_repo must not be called when remote_url is given")

    res = publish_workspace(root, "u1", token=TOKEN, remote_url=str(bare), create_repo=never_create)
    assert res.created is False
    assert _run(bare, "rev-parse", "main") == res.head_sha


def test_republish_same_remote_is_plain_push(tmp_path):
    """Idempotent-ish: publish, commit more, publish again to the same remote — a fast-forward push."""
    root = tmp_path / "workspaces"
    ws = _workspace(root, "u1")
    bare = _bare(tmp_path / "remote.git")
    publish_workspace(root, "u1", token=TOKEN, remote_url=str(bare))

    (ws / "more.md").write_text("more\n")
    _run(ws, "add", "-A")
    _run(ws, "commit", "-q", "-m", "more")

    res = publish_workspace(root, "u1", token=TOKEN, remote_url=str(bare))
    assert _run(bare, "rev-parse", "main") == res.head_sha == _run(ws, "rev-parse", "HEAD")


def test_divergence_fails_loud_never_force(tmp_path):
    """The remote grew history the workspace doesn't have → a clear error; the remote's commit
    survives (NO force push, ever)."""
    root = tmp_path / "workspaces"
    _workspace(root, "u1")
    bare = _bare(tmp_path / "remote.git")
    # seed the remote with foreign history
    other = tmp_path / "other"
    other.mkdir()
    _run(other, "init", "-q", "-b", "main")
    _run(other, "config", "user.email", "o@o")
    _run(other, "config", "user.name", "o")
    (other / "X").write_text("foreign\n")
    _run(other, "add", "-A")
    _run(other, "commit", "-q", "-m", "foreign")
    _run(other, "push", "-q", str(bare), "main")
    foreign_sha = _run(bare, "rev-parse", "main")

    with pytest.raises(PublishError):
        publish_workspace(root, "u1", token=TOKEN, remote_url=str(bare))
    assert _run(bare, "rev-parse", "main") == foreign_sha  # remote untouched


def test_token_never_persisted_and_errors_redacted(tmp_path):
    """P15: after a publish the workspace's git config carries NO token; the dedicated remote exists
    token-free and origin was never touched; a push failure's message is token-redacted."""
    root = tmp_path / "workspaces"
    ws = _workspace(root, "u1")
    _run(ws, "remote", "add", "origin", "https://example.com/keep.git")
    bare = _bare(tmp_path / "remote.git")

    publish_workspace(root, "u1", token=TOKEN, remote_url=f"file://{bare}")

    cfg = (ws / ".git" / "config").read_text()
    assert TOKEN not in cfg
    assert _run(ws, "remote", "get-url", "origin") == "https://example.com/keep.git"
    assert _run(ws, "remote", "get-url", PUBLISH_REMOTE) == f"file://{bare}"

    # a failing push (bogus remote) surfaces a token-free error
    with pytest.raises(PublishError) as ei:
        publish_workspace(root, "u1", token=TOKEN, remote_url=str(tmp_path / "nope.git"))
    assert TOKEN not in str(ei.value)


def test_create_failure_errors_are_token_free(tmp_path):
    """Creator failures (already-exists and generic) surface redacted, actionable errors."""
    root = tmp_path / "workspaces"
    _workspace(root, "u1")

    def exists(*a):
        raise RepoExistsError("a repository named 'w' already exists under your account — pick "
                              "another name, or pass its URL as remote_url to push into it")

    with pytest.raises(RepoExistsError) as ei:
        publish_workspace(root, "u1", token=TOKEN, repo_name="w", create_repo=exists)
    assert "already exists" in str(ei.value) and TOKEN not in str(ei.value)


def test_attached_workspace_is_refused(tmp_path):
    """Vexa-born only: an ATTACHED external repo already has a home — publish refuses it."""
    root = tmp_path / "workspaces"
    ws = _workspace(root, "u1")
    origin = tmp_path / "external"
    origin.mkdir()
    _run(origin, "init", "-q", "-b", "main")
    _run(origin, "config", "user.email", "t@t")
    _run(origin, "config", "user.name", "t")
    (origin / "CLAUDE.md").write_text("CUSTOM ROOT")
    _run(origin, "add", "-A")
    _run(origin, "commit", "-q", "-m", "seed")
    swap_workspace(root, "u1", str(origin), "main")   # active workspace is now the attached repo

    with pytest.raises(PublishError) as ei:
        publish_workspace(root, "u1", token=TOKEN, repo_name="w",
                          create_repo=lambda *a: (_ for _ in ()).throw(AssertionError))
    assert "attached" in str(ei.value)


def test_bad_inputs_are_value_errors(tmp_path):
    """Missing token / bad repo_name / no workspace / no commits fail loud with clear messages."""
    root = tmp_path / "workspaces"
    with pytest.raises(ValueError):
        publish_workspace(root, "u1", token="  ", repo_name="w")   # no token
    with pytest.raises(PublishError):
        publish_workspace(root, "u1", token=TOKEN, repo_name="w")  # no workspace yet
    _workspace(root, "u2")
    with pytest.raises(ValueError):
        publish_workspace(root, "u2", token=TOKEN, repo_name="bad name!")  # invalid repo name
    with pytest.raises(ValueError):
        publish_workspace(root, "u2", token=TOKEN)  # neither repo_name nor remote_url
