"""The end-of-turn policy guard never reads or deletes through a link planted in ``policy/``.

``policy/`` sits in the turn's work tree, which the model's tools can write. A turn can therefore
replace ``policy/`` with a symlink, drop a symlink in place of ``policy/members.json``, or add a
symlinked subdirectory — each pointing at another workspace's ``policy/`` or anywhere else on the
volume. The write-back's ``_revert_policy_writes`` must restore THIS workspace's ``policy/`` and
remove the planted link, while the thing the link points at is left exactly as it was: it enumerates
and deletes without following a link at any level (``_current_policy_entries`` walks
``followlinks=False``; the removal goes through ``_unlink_in_tree``), and git replaces a leaf symlink
on checkout rather than writing through it.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from control_plane import workspace_membership as m
from llm import ports

OWNER = "u_owner"


def _git(work: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _owned(root: Path, wid: str) -> Path:
    ws = root / wid
    ws.mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("hi\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    m.ensure_owner(root, wid, OWNER, index=m.InMemoryMembershipIndex(), email="owner@example.test",
                   commit_fn=m.policy_commit)
    return ws


@pytest.fixture
def world(tmp_path):
    victim = _owned(tmp_path, "wsVictim")
    mine = _owned(tmp_path, "wsMine")
    secret = victim / m.MEMBERS_FILE
    base = _git(mine, "rev-parse", "HEAD")
    return tmp_path, mine, victim, secret, base


def _victim_untouched(secret: Path) -> None:
    assert secret.exists() and OWNER in secret.read_text()


def test_a_policy_dir_replaced_by_a_link_is_removed_not_followed(world):
    _root, mine, _victim, secret, base = world
    import shutil
    shutil.rmtree(mine / "policy")
    (mine / "policy").symlink_to(secret.parent)
    affected = ports._revert_policy_writes(mine, base)
    _victim_untouched(secret)
    assert not (mine / "policy").is_symlink()          # the link is gone
    assert OWNER in (mine / m.MEMBERS_FILE).read_text()  # this workspace's own list is restored
    assert "policy" in affected


def test_a_members_file_replaced_by_a_link_is_removed_not_written_through(world):
    _root, mine, _victim, secret, base = world
    (mine / m.MEMBERS_FILE).unlink()
    (mine / m.MEMBERS_FILE).symlink_to(secret)
    ports._revert_policy_writes(mine, base)
    _victim_untouched(secret)
    f = mine / m.MEMBERS_FILE
    assert not f.is_symlink() and OWNER in f.read_text()


def test_a_symlinked_subdir_under_policy_is_not_descended_or_deleted_through(world):
    _root, mine, _victim, secret, base = world
    (mine / "policy" / "sub").symlink_to(secret.parent, target_is_directory=True)
    affected = ports._revert_policy_writes(mine, base)
    _victim_untouched(secret)
    assert not (mine / "policy" / "sub").exists()      # the link itself removed
    assert "policy/sub" in affected
    # the victim's file was never enumerated as this turn's add
    assert not any(a.startswith("policy/sub/") for a in affected)


def test_an_untouched_policy_is_left_alone(world):
    _root, mine, _victim, _secret, base = world
    assert ports._revert_policy_writes(mine, base) == []
    assert OWNER in (mine / m.MEMBERS_FILE).read_text()


def test_unlink_in_tree_never_follows_a_symlinked_parent(tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("victim's\n")
    work = tmp_path / "work"
    (work / "policy").mkdir(parents=True)
    (work / "policy" / "sub").symlink_to(victim, target_is_directory=True)
    ports._unlink_in_tree(work, "policy/sub/keep.txt")   # would delete victim/keep.txt if followed
    assert (victim / "keep.txt").exists()
    ports._unlink_in_tree(work, "policy/sub")            # removes the link itself
    assert not (work / "policy" / "sub").exists() and victim.exists()
