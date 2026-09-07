"""Loading an EXISTING repo into a SHARED (group) workspace — the group lane of "attach".

The desk already had this (``POST /api/workspace/swap``); a group workspace did not, and the two are
not the same problem. A desk belongs to a subject; a group belongs to a MEMBER LIST — and that member
list lives inside the very tree an attach replaces. So the tests here hold three things:

  1. the mechanic (park what is live, clone the repo in, swap back with no re-clone),
  2. **membership survives** — the one way this feature could silently destroy a group,
  3. the write gate — a viewer may read a group workspace and may not replace it.

Real git throughout, local file remotes, no network.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_attach import (
    CloneError,
    attach_shared_workspace,
    shared_attached_state,
    shared_store,
)
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests import gitserve


class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


def _git(work: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _client(root: Path, index=None):
    return TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(root)), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
        membership_index=index or m.InMemoryMembershipIndex(),
    ))


def _shared_ws(root: Path, workspace_id: str, owner: str = "u_owner") -> Path:
    """A real shared workspace: a git tree with a committed member list, exactly as ensure_owner makes."""
    ws = root / workspace_id
    ws.mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "CLAUDE.md").write_text("# the group's workspace\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    m.ensure_owner(root, workspace_id, owner, index=m.InMemoryMembershipIndex(),
                   commit_fn=lambda w, msg: m.policy_commit(w, msg))
    return ws


def _desk(root: Path, subject: str) -> Path:
    """A person's own workspace, where the store keeps it: <root>/<subject>."""
    ws = root / subject
    ws.mkdir(parents=True)
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "config", "user.email", "t@t"); _git(ws, "config", "user.name", "t")
    (ws / "CLAUDE.md").write_text("# my desk\n")
    _git(ws, "add", "-A"); _git(ws, "commit", "-q", "-m", "seed")
    return ws


def _existing_repo(root: Path, name: str = "their-kg") -> str:
    """Somebody's EXISTING workspace repo, already on 'GitHub' — the thing the founder wants to load."""
    work = root / f"{name}-src"
    work.mkdir(parents=True)
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    (work / "CLAUDE.md").write_text("# ASWF DNA\n")
    (work / "README.md").write_text("the existing workspace\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "their history")
    bare = root / f"{name}.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True, capture_output=True)
    return str(bare)


# ── the mechanic ───────────────────────────────────────────────────────────────────────────────────

def test_attach_clones_the_repo_in_and_parks_what_was_there(tmp_path):
    ws = _shared_ws(tmp_path, "acme-a1b2c3")
    repo = _existing_repo(tmp_path)

    r = attach_shared_workspace(tmp_path, "acme-a1b2c3", repo, "main")

    assert r.swapped and r.cloned and r.parked_slug == "seed"
    assert (ws / "README.md").read_text() == "the existing workspace\n"
    assert (shared_store(tmp_path, "acme-a1b2c3") / "seed" / "CLAUDE.md").exists(), \
        "the group's previous tree must be kept, not destroyed"
    state = shared_attached_state(tmp_path, "acme-a1b2c3")
    assert state["slots"][state["active"]]["repo"] == repo


def test_the_member_list_survives_the_attach(tmp_path):
    """THE failure this feature could have shipped: ``policy/members.json`` lives INSIDE the tree the
    clone replaces, so without carrying it across, attaching a repo deletes everyone's access — the
    workspace would answer ``is_member -> None`` for every member and vanish from every active set."""
    _shared_ws(tmp_path, "grp-x1", owner="u_owner")
    m.grant_membership(tmp_path, "grp-x1", "u_mate", "contributor",
                       index=m.InMemoryMembershipIndex(), added_by="u_owner", commit_fn=m.policy_commit)
    assert m.is_member(tmp_path, "grp-x1", "u_mate") == "contributor"

    attach_shared_workspace(tmp_path, "grp-x1", _existing_repo(tmp_path, "other"), "main")

    assert m.is_member(tmp_path, "grp-x1", "u_owner") == "owner"
    assert m.is_member(tmp_path, "grp-x1", "u_mate") == "contributor"
    # …and it is carried WITHOUT entering the attached repo's history: our member list is not theirs to
    # receive, and a local commit on arrival would make the first pull after an attach a divergence.
    assert _git(tmp_path / "grp-x1", "status", "--porcelain") == "", \
        "the attached tree must be clean — the carried policy is excluded, not committed"
    assert "/policy/" in (tmp_path / "grp-x1" / ".git" / "info" / "exclude").read_text()
    assert _git(tmp_path / "grp-x1", "log", "--oneline") .count(chr(10)) == 0, "no commit was added"

    # a later grant still works against the untracked policy
    m.grant_membership(tmp_path, "grp-x1", "u_third", "viewer",
                       index=m.InMemoryMembershipIndex(), added_by="u_owner", commit_fn=m.policy_commit)
    assert m.is_member(tmp_path, "grp-x1", "u_third") == "viewer"
    assert _git(tmp_path / "grp-x1", "status", "--porcelain") == ""


def test_swapping_back_restores_the_parked_tree_without_a_re_clone(tmp_path):
    ws = _shared_ws(tmp_path, "grp-x2")
    repo = _existing_repo(tmp_path)
    attach_shared_workspace(tmp_path, "grp-x2", repo, "main")
    (ws / "written-while-attached.md").write_text("work done on the repo\n")

    back = attach_shared_workspace(tmp_path, "grp-x2", None, slug="seed")
    assert back.swapped and not back.cloned
    assert (ws / "CLAUDE.md").read_text() == "# the group's workspace\n"

    again = attach_shared_workspace(tmp_path, "grp-x2", repo, "main")
    assert again.swapped and not again.cloned, "a repo we already hold must be restored, never re-cloned"
    assert (ws / "written-while-attached.md").exists(), "local work must survive the round trip"


def test_attaching_the_repo_already_mounted_is_a_no_op(tmp_path):
    _shared_ws(tmp_path, "grp-x3")
    repo = _existing_repo(tmp_path)
    attach_shared_workspace(tmp_path, "grp-x3", repo, "main")
    again = attach_shared_workspace(tmp_path, "grp-x3", repo, "main")
    assert again.swapped is False and again.cloned is False


def test_a_failed_clone_leaves_the_group_workspace_untouched(tmp_path):
    ws = _shared_ws(tmp_path, "grp-x4")

    def _boom(repo_url, ref, dest, token=None, **kw):
        raise CloneError("Authentication failed for 'https://github.com/acme/private.git'")

    with pytest.raises(CloneError):
        attach_shared_workspace(tmp_path, "grp-x4", "https://github.com/acme/private.git", "main", clone=_boom)

    assert (ws / "CLAUDE.md").read_text() == "# the group's workspace\n"
    assert m.is_member(tmp_path, "grp-x4", "u_owner") == "owner"
    assert shared_attached_state(tmp_path, "grp-x4")["active"] is None


def test_a_non_compliant_repo_is_nested_under_a_governed_workspace(tmp_path):
    """A repo with no CLAUDE.md is not a workspace; it is wrapped rather than refused (same rule as the
    desk attach), so "load our existing docs repo" works without asking anyone to restructure it."""
    _shared_ws(tmp_path, "grp-x5")
    plain = tmp_path / "plain-src"
    plain.mkdir()
    _git(plain, "init", "-q", "-b", "main")
    _git(plain, "config", "user.email", "t@t"); _git(plain, "config", "user.name", "t")
    (plain / "notes.md").write_text("just some docs\n")
    _git(plain, "add", "-A"); _git(plain, "commit", "-q", "-m", "docs")
    bare = tmp_path / "plain.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(plain), str(bare)], check=True, capture_output=True)

    r = attach_shared_workspace(tmp_path, "grp-x5", str(bare), "main")
    assert r.nested
    assert (tmp_path / "grp-x5" / "kg" / "plain" / "notes.md").exists()
    assert m.is_member(tmp_path, "grp-x5", "u_owner") == "owner"


@pytest.mark.parametrize("bad", ["../escape", "", ".hidden", "a/b"])
def test_a_workspace_id_can_never_traverse(tmp_path, bad):
    with pytest.raises(ValueError):
        attach_shared_workspace(tmp_path, bad, None, slug="seed")


# ── the routes: who may do it ──────────────────────────────────────────────────────────────────────

def test_route_attaches_for_a_contributor_and_refuses_a_viewer(tmp_path, monkeypatch):
    idx = m.InMemoryMembershipIndex()
    _shared_ws(tmp_path, "grp-r1", owner="u_owner")
    m.grant_membership(tmp_path, "grp-r1", "u_writer", "contributor", index=idx, added_by="u_owner", commit_fn=m.policy_commit)
    m.grant_membership(tmp_path, "grp-r1", "u_reader", "viewer", index=idx, added_by="u_owner", commit_fn=m.policy_commit)
    repo = gitserve.serve(tmp_path, gitserve.bare_repo(tmp_path, "kg"), monkeypatch)
    c = _client(tmp_path, idx)

    refused = c.post("/api/workspace/shared/grp-r1/attach", json={"repo": repo},
                     headers={"X-User-Id": "u_reader"})
    assert refused.status_code == 403
    assert not (tmp_path / "grp-r1" / "README.md").exists()

    stranger = c.post("/api/workspace/shared/grp-r1/attach", json={"repo": repo},
                      headers={"X-User-Id": "u_nobody"})
    assert stranger.status_code == 403

    ok = c.post("/api/workspace/shared/grp-r1/attach", json={"repo": repo},
                headers={"X-User-Id": "u_writer"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["state"] == "cloned" and body["repo"] == repo
    assert (tmp_path / "grp-r1" / "README.md").exists()


def test_route_attached_view_states_the_home_and_the_credential_kind_only(tmp_path, monkeypatch):
    idx = m.InMemoryMembershipIndex()
    _shared_ws(tmp_path, "grp-r2", owner="u_owner")
    repo = gitserve.serve(tmp_path, gitserve.bare_repo(tmp_path, "kg"), monkeypatch)
    c = _client(tmp_path, idx)
    c.post("/api/workspace/shared/grp-r2/attach", json={"repo": repo}, headers={"X-User-Id": "u_owner"})

    got = c.get("/api/workspace/shared/grp-r2/attached", headers={"X-User-Id": "u_owner"})
    assert got.status_code == 200
    view = got.json()
    assert view["workspace_id"] == "grp-r2"
    assert view["slots"][view["active"]]["repo"] == repo
    assert view["home"]["remote"] == "origin"
    # the DISPLAY url drops any userinfo (workspace_publish._display_url treats it as a
    # credential), so compare what identifies the repository: host and path.
    assert view["home"]["url"].endswith("/acme/kg")
    assert view["credential"].startswith("origin ")
    assert "ghp_" not in got.text and "PRIVATE KEY" not in got.text

    assert c.get("/api/workspace/shared/grp-r2/attached", headers={"X-User-Id": "u_nobody"}).status_code == 403


def test_push_pull_and_status_address_a_shared_workspace_by_id(tmp_path, monkeypatch):
    """The sync routes already took a ``slug``; these prove it resolves a GROUP workspace, and that a
    pull — which rewrites the tree — is refused for a viewer even though a read of the same workspace is not."""
    idx = m.InMemoryMembershipIndex()
    _shared_ws(tmp_path, "grp-r3", owner="u_owner")
    m.grant_membership(tmp_path, "grp-r3", "u_reader", "viewer", index=idx, added_by="u_owner", commit_fn=m.policy_commit)
    bare = gitserve.bare_repo(tmp_path, "syncme")
    repo = gitserve.serve(tmp_path, bare, monkeypatch, repo="syncme")
    c = _client(tmp_path, idx)
    c.post("/api/workspace/shared/grp-r3/attach", json={"repo": repo}, headers={"X-User-Id": "u_owner"})

    st = c.get("/api/workspace/git-remote-status?slug=grp-r3", headers={"X-User-Id": "u_owner"})
    assert st.status_code == 200 and st.json()["has_home"]
    assert st.json()["url"].endswith("/acme/syncme")   # display url drops userinfo + .git

    # a new commit lands on the home; the group pulls it
    other = tmp_path / "collab"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True, capture_output=True)
    _git(other, "config", "user.email", "t@t"); _git(other, "config", "user.name", "t")
    (other / "NEW.md").write_text("added elsewhere\n")
    _git(other, "add", "-A"); _git(other, "commit", "-q", "-m", "elsewhere"); _git(other, "push", "-q", "origin", "main")

    pulled = c.post("/api/workspace/pull", json={"slug": "grp-r3"}, headers={"X-User-Id": "u_owner"})
    assert pulled.status_code == 200, pulled.text
    assert pulled.json()["updated"] is True
    assert (tmp_path / "grp-r3" / "NEW.md").exists()

    refused = c.post("/api/workspace/pull", json={"slug": "grp-r3"}, headers={"X-User-Id": "u_reader"})
    assert refused.status_code == 403, "a viewer must not be able to rewrite the group's tree"

    (tmp_path / "grp-r3" / "ours.md").write_text("group work\n")
    _git(tmp_path / "grp-r3", "config", "user.email", "t@t"); _git(tmp_path / "grp-r3", "config", "user.name", "t")
    _git(tmp_path / "grp-r3", "add", "-A"); _git(tmp_path / "grp-r3", "commit", "-q", "-m", "ours")
    pushed = c.post("/api/workspace/push", json={"slug": "grp-r3", "token": "x"},
                    headers={"X-User-Id": "u_owner"})
    assert pushed.status_code == 200, pushed.text
    assert _git(bare, "log", "--oneline", "-1").endswith("ours")


def test_deploy_key_route_hands_out_the_public_half_only(tmp_path):
    idx = m.InMemoryMembershipIndex()
    _shared_ws(tmp_path, "grp-r4", owner="u_owner")
    c = _client(tmp_path, idx)

    empty = c.get("/api/workspace/grp-r4/deploy-key", headers={"X-User-Id": "u_owner"})
    assert empty.status_code == 200 and empty.json()["public_key"] is None

    made = c.post("/api/workspace/grp-r4/deploy-key",
                  json={"repo": "git@github.com:acme/kg.git"}, headers={"X-User-Id": "u_owner"})
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["public_key"].startswith("ssh-ed25519 ")
    assert body["add_at"] == "https://github.com/acme/kg/settings/keys"
    assert body["add_as"] == "a deploy key with WRITE access"
    assert "say `done` when added" in body["then"]
    assert "PRIVATE KEY" not in made.text

    again = c.post("/api/workspace/grp-r4/deploy-key", json={}, headers={"X-User-Id": "u_owner"})
    assert again.json()["public_key"] == body["public_key"], "re-asking must not invalidate a key they added"

    assert c.get("/api/workspace/grp-r4/deploy-key", headers={"X-User-Id": "u_nobody"}).status_code in (403, 404)


def test_a_private_repo_with_no_credential_answers_with_the_key_to_add(tmp_path, monkeypatch):
    """The whole secrets answer in one response: when git refuses us and we hold nothing, the reply is a
    STATE — here is our public key, add it as a write deploy key — not a field asking for a token."""
    idx = m.InMemoryMembershipIndex()
    _shared_ws(tmp_path, "grp-r5", owner="u_owner")
    c = _client(tmp_path, idx)

    r = c.post("/api/workspace/shared/grp-r5/attach",
               json={"repo": "ssh://git@github.com/acme/definitely-not-there.git"},
               headers={"X-User-Id": "u_owner"})
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "ssh-ed25519 " in detail
    assert "deploy key with WRITE access" in detail
    assert "say `done` when added" in detail
    assert (tmp_path / "grp-r5" / "CLAUDE.md").exists(), "the group's tree must be untouched by a failed attach"


def test_the_desk_lane_answers_the_same_way(tmp_path, monkeypatch):
    """The person's OWN workspace loads a repo through ``/api/workspace/swap``, and it must resolve the
    same credential — otherwise an ssh:// repo works for a group and silently does not for a desk, and
    the MCP verb (which carries no token, ever) could only load public repos onto a desk."""
    c = _client(tmp_path)
    repo = gitserve.serve(tmp_path, gitserve.bare_repo(tmp_path, "mine"), monkeypatch, repo="mine")

    ok = c.post("/api/workspace/swap", json={"repo": repo, "ref": "main"}, headers={"X-User-Id": "u_solo"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["cloned"] is True
    assert (tmp_path / "u_solo" / "README.md").exists()

    refused = c.post("/api/workspace/swap",
                     json={"repo": "ssh://git@github.com/acme/definitely-not-there.git"},
                     headers={"X-User-Id": "u_solo2"})
    assert refused.status_code == 502
    assert "ssh-ed25519 " in refused.json()["detail"]
    assert "say `done` when added" in refused.json()["detail"]


def test_personal_is_a_name_the_desk_answers_to(tmp_path):
    """The terminal's workspace chip and the MCP verbs both say "personal" for a person's own desk; the
    store says "seed slot". A route addressed with the first name used to 404 — which is how the deploy
    key for a desk became unreachable from the very path that needs it most (the MCP, which has no other
    way to name it)."""
    c = _client(tmp_path)
    _desk(tmp_path, "u_desk")

    made = c.post("/api/workspace/personal/deploy-key",
                  json={"repo": "git@github.com:acme/kg.git"}, headers={"X-User-Id": "u_desk"})
    assert made.status_code == 200, made.text
    assert made.json()["public_key"].startswith("ssh-ed25519 ")

    status = c.get("/api/workspace/git-remote-status?slug=personal", headers={"X-User-Id": "u_desk"})
    assert status.status_code == 200

    # …and it is still their OWN desk it names, never anybody else's
    assert c.get("/api/workspace/git-remote-status?slug=u_someone_else",
                 headers={"X-User-Id": "u_desk"}).status_code == 404
