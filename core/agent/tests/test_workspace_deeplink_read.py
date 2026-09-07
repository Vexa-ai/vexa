"""Reading a page in a shared workspace you belong to — `_read_target`'s scope (Vexa-ai/vexa#1643).

**The admin opened `/w/<a shared workspace of his>/README.md` and got his own desk's README.** The
terminal half of that is the route; this is the half underneath it, and it is the READ twin of the
write-path defect F196/F198/F200 already fixed one branch below:

    ``_read_target`` authorized a slug by whether it was MOUNTED — the caller's own actives plus
    ``shared_active_mounts`` over their memberships. But ``shared_active_mounts`` drops a workspace
    the subject has SWITCHED OFF (``hidden_shared``, a per-user display toggle) and one whose index
    row is stale, while their membership is untouched. So a member who had switched a workspace off
    — or whose chat's mount set switched it off for them, which is exactly what ``mountSet`` does on
    every chat open — was refused a page they may read, with the same 403 a stranger gets.

    And the link resolver, ``access_for``, answers ``readable`` for that same person off
    ``policy/members.json``. A chip that says you may open something and an endpoint that refuses it
    is worse than either answer alone — which is what ``_read_target``'s own docstring says about
    the desk fall-through it grew for precisely this reason.

The authoritative answer is one call away and is what the write path already uses: the roster in the
workspace's own git, read by ``is_member``. **Membership, not mount state.** WRITE is untouched:
``require_role(..., "contributor")`` still gates it, so a viewer reads and does not write.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _git(work: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _init_ws(root: Path, slug: str, body: str = "hi\n") -> Path:
    ws = root / slug
    (ws / "kg" / "entities").mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text(body)
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    return ws


def _h(subject: str) -> dict:
    return {"X-User-Id": subject}


def _world(root: Path):
    """Desks 126 and 127, and a shared workspace `pilot` that 126 owns and 127 reads."""
    _init_ws(root, "126")
    _init_ws(root, "127")
    _init_ws(root, "pilot", "# Pilot\n")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(root, "pilot", "126", index=idx)
    m.grant_membership(root, "pilot", "127", "viewer", added_by="126", index=idx)
    c = TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
        membership_index=idx,
    ))
    return c, idx


def _read(c, subject: str, slug: str, path: str = "README.md"):
    return c.get("/api/workspace/file", params={"path": path, "slug": slug}, headers=_h(subject))


# ── the link opens for every member, mounted or not ─────────────────────────────────────────────

def test_the_owner_reads_the_page_after_switching_the_workspace_off(tmp_path):
    """The founder's own case. `mountSet` switches OFF every shared workspace not in the chat's
    set on every chat open, so "switched off" is the ORDINARY state of a workspace you are not
    currently conversing about — and a link into one is the ordinary way you reach it."""
    c, _ = _world(tmp_path)
    assert _read(c, "126", "pilot").status_code == 200

    off = c.post("/api/workspace/shared/pilot/active", json={"active": False}, headers=_h("126"))
    assert off.status_code == 200

    r = _read(c, "126", "pilot")
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "# Pilot\n"


def test_a_viewer_reads_it_too_and_still_cannot_write_it(tmp_path):
    """Reader is a role, not a lesser kind of membership: the deliverable says desk, shared
    (owner, contributor, reader) and `_global`. The write gate is a different question and does
    not move — `require_role(..., "contributor")` is still the one answering it."""
    c, _ = _world(tmp_path)
    c.post("/api/workspace/shared/pilot/active", json={"active": False}, headers=_h("127"))

    assert _read(c, "127", "pilot").status_code == 200
    wrote = c.put("/api/workspace/file", json={"path": "README.md", "content": "no", "slug": "pilot"},
                  headers=_h("127"))
    assert wrote.status_code == 403, wrote.text


def test_a_stale_index_row_does_not_cost_a_member_their_page(tmp_path):
    """The index (`users.data.memberships[]`) is a convenience copy, rebuildable from the git files,
    and `shared_active_mounts` enumerates candidates from it. A member missing from it was refused;
    the roster in the workspace's own git says otherwise and it is the authority."""
    c, idx = _world(tmp_path)
    idx.remove("126", "pilot")
    assert _read(c, "126", "pilot").status_code == 200


# ── and it is not a way in for anybody else ─────────────────────────────────────────────────────

def test_a_non_member_is_still_refused(tmp_path):
    """The widening is membership-shaped and nothing else. A shared workspace is not a desk, so the
    read fall-through above it (any signed-in subject may read a DESK) must not reach it."""
    c, idx = _world(tmp_path)
    m.remove_member(tmp_path, "pilot", "127", index=idx)
    r = _read(c, "127", "pilot")
    assert r.status_code == 403, r.text


def test_an_unauthenticated_caller_is_refused(tmp_path):
    """No subject, no membership to check — the branch is guarded on the subject for the same
    reason the desk fall-through is."""
    c, _ = _world(tmp_path)
    assert c.get("/api/workspace/file", params={"path": "README.md", "slug": "pilot"}).status_code in (401, 403)


def test_a_desk_and_the_company_tier_are_unchanged(tmp_path):
    """The other two kinds a `/w/…` link can name. Neither goes through the new branch; both are
    asserted here so a change to it cannot quietly move them."""
    c, _ = _world(tmp_path)
    # another person's desk — readable by any signed-in subject (founder ruling 2026-09-02)
    assert _read(c, "127", "126").status_code == 200
    # …and still not writable by them
    assert c.put("/api/workspace/file", json={"path": "README.md", "content": "no", "slug": "126"},
                 headers=_h("127")).status_code == 403
    # the company tier, when the deployment has one
    (tmp_path / "_global").mkdir()
    (tmp_path / "_global" / "README.md").write_text("org\n")
    assert _read(c, "127", "_global").json()["content"] == "org\n"
