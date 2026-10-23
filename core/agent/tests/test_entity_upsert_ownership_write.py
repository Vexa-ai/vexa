"""`_read_target`'s write path — ownership, not mount state (ledger F196/F198/F200, live agent,
2026-09-03).

Live repro: the agent was the confirmed owner of a shared workspace (`workspaces()` said so, and
it had just written into that same directory via `workspace_write` moments earlier in the SAME
session) and `entity_upsert(..., slug="zenith-c172ae", ...)` still came back
`{"error": "the entity could not be written", "status": 403, "detail": "not authorized for this
workspace"}`. A second turn in the same session got the OTHER half of the same bug: with no slug
at all, `entity_upsert` silently wrote three pages into the personal desk instead of the shared
workspace all the surrounding context was about.

Root cause: `_read_target(request, slug, write=True)` (`control_plane/api.py`) authorizes a write
by checking whether `slug` is in the caller's currently ACTIVE mount set (`shared_active_mounts`)
— and that set drops any shared workspace the subject has switched off (`hidden_shared_set`),
which is a per-user DISPLAY toggle, not a membership change. So a real owner/contributor whose
workspace happens not to be "on" right now got the identical 403 a stranger gets.

Reuses `test_lane_a_shared_mounts.py`'s scaffolding (`_init_ws`, real git-backed
`policy/members.json`) rather than re-inventing it — the fix must hold against the same
authoritative membership store Lane A itself reads.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane import workspace_attach as wa
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


def _init_ws(root: Path, workspace_id: str) -> Path:
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


def test_an_owner_can_write_even_when_the_workspace_is_not_currently_mounted(tmp_path):
    """The exact repro: `owner1` genuinely owns `zenith`, but has switched it off (or it was
    simply never turned on for this session/dispatch) — `shared_active_mounts` will not list it,
    yet the write must still land, because ownership is unchanged."""
    idx = m.InMemoryMembershipIndex()
    _init_ws(tmp_path, "zenith")
    m.ensure_owner(tmp_path, "zenith", "owner1", index=idx)
    wa.set_shared_active(tmp_path, "owner1", "zenith", active=False)  # switched OFF
    assert "zenith" not in {mnt.slug for mnt in wa.shared_active_mounts(
        tmp_path, "owner1", idx.list("owner1"))}, "test setup: must actually be un-mounted"

    c = _client(tmp_path, idx)
    r = c.post("/api/workspace/entity", headers=_h("owner1"), json={
        "kind": "project", "name": "Zenith SIG", "slug": "zenith",
        "facts": ["Met to plan the FINOS submission."], "source": "the 2026-09-03 call"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert (tmp_path / "zenith" / body["path"]).is_file(), \
        "wrote somewhere other than the owned shared workspace"


def test_a_contributor_can_write_even_when_the_workspace_is_not_currently_mounted(tmp_path):
    idx = m.InMemoryMembershipIndex()
    _init_ws(tmp_path, "zenith")
    m.ensure_owner(tmp_path, "zenith", "owner1", index=idx)
    m.grant_membership(tmp_path, "zenith", "contrib1", "contributor", added_by="owner1", index=idx)
    wa.set_shared_active(tmp_path, "contrib1", "zenith", active=False)

    c = _client(tmp_path, idx)
    r = c.post("/api/workspace/entity", headers=_h("contrib1"), json={
        "kind": "project", "name": "Zenith SIG", "slug": "zenith",
        "facts": ["Met today."], "source": "the 2026-09-03 call"})
    assert r.status_code == 200, r.text


def test_a_viewer_still_cannot_write_even_though_they_are_a_real_member(tmp_path):
    """The fix reads REAL role, not just real membership — a viewer 403s exactly as before."""
    idx = m.InMemoryMembershipIndex()
    _init_ws(tmp_path, "zenith")
    m.ensure_owner(tmp_path, "zenith", "owner1", index=idx)
    m.grant_membership(tmp_path, "zenith", "viewer1", "viewer", added_by="owner1", index=idx)

    c = _client(tmp_path, idx)
    r = c.post("/api/workspace/entity", headers=_h("viewer1"), json={
        "kind": "project", "name": "Zenith SIG", "slug": "zenith",
        "facts": ["Met today."], "source": "the 2026-09-03 call"})
    assert r.status_code == 403, r.text


def test_a_stranger_still_cannot_write_a_shared_workspace_they_never_joined(tmp_path):
    """The negative control: nothing here widens write access for someone with no membership row
    at all — the whole point of reading `require_role` rather than trusting a slug."""
    idx = m.InMemoryMembershipIndex()
    _init_ws(tmp_path, "zenith")
    m.ensure_owner(tmp_path, "zenith", "owner1", index=idx)

    c = _client(tmp_path, idx)
    r = c.post("/api/workspace/entity", headers=_h("nobody"), json={
        "kind": "project", "name": "Zenith SIG", "slug": "zenith",
        "facts": ["Met today."], "source": "the 2026-09-03 call"})
    assert r.status_code == 403, r.text
