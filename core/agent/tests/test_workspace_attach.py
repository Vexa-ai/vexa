"""workspace_attach — attach a custom external git repo as a subject's workspace, swappable.

Proves the swap lifecycle on REAL git over local repos (no network):
  seed → attach custom repo (parks seed) → swap back to seed (restores park) →
  re-attach the same repo (restores its park, NO re-clone) → idempotent no-op.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from control_plane.workspace_attach import (
    CloneError,
    _authenticated_url,
    _git_clone,
    attached_workspaces,
    swap_workspace,
)


def _make_repo(path: Path, marker: str, *, compliant: bool = True) -> str:
    """A local git repo with a ``MARK`` file carrying ``marker`` — the clone source (no network). By
    default it carries a ``CLAUDE.md`` governance root (a compliant workspace); pass ``compliant=False``
    for a plain repo with no governance root (exercises the nest-under-kg path)."""
    path.mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", *a], cwd=path, check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@test")
    run("config", "user.name", "t")
    (path / "MARK").write_text(marker)
    if compliant:
        (path / "CLAUDE.md").write_text("CUSTOM ROOT")
    run("add", "-A")
    run("commit", "-q", "-m", "seed")
    return str(path)


def _seed_active(root: Path, subject: str, marker: str = "SEED") -> Path:
    """Stand up an initial seeded active workspace at <root>/<subject>."""
    from shared.seeding import seed_workspace
    ws = root / subject
    ws.mkdir(parents=True)
    (ws / "CLAUDE.md").write_text(marker)
    seed_workspace(ws, None)
    return ws


def test_attach_custom_repo_parks_the_seed(tmp_path):
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "origin", "CUSTOM")

    res = swap_workspace(root, "u1", origin, "main")

    assert res.swapped is True and res.cloned is True
    assert res.parked_slug == "seed"
    # active is now the custom repo
    assert (root / "u1" / "MARK").read_text() == "CUSTOM"
    assert (root / "u1" / "CLAUDE.md").read_text() == "CUSTOM ROOT"   # the custom repo's own root
    # the seed was PARKED (kept), never destroyed
    assert (root / ".attached" / "u1" / "seed" / "CLAUDE.md").read_text() == "SEED"
    view = attached_workspaces(root, "u1")
    assert view["active"] == res.active_slug
    assert "seed" in view["slots"]


def test_swap_back_to_seed_restores_the_park(tmp_path):
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "origin", "CUSTOM")
    swap_workspace(root, "u1", origin, "main")

    res = swap_workspace(root, "u1", None)  # repo=None → swap back to seed

    assert res.active_slug == "seed" and res.swapped is True and res.cloned is False
    assert (root / "u1" / "CLAUDE.md").read_text() == "SEED"   # original seed restored
    # the custom repo is now the parked one
    custom_slug = res.parked_slug
    assert (root / ".attached" / "u1" / custom_slug / "MARK").read_text() == "CUSTOM"


def test_swap_back_to_custom_restores_without_recloning(tmp_path):
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "origin", "CUSTOM")
    swap_workspace(root, "u1", origin, "main")

    # make a local edit in the attached custom workspace, then swap away and back
    (root / "u1" / "LOCAL").write_text("edit")
    swap_workspace(root, "u1", None)  # → seed

    calls: list = []
    def _no_clone(repo, ref, dest, token=None):  # restore must NOT clone
        calls.append((repo, ref))
    res = swap_workspace(root, "u1", origin, "main", clone=_no_clone)

    assert res.swapped is True and res.cloned is False
    assert calls == []                                   # restored the parked tree, no re-clone
    assert (root / "u1" / "MARK").read_text() == "CUSTOM"
    assert (root / "u1" / "LOCAL").read_text() == "edit"  # detached edits persisted


def test_compliant_repo_is_used_as_is(tmp_path):
    """A clone that already carries a governance root (CLAUDE.md) is the workspace as-is — not nested."""
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=origin, check=True, capture_output=True)
    run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (origin / "CLAUDE.md").write_text("CUSTOM ROOT")
    run("add", "-A"); run("commit", "-q", "-m", "x")

    res = swap_workspace(root, "u1", str(origin), "main")

    assert res.nested is False
    assert (root / "u1" / "CLAUDE.md").read_text() == "CUSTOM ROOT"  # used directly


def test_noncompliant_repo_is_nested_under_kg_of_a_template_workspace(tmp_path, monkeypatch):
    """A clone with NO governance root is wrapped: a fresh template workspace is materialized and the
    clone is nested under kg/<name>/ (its own .git dropped, folded into the governed workspace's git)."""
    template = tmp_path / "template"
    template.mkdir()
    (template / "CLAUDE.md").write_text("TEMPLATE ROOT")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(template))

    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "data-repo", "RAW", compliant=False)  # no CLAUDE.md

    res = swap_workspace(root, "u1", str(origin), "main")

    assert res.nested is True and res.cloned is True
    ws = root / "u1"
    assert (ws / "CLAUDE.md").read_text() == "TEMPLATE ROOT"          # governed by the template root
    assert (ws / "kg" / "data-repo" / "MARK").read_text() == "RAW"    # clone nested under kg/
    assert not (ws / "kg" / "data-repo" / ".git").exists()            # nested clone's git was dropped
    # the nested import is committed into the workspace repo (clean tree)
    status = subprocess.run(["git", "-C", str(ws), "status", "--porcelain"], capture_output=True, text=True)
    assert status.stdout.strip() == ""


def test_requesting_active_repo_is_a_noop(tmp_path):
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    origin = _make_repo(tmp_path / "origin", "CUSTOM")
    swap_workspace(root, "u1", origin, "main")

    res = swap_workspace(root, "u1", origin, "main")  # already active
    assert res.swapped is False and res.cloned is False and res.parked_slug is None


def test_authenticated_url_embeds_token_for_https_only():
    assert _authenticated_url("https://github.com/o/r.git", "TOK") == "https://TOK@github.com/o/r.git"
    assert _authenticated_url("https://github.com/o/r.git", None) == "https://github.com/o/r.git"
    assert _authenticated_url("git@github.com:o/r.git", "TOK") == "git@github.com:o/r.git"  # ssh: untouched
    assert _authenticated_url("/local/path", "TOK") == "/local/path"                         # local: untouched


def test_token_threads_to_clone_but_is_never_stored(tmp_path):
    """A private-repo token reaches the clone fn but is NOT persisted in the attachment state (P15)."""
    root = tmp_path / "workspaces"
    _seed_active(root, "u1")
    seen = {}

    def fake_clone(repo, ref, dest, token=None):       # simulate a compliant private clone
        seen["token"] = token
        dest.mkdir(parents=True)
        (dest / "CLAUDE.md").write_text("ROOT")
        (dest / "MARK").write_text("PRIVATE")
        subprocess.run(["git", "init", "-q", str(dest)], check=True, capture_output=True)

    res = swap_workspace(root, "u1", "https://github.com/o/private.git", "main",
                         token="SEKRET-TOKEN", clone=fake_clone)

    assert seen["token"] == "SEKRET-TOKEN" and res.cloned is True
    assert (root / "u1" / "MARK").read_text() == "PRIVATE"
    state_text = (root / ".attached" / "u1" / "state.json").read_text()
    assert "SEKRET-TOKEN" not in state_text                       # token absent from persisted state
    assert "token" not in json.loads(state_text)["slots"][res.active_slug]


def test_clone_error_redacts_token(tmp_path):
    """A failed authenticated clone raises CloneError with the token scrubbed from the message (P15)."""
    with pytest.raises(CloneError) as ei:
        _git_clone("https://invalid.invalid/nope.git", "main", tmp_path / "dest", token="SUPERSECRET")
    assert "SUPERSECRET" not in str(ei.value)


def test_failed_clone_leaves_active_workspace_and_state_intact(tmp_path):
    """REGRESSION: a swap whose clone FAILS must not disturb the live workspace — the active tree stays
    in place, no half-parked state, and a subsequent swap-back still works. (Previously the active
    workspace was parked BEFORE the clone, so a clone error left the user with no workspace.)"""
    root = tmp_path / "workspaces"
    _seed_active(root, "u1", "SEED-ROOT")
    # establish a known-good attached repo first, so there's a real active workspace + state to protect
    origin = _make_repo(tmp_path / "origin", "CUSTOM")
    swap_workspace(root, "u1", origin, "main")
    assert (root / "u1" / "MARK").read_text() == "CUSTOM"
    state_before = (root / ".attached" / "u1" / "state.json").read_text()

    def boom(repo, ref, dest, token=None):
        raise CloneError("remote: Repository not found")

    with pytest.raises(CloneError):
        swap_workspace(root, "u1", "https://github.com/x/private.git", "main", clone=boom)

    # the live workspace is UNTOUCHED — still the custom repo, still a real repo
    assert (root / "u1" / "MARK").read_text() == "CUSTOM"
    assert (root / "u1" / ".git").exists()
    # state is unchanged (no phantom park of the active workspace)
    assert (root / ".attached" / "u1" / "state.json").read_text() == state_before
    # and a legitimate swap-back to seed still works afterwards
    res = swap_workspace(root, "u1", None)
    assert res.active_slug == "seed" and (root / "u1" / "CLAUDE.md").read_text() == "SEED-ROOT"


def test_invalid_subject_rejected(tmp_path):
    root = tmp_path / "workspaces"
    root.mkdir()
    with pytest.raises(ValueError):
        swap_workspace(root, "../escape", "x")
    with pytest.raises(ValueError):
        swap_workspace(root, ".attached", "x")  # reserved store namespace
