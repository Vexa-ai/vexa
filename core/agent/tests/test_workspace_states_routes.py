"""The workspace-state verbs over HTTP (Vexa-ai/vexa#1783): list with states, set states for the
person or for one chat, defaults for new chats, and delete — a shared workspace by its owner only,
naming the members who lose it first. The personal workspace is never hidden, shared or deleted."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import workspace_attach as attach
from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _seed(root: Path, subject: str) -> None:
    from shared.seeding import seed_workspace
    ws = root / subject
    ws.mkdir(parents=True)
    (ws / "CLAUDE.md").write_text("SEED")
    seed_workspace(ws, None)


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "ws"
    for s in ("u1", "u2", "u3"):
        _seed(root, s)
    idx = m.InMemoryMembershipIndex()
    client = TestClient(create_app(Dispatcher(load_settings(), _Runtime(), _Identity()),
                                   reader=WorkspaceReader(str(root)), membership_index=idx))
    return root, idx, client


def _h(s):
    return {"X-User-Id": s}


def _shared(root, idx, name, owner, *members):
    wid = attach.create_shared_workspace_dir(root, name)
    m.ensure_owner(root, wid, owner, index=idx, commit_fn=m.policy_commit)
    for who in members:
        m.grant_membership(root, wid, who, "contributor", added_by=owner, index=idx, commit_fn=m.policy_commit)
    return wid


def _by(view, slug):
    return next((w for w in view["workspaces"] if w["slug"] == slug), None)


def test_states_lists_every_workspace_and_the_scope_line(env):
    root, idx, c = env
    a = attach.create_workspace(root, "u1", name="Alpha").slug
    v = c.get("/api/workspace/states", headers=_h("u1")).json()
    assert _by(v, "personal")["state"] == "active" and _by(v, a)["state"] == "active"
    # LEAN (founder 2026-10-15): no "No profile", no count — the detail is in the menu
    assert v["line"] == "Writes to Personal"
    assert v["chat"]["target"] == "personal"


def test_hide_three_of_five_then_they_leave_every_listing(env):
    root, idx, c = env
    slugs = [attach.create_workspace(root, "u1", name=f"W{i}").slug for i in range(5)]
    r = c.post("/api/workspace/state", headers=_h("u1"),
               json={"workspaces": ["W0", "W1", "W2"], "state": "hidden"})
    assert r.status_code == 200, r.text
    assert "Hidden for you, everywhere" in r.json()["say"]
    v = c.get("/api/workspace/states", headers=_h("u1")).json()
    assert {w["slug"] for w in v["workspaces"]} == {"personal", *slugs[3:]} and v["hidden"] == 3
    allv = c.get("/api/workspace/states?hidden=true", headers=_h("u1")).json()
    assert {_by(allv, s)["state"] for s in slugs[:3]} == {"hidden"}
    # the Files listing and the active set leave them out too
    listed = c.get("/api/workspace/shared", headers=_h("u1")).json()
    assert not {s for s in slugs[:3]} & {r["slug"] for r in listed["all"]}
    active = c.get("/api/workspace/active", headers=_h("u1")).json()
    assert not {s for s in slugs[:3]} & {r["slug"] for r in active["active"]}
    # unhide one: active again
    c.post("/api/workspace/state", headers=_h("u1"), json={"workspaces": ["W1"], "state": "active",
                                                          "scope": "default"})
    v = c.get("/api/workspace/states", headers=_h("u1")).json()
    assert _by(v, slugs[1])["state"] == "active"


def test_hiding_personal_takes_it_out_of_listings_chats_and_mounts(env):
    root, idx, c = env
    a = attach.create_workspace(root, "u1", name="Alpha").slug
    r = c.post("/api/workspace/state", headers=_h("u1"), json={"workspaces": ["Personal"], "state": "hidden"})
    assert r.status_code == 200, r.text
    v = c.get("/api/workspace/states?session=chat-p", headers=_h("u1")).json()
    assert all(w["slug"] != "personal" for w in v["workspaces"])
    assert "personal" not in v["chat"]["reads"] and v["chat"]["target"] != "personal"
    assert "Personal" not in v["line"]
    listed = c.get("/api/workspace/shared", headers=_h("u1")).json()["all"]
    assert all(r["slug"] != "personal" for r in listed)
    from control_plane.dispatch import build_mount_set
    from shared.config import load_settings
    mounts = build_mount_set(load_settings(workspaces_dir=str(root)), "u1", [], reads=None, target="")
    assert not any(m.get("path") == f"{root}/u1" for m in mounts), "the desk is not on the worker's disk"


def test_chat_scope_changes_only_that_chat(env):
    root, idx, c = env
    a = attach.create_workspace(root, "u1", name="Alpha").slug
    r = c.post("/api/workspace/state", headers=_h("u1"),
               json={"workspaces": ["Alpha"], "state": "inactive", "session": "chat-1"})
    assert r.status_code == 200, r.text
    one = c.get("/api/workspace/states?session=chat-1", headers=_h("u1")).json()
    two = c.get("/api/workspace/states?session=chat-2", headers=_h("u1")).json()
    assert a not in one["chat"]["reads"] and one["chat"]["own_reads"] is True
    assert a in two["chat"]["reads"] and two["chat"]["own_reads"] is False
    assert _by(one, a)["state"] == "active", "the person's default did not move"


def test_a_worker_changes_its_own_chat_from_the_signed_unit(env):
    root, idx, c = env
    a = attach.create_workspace(root, "u1", name="Alpha").slug
    h = {**_h("u1"), "x-user-regime": "human", "x-user-delegation-unit": "agent-u1-chat-chat-9-g1"}
    r = c.post("/api/workspace/state", headers=h,
               json={"workspaces": ["Alpha"], "state": "inactive", "session": "someone-elses"})
    assert r.status_code == 200, r.text
    assert a not in c.get("/api/workspace/states?session=chat-9", headers=_h("u1")).json()["chat"]["reads"]
    assert a in c.get("/api/workspace/states?session=someone-elses", headers=_h("u1")).json()["chat"]["reads"]


def test_an_unwatched_worker_cannot_change_states(env):
    _, _, c = env
    h = {**_h("u1"), "x-user-regime": "autonomous", "x-user-delegation-unit": "agent-u1-routine-x"}
    r = c.post("/api/workspace/state", headers=h, json={"workspaces": "all", "state": "hidden"})
    assert r.status_code == 403


def test_hiding_shared_is_per_member(env):
    root, idx, c = env
    wid = _shared(root, idx, "Team", "u1", "u2")
    c.post("/api/workspace/state", headers=_h("u2"), json={"workspaces": [wid], "state": "hidden"})
    assert _by(c.get("/api/workspace/states", headers=_h("u2")).json(), wid) is None
    assert _by(c.get("/api/workspace/states", headers=_h("u1")).json(), wid)["state"] == "active"
    assert m.is_member(root, wid, "u2") == "contributor"


def test_defaults_set_target_and_active_set(env):
    root, idx, c = env
    a = attach.create_workspace(root, "u1", name="Alpha").slug
    b = attach.create_workspace(root, "u1", name="Beta").slug
    r = c.post("/api/workspace/defaults", headers=_h("u1"), json={"target": "Alpha", "active": ["Personal", "Alpha"]})
    assert r.status_code == 200, r.text
    v = c.get("/api/workspace/states", headers=_h("u1")).json()
    assert v["defaults"]["target"] == a
    assert _by(v, b)["state"] == "inactive" and _by(v, a)["state"] == "active"
    assert c.get("/api/chat/default-target", headers=_h("u1")).json()["target"] == a


def test_delete_shared_sole_owner(env):
    root, idx, c = env
    wid = _shared(root, idx, "Solo", "u1")
    r = c.delete(f"/api/workspace/{wid}", headers=_h("u1"))
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert not (root / wid).exists()


def test_delete_shared_with_members_names_them_first(env):
    root, idx, c = env
    wid = _shared(root, idx, "Team", "u1", "u2")
    r = c.delete(f"/api/workspace/{wid}", headers=_h("u1"))
    assert r.status_code == 409
    assert "u2" in " ".join(r.json()["detail"]["members"])
    assert (root / wid).exists()
    r = c.delete(f"/api/workspace/{wid}?confirm=true", headers=_h("u1"))
    assert r.status_code == 200 and not (root / wid).exists()
    assert m.is_member(root, wid, "u2") is None


def test_delete_shared_refused_for_a_non_owner(env):
    root, idx, c = env
    wid = _shared(root, idx, "Team", "u1", "u2")
    r = c.delete(f"/api/workspace/{wid}?confirm=true", headers=_h("u2"))
    assert r.status_code == 403 and (root / wid).exists()


def test_personal_cannot_be_deleted_and_the_refusal_offers_hide(env):
    _, _, c = env
    r = c.delete("/api/workspace/personal", headers=_h("u1"))
    assert r.status_code == 400
    d = r.json()["detail"]
    assert "can't be deleted" in d and "hide" in d.lower()


def test_hiding_the_chats_target_moves_its_writes_back_to_personal(env):
    root, idx, c = env
    a = attach.create_workspace(root, "u1", name="Alpha").slug
    r = c.post("/api/chat/target", headers=_h("u1"), json={"session": "chat-1", "workspace": a})
    assert r.status_code == 200, r.text
    c.post("/api/workspace/state", headers=_h("u1"), json={"workspaces": ["Alpha"], "state": "hidden", "session": "chat-1"})
    v = c.get("/api/workspace/states?session=chat-1", headers=_h("u1")).json()
    assert v["chat"]["target"] == "personal"



def test_hiding_remounts_every_warm_chat_of_the_person_on_its_next_turn(tmp_path):
    """Proven on app.dev: a hide in a chat left the hidden tree bound into that chat's warm worker."""
    from control_plane.api import _Sessions
    root = tmp_path / "ws"
    _seed(root, "u1")
    sessions = _Sessions()
    c = TestClient(create_app(Dispatcher(load_settings(), _Runtime(), _Identity()),
                              reader=WorkspaceReader(str(root)), membership_index=m.InMemoryMembershipIndex(),
                              sessions=sessions))
    attach.create_workspace(root, "u1", name="Alpha")
    for s in ("chat-1", "chat-2"):
        sessions.set_target("u1", s, "", restart=False)
        sessions.upsert("u1", s, title=s)
    before = {s: sessions.mount_gen("u1", s) for s in ("chat-1", "chat-2")}
    r = c.post("/api/workspace/state", headers=_h("u1"), json={"workspaces": ["Alpha"], "state": "hidden"})
    assert r.status_code == 200
    for s in ("chat-1", "chat-2"):
        assert sessions.take_mount_generation("u1", s) == before[s] + 1


# ── founder frictions 2026-10-14 11:30–12:33 ─────────────────────────────────────────────────────

def test_all_is_accepted_as_a_string_and_as_a_list_and_all_except(env):
    root, idx, c = env
    for n in ("A", "B", "C"):
        attach.create_workspace(root, "u1", name=n)
    r = c.post("/api/workspace/state", headers=_h("u1"), json={"workspaces": "all", "state": "hidden"})
    assert r.status_code == 200, r.text
    r = c.post("/api/workspace/state", headers=_h("u1"),
               json={"workspaces": ["all"], "excluding": ["B"], "state": "active", "scope": "default"})
    assert r.status_code == 200, r.text
    v = c.get("/api/workspace/states?hidden=true", headers=_h("u1")).json()
    st = {w["name"]: w["state"] for w in v["workspaces"]}
    assert st["A"] == "active" and st["C"] == "active" and st["B"] == "hidden"


def test_a_chat_with_no_workspaces_mounts_nothing_and_says_so(env):
    root, idx, c = env
    attach.create_workspace(root, "u1", name="A")
    r = c.post("/api/workspace/state", headers=_h("u1"),
               json={"workspaces": "all", "state": "inactive", "session": "chat-x"})
    assert r.status_code == 200, r.text
    v = c.get("/api/workspace/states?session=chat-x", headers=_h("u1")).json()
    assert v["chat"]["reads"] == [] and v["chat"]["target"] is None
    assert v["line"] == "No workspaces"
    # and back: "write here" on Personal brings a target
    c.post("/api/chat/target", headers=_h("u1"), json={"session": "chat-x", "workspace": ""})
    v = c.get("/api/workspace/states?session=chat-x", headers=_h("u1")).json()
    assert v["chat"]["target"] == "personal"


def test_unselecting_the_target_alone_never_reports_a_change_that_did_not_happen(env):
    root, idx, c = env
    r = c.post("/api/workspace/state", headers=_h("u1"),
               json={"workspaces": ["personal"], "state": "inactive", "session": "chat-y"})
    assert r.status_code == 200, r.text
    body = r.json()
    v = c.get("/api/workspace/states?session=chat-y", headers=_h("u1")).json()
    # the only workspace was the target: the chat now has none, and says so
    assert v["chat"]["reads"] == [] and v["chat"]["target"] is None
    assert body["changed"] == ["personal"]


def test_workspace_new_cuts_a_long_name_at_a_word_and_takes_a_chosen_slug(env):
    _, _, c = env
    r = c.post("/api/workspace/new", headers=_h("u1"),
               json={"name": "AI Tinkerers Lisbon — Web Summit side event"})
    assert r.status_code == 201
    slug = r.json()["slug"]
    assert slug == "ai-tinkerers-lisbon-web-summit-side" and "slug" in r.json()["say"]
    r = c.post("/api/workspace/new", headers=_h("u1"), json={"name": "Anything", "slug": "tinkerers"})
    assert r.status_code == 201 and r.json()["slug"] == "tinkerers"
    r = c.post("/api/workspace/new", headers=_h("u1"), json={"name": "X", "slug": "Bad Slug!"})
    assert r.status_code == 422
