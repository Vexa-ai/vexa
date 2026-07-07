"""workspace_git_sync — GitHub push / pull / status for a workspace with a home remote.

Proves the sync lifecycle on REAL git over LOCAL repos (no network): a clone keeps a token-free
``origin`` → push a local commit (ff) → pull a remote commit (ff) → a divergence is REFUSED (no
merge/rebase/force) → ahead/behind status is computed locally → a home-less workspace reports none →
tokens never leak into error messages (P15).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from control_plane.workspace_git_sync import (
    RemoteSyncError,
    home_remote,
    pull_origin,
    push_origin,
    remote_status,
)

TOKEN = "ghp_SECRET_token_123"


def _run(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _bare(path: Path) -> Path:
    path.mkdir(parents=True)
    _run(path, "init", "-q", "--bare", "-b", "main")
    return path


def _clone(remote: Path, dest: Path, *, seed: bool = False) -> Path:
    if seed:  # give the bare repo an initial commit so a clone lands on a real branch
        tmp = dest.parent / (dest.name + "_seed")
        subprocess.run(["git", "clone", "-q", str(remote), str(tmp)], check=True, capture_output=True, text=True)
        _run(tmp, "config", "user.email", "t@t")
        _run(tmp, "config", "user.name", "t")
        (tmp / "README.md").write_text("v0\n")
        _run(tmp, "add", "-A")
        _run(tmp, "commit", "-q", "-m", "init")
        _run(tmp, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True, capture_output=True, text=True)
    _run(dest, "config", "user.email", "t@t")
    _run(dest, "config", "user.name", "t")
    return dest


def _commit(ws: Path, text: str) -> str:
    (ws / "note.md").write_text(text)
    _run(ws, "add", "-A")
    _run(ws, "commit", "-q", "-m", text)
    return _run(ws, "rev-parse", "HEAD")


def test_home_remote_is_origin_for_a_clone(tmp_path):
    bare = _bare(tmp_path / "remote.git")
    ws = _clone(bare, tmp_path / "ws", seed=True)
    home = home_remote(ws)
    assert home is not None
    name, url = home
    assert name == "origin"
    assert TOKEN not in url  # token-free (P15)


def test_status_no_home_when_no_remote(tmp_path):
    ws = tmp_path / "born"
    ws.mkdir()
    _run(ws, "init", "-q", "-b", "main")
    _run(ws, "config", "user.email", "t@t")
    _run(ws, "config", "user.name", "t")
    _commit(ws, "local only")
    s = remote_status(ws)
    assert s.has_home is False
    assert s.branch == "main"
    assert s.ahead == 0 and s.behind == 0


def test_push_fast_forwards_the_home(tmp_path):
    bare = _bare(tmp_path / "remote.git")
    ws = _clone(bare, tmp_path / "ws", seed=True)
    sha = _commit(ws, "local work")
    # ahead by 1 before the push
    before = remote_status(ws)
    assert before.ahead == 1 and before.behind == 0
    r = push_origin(ws, token=TOKEN)
    assert r.head_sha == sha and r.branch == "main" and r.remote == "origin"
    # the bare remote now carries the commit
    assert _run(bare, "rev-parse", "main") == sha
    # and status is back in sync (tracking ref advanced locally, no re-fetch)
    after = remote_status(ws)
    assert after.ahead == 0 and after.behind == 0


def test_pull_fast_forwards_from_the_home(tmp_path):
    bare = _bare(tmp_path / "remote.git")
    a = _clone(bare, tmp_path / "a", seed=True)
    b = _clone(bare, tmp_path / "b")
    # B pushes a commit up
    sha = _commit(b, "from B")
    push_origin(b, token=TOKEN)
    # A pulls it (ff)
    r = pull_origin(a, token=TOKEN)
    assert r.updated is True and r.behind_before == 1
    assert _run(a, "rev-parse", "HEAD") == sha


def test_pull_with_no_new_commits_is_a_noop(tmp_path):
    bare = _bare(tmp_path / "remote.git")
    a = _clone(bare, tmp_path / "a", seed=True)
    r = pull_origin(a, token=TOKEN)
    assert r.updated is False and r.behind_before == 0


def test_pull_refuses_a_divergence(tmp_path):
    """Local has a commit the remote doesn't → not a fast-forward → refuse (no merge/rebase/force)."""
    bare = _bare(tmp_path / "remote.git")
    a = _clone(bare, tmp_path / "a", seed=True)
    b = _clone(bare, tmp_path / "b")
    _commit(b, "from B"); push_origin(b, token=TOKEN)
    _commit(a, "diverging local on A")  # A now has a local commit not on the remote
    with pytest.raises(RemoteSyncError) as exc:
        pull_origin(a, token=TOKEN)
    assert "fast-forward" in str(exc.value).lower()
    assert TOKEN not in str(exc.value)


def test_push_non_fast_forward_is_refused(tmp_path):
    """The remote moved ahead → a push is rejected (never a force push), with a token-free message."""
    bare = _bare(tmp_path / "remote.git")
    a = _clone(bare, tmp_path / "a", seed=True)
    b = _clone(bare, tmp_path / "b")
    _commit(b, "from B"); push_origin(b, token=TOKEN)
    _commit(a, "local on A")  # A diverges without pulling
    with pytest.raises(RemoteSyncError) as exc:
        push_origin(a, token=TOKEN)
    assert TOKEN not in str(exc.value)
    assert "force" in str(exc.value).lower() or "reject" in str(exc.value).lower()


def test_push_requires_a_token(tmp_path):
    bare = _bare(tmp_path / "remote.git")
    ws = _clone(bare, tmp_path / "ws", seed=True)
    with pytest.raises(ValueError):
        push_origin(ws, token="  ")
