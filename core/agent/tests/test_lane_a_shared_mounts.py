"""Lane A (slim slice) — membership → shared mount in the active set.

The linchpin seam: a workspace the subject is a MEMBER of shows up in their ACTIVE SET (what the
dispatch mounts + what the terminal's readActiveSet()/KNOWLEDGE panel renders), with write gated by
role. Offline L2 tests over the real git-backed policy/members.json + the in-memory index — no docker,
no runtime, no DB. Reuses the Lane M scaffolding (_init_ws, grant_membership, InMemoryMembershipIndex).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_attach import SHARED_ROLE, shared_active_mounts
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


# ── minimal scaffolding (mirrors test_workspace_membership; inlined so the module is self-contained) ──
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


def _init_ws(root: Path, workspace_id: str) -> Path:
    """A real git workspace dir (so policy_commit + is_member exercise real git)."""
    ws = root / workspace_id
    ws.mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("hi\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    return ws


def _client(root: Path, index=None):
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
        membership_index=index or m.InMemoryMembershipIndex(),
    ))


def _h(subject: str) -> dict:
    return {"X-User-Id": subject}


def _grant(root, ws_id, owner, subject=None, role="contributor"):
    """Make `ws_id` a real shared workspace owned by `owner`, optionally granting `subject` `role`.
    Returns the index so callers can read the derived memberships[] exactly as identity would serve them."""
    idx = m.InMemoryMembershipIndex()
    _init_ws(root, ws_id)
    m.ensure_owner(root, ws_id, owner, index=idx)
    if subject:
        m.grant_membership(root, ws_id, subject, role, added_by=owner, index=idx)
    return idx


# ── the pure helper: memberships[] → shared ActiveMounts ─────────────────────────────────────────
def test_contributor_gets_a_writable_shared_mount(tmp_path):
    idx = _grant(tmp_path, "wsA", owner="owner1", subject="contrib1", role="contributor")

    mounts = shared_active_mounts(tmp_path, "contrib1", idx.list("contrib1"))

    assert len(mounts) == 1
    (mount,) = mounts
    assert mount.slug == "wsA"
    assert mount.role == SHARED_ROLE
    assert mount.write is True                      # contributor writes
    assert mount.primary is False                   # a shared ws is never the private baseline
    assert mount.path == str((tmp_path / "wsA").resolve())


def test_viewer_gets_a_read_only_shared_mount(tmp_path):
    idx = _grant(tmp_path, "wsA", owner="owner1", subject="viewer1", role="viewer")

    (mount,) = shared_active_mounts(tmp_path, "viewer1", idx.list("viewer1"))

    assert mount.role == SHARED_ROLE
    assert mount.write is False                      # viewer = read-only — the write gate


def test_stale_index_entry_is_not_mounted(tmp_path):
    # The index NAMES wsA for u_ghost, but the authoritative policy/members.json has no such member.
    _grant(tmp_path, "wsA", owner="owner1")          # owner only; u_ghost was never granted
    stale = [{"workspace_id": "wsA", "role": "contributor"}]

    assert shared_active_mounts(tmp_path, "u_ghost", stale) == []   # git disagrees with the index → drop


def test_unmaterialized_and_reserved_and_own_are_skipped(tmp_path):
    idx = _grant(tmp_path, "wsA", owner="owner1", subject="u1", role="contributor")
    memberships = idx.list("u1") + [
        {"workspace_id": "wsGhost", "role": "contributor"},   # not materialized on this node
        {"workspace_id": "_system", "role": "owner"},          # reserved — never shared
        {"workspace_id": "u1", "role": "owner"},               # the subject's own baseline
        {"workspace_id": "../escape", "role": "owner"},        # traversal attempt
    ]

    mounts = shared_active_mounts(tmp_path, "u1", memberships)

    assert [mount.slug for mount in mounts] == ["wsA"]          # only the real shared membership survives


# ── the route the KNOWLEDGE panel reads: GET /api/workspace/active ────────────────────────────────
def test_active_route_lists_private_baseline_plus_shared(tmp_path):
    idx = _grant(tmp_path, "wsA", owner="owner1", subject="contrib1", role="contributor")
    client = _client(tmp_path, index=idx)

    body = client.get("/api/workspace/active", headers=_h("contrib1")).json()
    active = body["active"]
    by_slug = {mount["slug"]: mount for mount in active}

    # the subject's own private baseline is still first-class (primary, private, writable)...
    (primary,) = [mount for mount in active if mount["primary"]]
    assert primary["role"] == "private"
    assert primary["write"] is True
    # ...and the shared workspace they're a contributor of now appears, writable, non-primary.
    assert by_slug["wsA"]["role"] == SHARED_ROLE
    assert by_slug["wsA"]["write"] is True
    assert by_slug["wsA"]["primary"] is False


def test_non_member_sees_no_shared_mount(tmp_path):
    _grant(tmp_path, "wsA", owner="owner1")          # nobody else granted
    client = _client(tmp_path, index=m.InMemoryMembershipIndex())

    body = client.get("/api/workspace/active", headers=_h("stranger")).json()

    assert all(mount["slug"] != "wsA" for mount in body["active"])   # wsA is invisible to a non-member


# ── the DISPATCH mount set: shared workspaces enter the stack READ-ONLY (Slice 1) ────────────────
def test_dispatch_mount_set_includes_shared_read_only(tmp_path):
    from types import SimpleNamespace
    from control_plane.dispatch import build_mount_set

    idx = _grant(tmp_path, "wsA", owner="owner1", subject="contrib1", role="contributor")
    settings = SimpleNamespace(
        workspaces_dir=str(tmp_path), global_system_workspace_path="", global_system_workspace_ref="",
    )

    stack = build_mount_set(settings, "contrib1", idx.list("contrib1"))
    shared = [mount for mount in stack if mount["role"] == SHARED_ROLE]

    assert [mount["slug"] for mount in shared] == ["wsA"]
    assert shared[0]["write"] is False           # Slice 1: shared is READ-ONLY until Lane W (serialized writer)
    # and the _system tier is still appended last (three-tier stack intact)
    assert stack[-1]["slug"] == "_system"


# ── tree/file reads scoped by slug are membership-gated (authorization) ───────────────────────────
def test_member_reads_shared_tree_by_slug(tmp_path):
    idx = _grant(tmp_path, "wsA", owner="owner1", subject="contrib1", role="contributor")
    client = _client(tmp_path, index=idx)

    resp = client.get("/api/workspace/tree", params={"slug": "wsA"}, headers=_h("contrib1"))

    assert resp.status_code == 200
    assert "README.md" in resp.json()["files"]     # the shared workspace's own tree, read via slug


def test_non_member_is_refused_shared_tree_and_file(tmp_path):
    _grant(tmp_path, "wsA", owner="owner1")          # stranger is NOT a member
    client = _client(tmp_path, index=m.InMemoryMembershipIndex())

    tree = client.get("/api/workspace/tree", params={"slug": "wsA"}, headers=_h("stranger"))
    file = client.get("/api/workspace/file", params={"path": "README.md", "slug": "wsA"}, headers=_h("stranger"))

    assert tree.status_code == 403          # cannot enumerate a workspace you're not a member of
    assert file.status_code == 403          # ...nor read its files by slug


# ── the reader can read ANY workspace dir under root by path (own .attached slots + shared ws) ─────
def test_reader_reads_any_dir_under_root_and_guards_traversal(tmp_path):
    import pytest
    from control_plane.workspace_reader import WorkspaceReader
    wsr = WorkspaceReader(str(tmp_path))
    # a dir that is NOT <root>/<subject> — mimics a non-primary private slot (.attached/…) or a shared ws
    d = tmp_path / ".attached" / "u1" / "extra"
    (d / "kg").mkdir(parents=True)
    (d / "kg" / "note.md").write_text("hi")

    assert wsr.tree_at(d) == ["kg/note.md"]          # path-based read (impossible via tree(subject) before)
    assert wsr.read_at(d, "kg/note.md") == "hi"
    with pytest.raises(ValueError):                  # a dir OUTSIDE the store root is refused (traversal guard)
        wsr.tree_at(tmp_path.parent)


def test_dispatch_without_index_is_private_only(tmp_path):
    from types import SimpleNamespace
    from control_plane.dispatch import build_mount_set

    _grant(tmp_path, "wsA", owner="owner1", subject="contrib1", role="contributor")
    settings = SimpleNamespace(
        workspaces_dir=str(tmp_path), global_system_workspace_path="", global_system_workspace_ref="",
    )

    stack = build_mount_set(settings, "contrib1", None)   # no memberships passed → no shared mounts

    assert all(mount["role"] != SHARED_ROLE for mount in stack)
