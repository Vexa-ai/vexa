"""A slug or workspace id names ONE workspace of the caller's own store, or of a workspace they belong to.

Two subjects share one store: `u_jane` (the caller) and `u_mallory` (the other tenant). Mallory has a
live desk, a parked workspace and a chat thread. Every route that takes a slug or a workspace id is
driven by Jane with every shape that could name one of Mallory's trees —
a traversal, an absolute path, Mallory's own name, an encoded separator, a dotted store path — and
each must refuse without moving, mounting, reading or echoing anything of Mallory's.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import workspace_attach as wa
from control_plane import workspace_membership as membership
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


def _git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True)


def _repo(d: Path, marker: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "MARK").write_text(marker)
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "init")
    return d


def _thread(ws: Path, session: str, sid: str, text: str) -> None:
    (ws / ".claude" / "sessions").mkdir(parents=True, exist_ok=True)
    (ws / ".claude" / "sessions" / f"{session}.session").write_text(sid + "\n")
    proj = ws / ".claude" / "projects" / "-workspace"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{sid}.jsonl").write_text(json.dumps(
        {"type": "user", "message": {"role": "user", "content": text}}) + "\n")


@pytest.fixture()
def store(tmp_path):
    root = tmp_path / "store"
    _repo(root / "u_jane", "JANE DESK")
    _repo(root / "u_mallory", "MALLORY DESK")
    # Mallory's parked workspace — with a member list left inside it, the shape an un-shared group has
    parked = _repo(root / ".attached" / "u_mallory" / "secret-slot", "MALLORY PARKED")
    (parked / "policy").mkdir()
    (parked / "policy" / "members.json").write_text(json.dumps(
        [{"subject": "u_mallory", "role": "owner"}, {"subject": "u_jane", "role": "contributor"}]))
    (root / ".attached" / "u_mallory" / "state.json").write_text(json.dumps(
        {"active": None, "slots": {"secret-slot": {"repo": None, "ref": None}},
         "active_set": ["seed", "secret-slot"], "_flat_v1": True}))
    # Mallory's chat threads: one on her desk, one in her parked workspace
    _thread(root / "u_mallory", "main", "sid-mallory-desk", "MALLORY SAID THIS")
    _thread(parked, "chat-z", "sid-mallory-parked", "MALLORY PARKED THREAD")
    # A group Jane owns (the shared attach route's target), already attached once, so its store exists
    grp = _repo(root / "grp", "GROUP")
    (grp / "policy").mkdir()
    (grp / "policy" / "members.json").write_text(json.dumps([{"subject": "u_jane", "role": "owner"}]))
    (root / ".attached-shared" / "grp").mkdir(parents=True)
    (root / ".attached-shared" / "grp" / "state.json").write_text(json.dumps({"active": None, "slots": {}}))
    # Jane has used the switcher before, so her own store exists — the shape every real caller has
    (root / ".attached" / "u_jane").mkdir(parents=True)
    (root / ".attached" / "u_jane" / "state.json").write_text(json.dumps(
        {"active": None, "slots": {}, "active_set": ["seed"], "_flat_v1": True}))
    return root


def _client(root: Path) -> TestClient:
    return TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(root)), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root))))


JANE = {"X-User-Id": "u_jane"}

#: Every shape a slug could take to name a tree that is not one of the caller's slots.
FOREIGN_SLUGS = [
    "../u_mallory/secret-slot",                 # traversal into another subject's store
    "../../u_mallory",                          # traversal to another subject's live desk
    "..",                                       # the store of every subject
    ".",
    "u_mallory/../../u_mallory",                # descends first, escapes after
    "{abs}",                                    # absolute — discards the store it is joined to
    "..%2Fu_mallory%2Fsecret-slot",             # an encoded separator
    "..\\u_mallory",                            # the other separator
    ".attached/u_mallory/secret-slot",          # a dotted store path
    "secret-slot\n",
]


def _foreign(slug: str, root: Path) -> str:
    """The slug, with ``{abs}`` filled in as the ABSOLUTE path of Mallory's parked workspace — an
    absolute path inside the store, never one on the host (the unfixed code moves what it names)."""
    return slug.replace("{abs}", str(root / ".attached" / "u_mallory" / "secret-slot"))


def _mallory_intact(root: Path) -> None:
    assert (root / "u_mallory" / "MARK").read_text() == "MALLORY DESK"
    assert (root / ".attached" / "u_mallory" / "secret-slot" / "MARK").read_text() == "MALLORY PARKED"
    assert (root / "u_jane" / "MARK").read_text() == "JANE DESK"


# ── the mechanic: no slug outside the store, whoever calls it ────────────────────────────────────

@pytest.mark.parametrize("slug", FOREIGN_SLUGS)
def test_swap_refuses_a_slug_that_is_not_one_of_the_callers_slots(store, slug):
    with pytest.raises(KeyError):
        wa.swap_workspace(store, "u_jane", None, slug=_foreign(slug, store))
    _mallory_intact(store)


@pytest.mark.parametrize("slug", FOREIGN_SLUGS)
def test_activate_refuses_a_slug_that_is_not_one_of_the_callers_slots(store, slug):
    with pytest.raises(KeyError):
        wa.activate_workspace(store, "u_jane", None, slug=_foreign(slug, store))
    _mallory_intact(store)
    assert all("u_mallory" not in m.path for m in wa.active_workspaces(store, "u_jane"))


@pytest.mark.parametrize("slug", FOREIGN_SLUGS)
def test_shared_attach_refuses_a_slug_outside_the_groups_store(store, slug):
    with pytest.raises(KeyError):
        wa.attach_shared_workspace(store, "grp", None, slug=_foreign(slug, store))
    _mallory_intact(store)
    assert (store / "grp" / "MARK").read_text() == "GROUP"


@pytest.mark.parametrize("slug", FOREIGN_SLUGS)
def test_share_enable_refuses_a_slug_that_is_not_one_of_the_callers_slots(store, slug):
    with pytest.raises((KeyError, membership.MembershipError)):
        wa.ensure_workspace_shareable(store, "u_jane", _foreign(slug, store))
    _mallory_intact(store)


@pytest.mark.parametrize("wid", ["../u_mallory", ".attached/u_mallory/secret-slot", "u_mallory/..", "{abs}", ".."])
def test_unshare_refuses_an_id_that_is_not_one_top_level_workspace(store, wid):
    with pytest.raises(KeyError):
        wa.ensure_workspace_private(store, "u_jane", _foreign(wid, store))
    _mallory_intact(store)


@pytest.mark.parametrize("subject", ["", "u_jane/../u_mallory", "a/b", "..", ".attached"])
def test_a_subject_is_one_name(store, subject):
    with pytest.raises(ValueError):
        wa.attached_workspaces(store, subject)


def test_a_poisoned_active_set_entry_is_never_mounted(store):
    """State written before this rule existed may hold a slug that names another tree. It is dropped
    from the mount set rather than resolved."""
    jane_store = store / ".attached" / "u_jane"
    (jane_store / "state.json").write_text(json.dumps(
        {"active": None, "slots": {"../u_mallory/secret-slot": {"repo": None, "ref": None}},
         "active_set": ["seed", "../u_mallory/secret-slot"], "_flat_v1": True}))
    assert [m.slug for m in wa.active_workspaces(store, "u_jane")] == ["seed"]


@pytest.mark.parametrize("wid", [".attached/u_mallory/secret-slot", "a/b", "..", "", ".hidden", "x\\y"])
def test_a_membership_question_takes_one_top_level_name(store, wid):
    """The member list left inside Mallory's parked tree names Jane — and is never consulted, because a
    store path is not a workspace id."""
    with pytest.raises(membership.MembershipError):
        membership.is_member(store, wid, "u_jane")


def test_the_callers_own_slots_still_work(store, tmp_path):
    origin = _repo(tmp_path / "origin", "ORIGIN")
    slug = wa.activate_workspace(store, "u_jane", str(origin), "main").slug
    assert wa.deactivate_workspace(store, "u_jane", slug).changed
    assert wa.activate_workspace(store, "u_jane", None, slug=slug).changed
    swapped = wa.swap_workspace(store, "u_jane", None, slug=slug)
    assert swapped.swapped and (store / "u_jane" / "MARK").read_text() == "ORIGIN"
    back = wa.swap_workspace(store, "u_jane", None, slug="seed")
    assert back.swapped and (store / "u_jane" / "MARK").read_text() == "JANE DESK"
    _mallory_intact(store)


# ── the routes ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("slug", FOREIGN_SLUGS)
def test_swap_and_activate_routes_refuse_every_foreign_slug(store, slug):
    c = _client(store)
    for route in ("/api/workspace/swap", "/api/workspace/activate"):
        r = c.post(route, json={"slug": _foreign(slug, store)}, headers=JANE)
        assert r.status_code in (400, 404), (route, slug, r.status_code, r.text)
    active = c.get("/api/workspace/active", headers=JANE).json()["active"]
    assert all("u_mallory" not in m["path"] for m in active)
    _mallory_intact(store)


@pytest.mark.parametrize("slug", FOREIGN_SLUGS)
def test_shared_attach_route_refuses_every_foreign_slug(store, slug):
    r = _client(store).post("/api/workspace/shared/grp/attach", json={"slug": _foreign(slug, store)},
                            headers=JANE)
    assert r.status_code in (400, 404), (slug, r.status_code, r.text)
    _mallory_intact(store)


@pytest.mark.parametrize("seg", ["%2e%2e", "%2E%2E", "..%2Fu_mallory", "%2e"])
def test_path_parameter_routes_refuse_an_encoded_traversal(store, seg):
    c = _client(store)
    for method, route in (("POST", f"/api/workspace/{seg}/share-enable"),
                          ("POST", f"/api/workspace/{seg}/unshare"),
                          ("POST", f"/api/workspace/shared/{seg}/attach"),
                          ("POST", f"/api/workspace/{seg}/archive"),
                          ("DELETE", f"/api/workspace/{seg}")):
        r = c.request(method, route, headers=JANE, json={})
        assert r.status_code in (400, 403, 404, 405, 422), (route, r.status_code, r.text)
    _mallory_intact(store)
    assert sorted(p.name for p in (store / ".attached").iterdir()) == ["u_jane", "u_mallory"]


@pytest.mark.parametrize("slug", [".attached/u_mallory/secret-slot", "../u_mallory", "{abs}", "u_mallory/.."])
def test_manage_and_read_routes_refuse_a_store_path_with_a_stale_member_list(store, slug):
    """Mallory's parked tree still carries a member list naming Jane. Neither the management door
    (purpose) nor the read door (tree, file) treats a store path as a shared workspace."""
    c = _client(store)
    slug = _foreign(slug, store)
    assert c.get("/api/workspace/purpose", params={"slug": slug}, headers=JANE).status_code in (400, 403, 404)
    assert c.post("/api/workspace/purpose", json={"slug": slug, "purpose": "mine now"},
                  headers=JANE).status_code in (400, 403, 404)
    tree = c.get("/api/workspace/tree", params={"slug": slug}, headers=JANE)
    assert tree.status_code in (400, 403, 404)
    f = c.get("/api/workspace/file", params={"slug": slug, "path": "MARK"}, headers=JANE)
    assert f.status_code in (400, 403, 404) and "MALLORY" not in f.text
    assert not (store / ".attached" / "u_mallory" / "secret-slot" / ".vexa" / "purpose").exists()
    _mallory_intact(store)
