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
    # NAMED `_global`, not "global". Inside the store the organisation tier must BE `_global`:
    # the runtime derives the store subpath from the mount's `path`, which is always
    # `<root>/_global`, so an in-store directory under any other name would mount something other
    # than what the operator configured. global_mount refuses it rather than binding the wrong dir.
    global_dir = tmp_path / "_global"
    global_dir.mkdir()
    settings = SimpleNamespace(
        workspaces_dir=str(tmp_path), global_system_workspace_path=str(global_dir),
        global_system_workspace_ref="", global_admin_subjects="",
    )

    stack = build_mount_set(settings, "contrib1", idx.list("contrib1"))
    shared = [mount for mount in stack if mount["role"] == SHARED_ROLE]

    assert [mount["slug"] for mount in shared] == ["wsA"]
    assert shared[0]["write"] is True            # contributor mounts the shared ws READ-WRITE
    # and the _system tier is still appended last (three-tier stack intact)
    assert stack[-1]["slug"] == "_system"


def test_switch_off_hides_shared_from_active_set(tmp_path):
    from control_plane.workspace_attach import set_shared_active, shared_active_mounts
    idx = _grant(tmp_path, "wsA", owner="owner1", subject="u1", role="contributor")

    assert [m.slug for m in shared_active_mounts(tmp_path, "u1", idx.list("u1"))] == ["wsA"]
    set_shared_active(tmp_path, "u1", "wsA", active=False)     # switch it OFF
    assert shared_active_mounts(tmp_path, "u1", idx.list("u1")) == []   # not mounted...
    assert m.is_member(tmp_path, "wsA", "u1") == "contributor"          # ...but still a member
    set_shared_active(tmp_path, "u1", "wsA", active=True)      # switch back ON
    assert [m.slug for m in shared_active_mounts(tmp_path, "u1", idx.list("u1"))] == ["wsA"]


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


def test_non_member_is_refused_a_shared_WRITE_by_slug(tmp_path):
    """F96 — the write side, stated where the read side already is.

    The rig's `workspace_write` bypassed this entirely: it wrote by `docker exec` into the volume,
    and a volume has no membership. Now that it forwards here on the caller's identity, THIS is the
    check that stops a stranger overwriting a shared workspace's files, so it is asserted rather
    than assumed."""
    _grant(tmp_path, "wsA", owner="owner1")          # stranger is NOT a member
    client = _client(tmp_path, index=m.InMemoryMembershipIndex())

    r = client.put("/api/workspace/file", headers=_h("stranger"),
                   json={"path": "README.md", "content": "mine now", "slug": "wsA"})

    assert r.status_code == 403


# ── the full share flow: create shared ws (bootstrap) → mint → accept → appears for the new member ──
def test_share_flow_create_mint_accept(tmp_path, monkeypatch):
    # a minimal seed template so create_shared_workspace_dir can seed the new ws
    seed = tmp_path / "_seed"; (seed / "kg" / "entities").mkdir(parents=True); (seed / "index.md").write_text("# seed\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    idx = m.InMemoryMembershipIndex()
    client = _client(tmp_path, index=idx)

    # 1) owner creates a shareable workspace (bootstrap)
    created = client.post("/api/workspace/shared/new", json={"name": "Deal Room"}, headers=_h("owner1"))
    assert created.status_code == 201
    wid = created.json()["workspace_id"]
    assert wid.startswith("deal-room-")
    assert m.is_member(tmp_path, wid, "owner1") == "owner"     # caller is owner in the authoritative store

    # 2) owner mints an open contributor invite
    minted = client.post("/api/workspace/invites",
                         json={"workspace_id": wid, "role": "contributor", "mode": "open", "max_uses": 5},
                         headers=_h("owner1"))
    assert minted.status_code == 201
    token = minted.json()["token"]

    # 3) a brand-new user redeems it → membership
    before = client.get("/api/workspace/active", headers=_h("newbie")).json()["active"]
    assert all(x["slug"] != wid for x in before)
    accepted = client.post("/api/workspace/invites/accept", json={"token": token}, headers=_h("newbie"))
    assert accepted.status_code == 200 and accepted.json()["already_member"] is False

    # 4) the shared ws now shows in the new member's active set
    after = client.get("/api/workspace/active", headers=_h("newbie")).json()["active"]
    shared = [x for x in after if x["slug"] == wid]
    assert shared and shared[0]["role"] == SHARED_ROLE


# ── any workspace can be shared AFTER creation (promote a private one to shared) ──────────────────
def test_share_enable_promotes_a_private_workspace(tmp_path, monkeypatch):
    from control_plane.workspace_attach import create_workspace, ensure_workspace_shareable, active_workspaces
    seed = tmp_path / "_seed"; (seed / "kg").mkdir(parents=True); (seed / "index.md").write_text("# seed\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))

    # a normal private workspace (the ONE kind of create) in the subject's .attached store
    created = create_workspace(tmp_path, "u1", name="Harari")
    slug = created.slug
    assert (tmp_path / ".attached" / "u1" / slug).exists()
    assert slug in [m.slug for m in active_workspaces(tmp_path, "u1")]

    # share it AFTER the fact → promoted to a top-level shared workspace, private slot gone
    new_id, promoted = ensure_workspace_shareable(tmp_path, "u1", slug)
    assert promoted is True
    assert new_id.startswith("harari-")
    assert (tmp_path / new_id).exists() and not (tmp_path / ".attached" / "u1" / slug).exists()
    assert slug not in [m.slug for m in active_workspaces(tmp_path, "u1")]   # left the private set

    # record ownership → it's now a real shareable workspace
    m.ensure_owner(tmp_path, new_id, "u1", index=m.InMemoryMembershipIndex())
    assert m.is_member(tmp_path, new_id, "u1") == "owner"

    # idempotent: sharing an already-shared workspace is a no-op
    again, promoted2 = ensure_workspace_shareable(tmp_path, "u1", new_id)
    assert again == new_id and promoted2 is False


def test_unshare_moves_a_shared_workspace_back_to_private(tmp_path):
    from control_plane.workspace_attach import ensure_workspace_private, active_workspaces
    idx = _grant(tmp_path, "wsA", owner="owner1", subject="member1", role="contributor")
    assert (tmp_path / "wsA").exists()

    new_slug = ensure_workspace_private(tmp_path, "owner1", "wsA")   # owner un-shares

    assert not (tmp_path / "wsA").exists()                           # the top-level shared ws is gone...
    assert (tmp_path / ".attached" / "owner1" / new_slug).exists()   # ...moved into the owner's private store
    assert new_slug in [m.slug for m in active_workspaces(tmp_path, "owner1")]
    # dropping the index entry (route does this) → shared_active_mounts no longer surfaces it for the member
    from control_plane.workspace_attach import shared_active_mounts
    assert shared_active_mounts(tmp_path, "member1", idx.list("member1")) == []   # ws_dir gone → not mounted


def test_archive_and_delete_workspace(tmp_path, monkeypatch):
    import pytest
    from control_plane.workspace_attach import (create_workspace, set_archived, delete_workspace,
                                                 attached_workspaces, active_workspaces)
    seed = tmp_path / "_seed"; (seed / "kg").mkdir(parents=True); (seed / "index.md").write_text("# seed\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    slug = create_workspace(tmp_path, "u1", name="Scratch").slug

    # archive → flag set + unmounted (dropped from the active set), tree kept
    set_archived(tmp_path, "u1", slug, True)
    assert attached_workspaces(tmp_path, "u1")["slots"][slug]["archived"] is True
    assert slug not in [m.slug for m in active_workspaces(tmp_path, "u1")]
    assert (tmp_path / ".attached" / "u1" / slug).exists()   # data preserved
    set_archived(tmp_path, "u1", slug, False)                # un-archive
    assert attached_workspaces(tmp_path, "u1")["slots"][slug]["archived"] is False

    # delete → slot + tree gone
    delete_workspace(tmp_path, "u1", slug)
    assert slug not in attached_workspaces(tmp_path, "u1")["slots"]
    assert not (tmp_path / ".attached" / "u1" / slug).exists()

    # the baseline is protected from both
    with pytest.raises(ValueError): set_archived(tmp_path, "u1", "seed", True)
    with pytest.raises(ValueError): delete_workspace(tmp_path, "u1", "seed")


def test_cannot_share_the_private_baseline(tmp_path):
    import pytest
    from control_plane.workspace_attach import ensure_workspace_shareable
    # a never-swapped subject's baseline slug is "seed" (the primary) — refused
    with pytest.raises(ValueError):
        ensure_workspace_shareable(tmp_path, "u1", "seed")


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
    # NAMED `_global`, not "global". Inside the store the organisation tier must BE `_global`:
    # the runtime derives the store subpath from the mount's `path`, which is always
    # `<root>/_global`, so an in-store directory under any other name would mount something other
    # than what the operator configured. global_mount refuses it rather than binding the wrong dir.
    global_dir = tmp_path / "_global"
    global_dir.mkdir()
    settings = SimpleNamespace(
        workspaces_dir=str(tmp_path), global_system_workspace_path=str(global_dir),
        global_system_workspace_ref="", global_admin_subjects="",
    )

    stack = build_mount_set(settings, "contrib1", None)   # no memberships passed → no shared mounts

    assert all(mount["role"] != SHARED_ROLE for mount in stack)


# ── the MOUNT paths must not lose a grant when the derived index is dead or incomplete ────────────
# (The 2026-09-01 dogfood incident: five membership writes 403'd against admin-api and were lost from
# the index while policy/members.json kept the grants — the workspaces became unmountable because every
# mount path enumerated candidates from the index ALONE. Sibling of
# test_workspace_membership.test_api_shared_list_survives_a_dead_index, which covers the LISTING.)
class _DeadIndex:
    """A ``MembershipIndex`` whose remote edge is down — agent-api holding the wrong internal secret,
    every call to admin-api answering 403."""

    def add(self, subject, workspace_id, role, added_at):
        raise RuntimeError("HTTP Error 403: Forbidden")

    def remove(self, subject, workspace_id):
        raise RuntimeError("HTTP Error 403: Forbidden")

    def list(self, subject):
        raise RuntimeError("HTTP Error 403: Forbidden")


class _SpyRuntime(_FakeRuntime):
    """Records every spawn so a test can read the worker env a dispatch produced."""

    def __init__(self):
        self.spawned = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append((workload_id, profile, env))
        return workload_id


def test_active_route_survives_a_dead_index(tmp_path):
    """GET /api/workspace/active must still mount a locally-held grant when the index mirror is
    unreachable — enumeration falls back to the authoritative policy/members.json instead of degrading
    to private-only, and the degradation is SAID (``index_degraded``), never swallowed."""
    _grant(tmp_path, "wsA", owner="owner1", subject="contrib1", role="contributor")
    client = _client(tmp_path, index=_DeadIndex())

    body = client.get("/api/workspace/active", headers=_h("contrib1")).json()

    by_slug = {mount["slug"]: mount for mount in body["active"]}
    assert "wsA" in by_slug, f"the grant is invisible with a dead index: {body}"
    assert by_slug["wsA"]["write"] is True          # the role still comes from the authoritative re-check
    assert body["index_degraded"] is True


def test_active_route_mounts_a_grant_the_index_lost(tmp_path):
    """The incident shape exactly: the index is HEALTHY but MISSING the row (the write 403'd and was
    lost) while policy/members.json holds the grant. The union must still mount it — and a healthy
    index is not reported degraded."""
    _grant(tmp_path, "wsA", owner="owner1", subject="contrib1", role="contributor")
    client = _client(tmp_path, index=m.InMemoryMembershipIndex())   # empty mirror — the rows were lost

    body = client.get("/api/workspace/active", headers=_h("contrib1")).json()

    assert "wsA" in {mount["slug"] for mount in body["active"]}, \
        f"a grant the index lost was dropped from the mount set: {body}"
    assert body["index_degraded"] is False


def test_active_route_union_is_additive_and_deduped(tmp_path):
    """A row in BOTH stores mounts exactly once, and an index row with no local dir (a workspace on
    another host) keeps being enumerated without erroring — the git store only ever ADDS rows the
    index is missing, it never subtracts."""
    idx = _grant(tmp_path, "wsA", owner="owner1", subject="contrib1", role="contributor")
    idx.add("contrib1", "wsElsewhere", "contributor", "2026-01-01T00:00:00Z")   # not materialized here
    client = _client(tmp_path, index=idx)

    body = client.get("/api/workspace/active", headers=_h("contrib1")).json()

    slugs = [mount["slug"] for mount in body["active"]]
    assert slugs.count("wsA") == 1                  # both stores hold the row — mounted exactly once
    assert "wsElsewhere" not in slugs               # another host's workspace stays unmounted here (as before)
    assert body["index_degraded"] is False


def test_dispatch_mounts_survive_a_dead_index(tmp_path):
    """Dispatch mount resolution: with the index edge down, a granted shared workspace still enters
    VEXA_MOUNTS from the authoritative store — previously the dispatcher fell back to 'dispatching
    private mounts only' and the workspace silently vanished from the worker."""
    import json

    _grant(tmp_path, "wsA", owner="owner1", subject="contrib1", role="contributor")
    rt = _SpyRuntime()
    d = Dispatcher(load_settings(workspaces_dir=str(tmp_path)), rt, _FakeIdentity(),
                   membership_index=_DeadIndex())

    d.dispatch({
        "identity": {"subject": "contrib1", "launcher": "schedule:r1"},
        "runner": "claude-code",
        "workspaces": [{"id": "contrib1", "mode": "rw"}],
        "trigger": "scheduled",
        "context": {"kind": "none"},
        "start": {"entrypoint": {"inline": "hi"}},
    })

    _, _profile, env = rt.spawned[0]
    mounts = json.loads(env["VEXA_MOUNTS"])
    shared = [mount for mount in mounts if mount["role"] == SHARED_ROLE]
    assert [mount["slug"] for mount in shared] == ["wsA"], f"shared mount lost with a dead index: {mounts}"
    assert shared[0]["write"] is True


def test_build_active_set_unions_index_rows_with_the_git_store(tmp_path):
    """The mount builder reconciles for itself: the rows a caller passes are the INDEX's view and may
    be incomplete — the authoritative policy/members.json scan is unioned in ([] = the lost-writes
    incident; a foreign-host row enumerates without error). None still means Lane A off."""
    from types import SimpleNamespace
    from control_plane.dispatch import build_active_set

    _grant(tmp_path, "wsA", owner="owner1", subject="contrib1", role="contributor")
    settings = SimpleNamespace(workspaces_dir=str(tmp_path))

    # the incident: the index answered, but with no rows — the git store must still mount the grant
    slugs = [mount["slug"] for mount in build_active_set(settings, "contrib1", [])]
    assert "wsA" in slugs, f"a locally-held grant was dropped: {slugs}"

    # additive: an index row for another host's workspace neither errors nor subtracts the local grant
    rows = [{"workspace_id": "wsElsewhere", "role": "contributor"}]
    slugs = [mount["slug"] for mount in build_active_set(settings, "contrib1", rows)]
    assert "wsA" in slugs and "wsElsewhere" not in slugs

    # Lane A off (no index wired anywhere) keeps meaning exactly that: no shared mounts
    slugs = [mount["slug"] for mount in build_active_set(settings, "contrib1", None)]
    assert "wsA" not in slugs
