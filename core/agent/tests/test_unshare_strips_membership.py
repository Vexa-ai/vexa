"""Un-share leaves no membership behind: the moved tree carries no member list (removed, committed),
nobody but its owner reads it by slug or id, and no invite minted for the group redeems afterwards.

The member list lives in the tree (`policy/members.json`) and un-share moves the tree into the
owner's private store, so the list used to travel with it — naming the former members of what is now
a private workspace."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_attach import ensure_workspace_private
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


def _git(work: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True, check=True).stdout.strip()


def _group(root: Path, wid: str, owner: str, member: str) -> m.InMemoryMembershipIndex:
    ws = root / wid
    ws.mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("group notes\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(root, wid, owner, index=idx, commit_fn=m.policy_commit)
    m.grant_membership(root, wid, member, "contributor", added_by=owner, index=idx, commit_fn=m.policy_commit)
    return idx


def test_the_moved_tree_carries_no_member_list(tmp_path):
    _group(tmp_path, "grp", "owner1", "member1")
    slug = ensure_workspace_private(tmp_path, "owner1", "grp")
    moved = tmp_path / ".attached" / "owner1" / slug
    assert (moved / "README.md").read_text() == "group notes\n"          # the content stays
    assert not (moved / "policy" / "members.json").exists()              # the member list does not
    assert "policy/members.json" not in _git(moved, "ls-files")          # …and the removal is committed
    assert _git(moved, "status", "--porcelain") == ""
    assert "unshare" in _git(moved, "log", "-1", "--format=%s")


def test_a_former_member_reaches_nothing_and_a_pending_invite_is_void(tmp_path):
    idx = _group(tmp_path, "grp", "owner1", "member1")
    token = m.mint_invite(tmp_path, "grp", role="viewer", created_by="owner1").token
    c = TestClient(create_app(Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
                              reader=WorkspaceReader(str(tmp_path)), membership_index=idx))
    r = c.post("/api/workspace/grp/unshare", headers={"X-User-Id": "owner1"})
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    # the private tree, by every name a former member could try
    for name in ("grp", slug, f".attached/owner1/{slug}"):
        assert c.get("/api/workspace/purpose", params={"slug": name},
                     headers={"X-User-Id": "member1"}).status_code in (400, 403, 404), name
        assert c.get("/api/workspace/file", params={"slug": name, "path": "README.md"},
                     headers={"X-User-Id": "member1"}).status_code in (400, 403, 404), name
    # …and by its id, which did not change: the owner's, nobody else's
    other = c.get(f"/api/workspaces/by-slug/{slug}", headers={"X-User-Id": "member1"}).json()
    mine = c.get(f"/api/workspaces/by-slug/{slug}", headers={"X-User-Id": "owner1"}).json()
    assert other["access"] == "not-yours" and other.get("writable") is not True
    assert mine["access"] == "readable"
    # the invite minted for the group no longer redeems, and does not bring a group back
    with pytest.raises(m.MembershipError):
        m.accept_invite(tmp_path, "grp", token=token, subject="stranger1", index=idx)
    assert not (tmp_path / "grp").exists()


def test_a_link_where_the_policy_folder_was_is_not_followed(tmp_path):
    other = tmp_path / "other"
    (other / "policy").mkdir(parents=True)
    (other / "policy" / "members.json").write_text("[]\n")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "policy").symlink_to(other / "policy")
    assert m.strip_policy(tree) == []
    assert (other / "policy" / "members.json").exists()
