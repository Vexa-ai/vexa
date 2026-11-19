"""A PERSON'S DEFAULT TARGET: the workspace their NEW chats write to (founder 2026-10-13).

The header's "Writes to …" control moves one chat. A person who works in one workspace all day wants
every new chat to start there instead of on Personal. So agent-api keeps, per person, one default
target, set from that menu ("Make default"):

  1. it is stored on the server, per person — it follows them across devices, nobody else reads it;
  2. it is accepted only where the person can WRITE, by the same check a chat's target gets;
  3. it is RE-CHECKED every time it is read: removed from the workspace, the workspace gone, or the
     role lowered to reader, and the answer is "Personal" — and the workspace is not named, because
     the person may no longer be able to see it;
  4. it never moves a chat that exists: only the terminal's new chats start from it.

L2: a real FastAPI app over fakes, no redis, no runtime.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import workspace_membership as wm
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_attach import create_shared_workspace_dir
from control_plane.workspace_membership import InMemoryMembershipIndex, ensure_owner
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

PERSON, OTHER = "u_priya", "u_omar"


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _Reader:
    def read(self, unit_id, resume=None, fresh=False):
        yield {"type": "turn-complete"}


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    wid = create_shared_workspace_dir(root, "Biz")
    ensure_owner(root, wid, PERSON, index=InMemoryMembershipIndex())
    sessions = _Sessions()
    app = create_app(Dispatcher(load_settings(workspaces_dir=str(root)), _Runtime(), _Identity()),
                     stream_reader=_Reader(), reader=WorkspaceReader(str(root)), sessions=sessions)
    return {"client": TestClient(app), "root": root, "wid": wid, "sessions": sessions}


def _h(who=PERSON):
    return {"X-User-Id": who}


def _set(stack, wid, who=PERSON):
    return stack["client"].put("/api/chat/default-target", json={"workspace": wid}, headers=_h(who))


def _get(stack, who=PERSON):
    r = stack["client"].get("/api/chat/default-target", headers=_h(who))
    assert r.status_code == 200
    return r.json()


def _set_role(stack, role):
    # the roster's authority is the policy store (PRD 0.13.4 D5), not the tree's mirror
    rows = wm.read_members(stack["root"], stack["wid"])
    for row in rows:
        if row.get("subject") == PERSON:
            row["role"] = role
    wm.write_policy(stack["root"], stack["wid"], "members", rows)


def _remove(stack):
    rows = [r for r in wm.read_members(stack["root"], stack["wid"]) if r.get("subject") != PERSON]
    wm.write_policy(stack["root"], stack["wid"], "members", rows)


# ── 1. stored per person ────────────────────────────────────────────────────────────────────────

def test_no_default_is_personal(stack):
    assert _get(stack) == {"target": None}


def test_a_workspace_the_person_writes_becomes_their_default(stack):
    r = _set(stack, stack["wid"])
    assert r.status_code == 200 and r.json() == {"target": stack["wid"]}
    assert _get(stack) == {"target": stack["wid"]}


def test_the_default_is_the_persons_own(stack):
    _set(stack, stack["wid"])
    assert _get(stack, OTHER) == {"target": None}


def test_personal_clears_it(stack):
    _set(stack, stack["wid"])
    assert _set(stack, "").json() == {"target": None}
    assert _get(stack) == {"target": None}


# ── 2. accepted only where the person can write ────────────────────────────────────────────────

def test_a_workspace_the_person_cannot_write_is_refused_and_not_stored(stack):
    r = _set(stack, stack["wid"], OTHER)
    assert r.status_code == 403
    assert _get(stack, OTHER) == {"target": None}


def test_a_reader_cannot_make_it_their_default(stack):
    _set_role(stack, "viewer")
    assert _set(stack, stack["wid"]).status_code == 403


def test_a_malformed_slug_is_a_400(stack):
    assert _set(stack, "grp/../_global").status_code in (400, 422)


def test_the_company_layer_is_not_an_everyday_default(stack):
    assert _set(stack, "_global").status_code == 403


# ── 3. re-checked on every read ─────────────────────────────────────────────────────────────────

def test_removed_from_the_workspace_falls_back_to_personal_without_naming_it(stack):
    _set(stack, stack["wid"])
    _remove(stack)
    body = _get(stack)
    assert body == {"target": None}
    assert stack["wid"] not in str(body)


def test_a_role_lowered_to_reader_falls_back_to_personal(stack):
    _set(stack, stack["wid"])
    _set_role(stack, "viewer")
    assert _get(stack) == {"target": None}


def test_a_deleted_workspace_falls_back_to_personal(stack):
    import shutil
    _set(stack, stack["wid"])
    shutil.rmtree(wm._ws_dir(stack["root"], stack["wid"]))
    assert _get(stack) == {"target": None}


def test_writable_again_brings_the_default_back(stack):
    _set(stack, stack["wid"])
    _set_role(stack, "viewer")
    assert _get(stack) == {"target": None}
    _set_role(stack, "contributor")
    assert _get(stack) == {"target": stack["wid"]}


# ── 4. existing chats keep theirs ───────────────────────────────────────────────────────────────

def test_setting_the_default_moves_no_existing_chat(stack):
    c = stack["client"]
    assert c.post("/api/chat", json={"prompt": "hi", "session": "pchat-old"}, headers=_h()).status_code == 200
    _set(stack, stack["wid"])
    assert stack["sessions"].target(PERSON, "pchat-old") == ""


# ── 5. a NEW chat writes to the default (founder 2026-10-14: "default set to biz but it's writing
#       in personal") — applied by the server on the chat's first turn, and shown in its header ──

def test_a_new_chat_writes_to_the_default_from_its_first_turn(stack):
    c = stack["client"]
    _set(stack, stack["wid"])
    head = c.get("/api/workspace/states?session=pchat-new", headers=_h()).json()
    name = next(w["display"] for w in head["workspaces"] if w["slug"] == stack["wid"])
    assert head["chat"]["target"] == stack["wid"] and head["line"] == f"Writes to {name}"
    assert c.post("/api/chat", json={"prompt": "note this", "session": "pchat-new"}, headers=_h()).status_code == 200
    assert stack["sessions"].target(PERSON, "pchat-new") == stack["wid"]
    assert c.get("/api/workspace/states?session=pchat-new", headers=_h()).json()["chat"]["target"] == stack["wid"]


def test_a_new_chat_whose_default_is_hidden_writes_to_personal_and_says_why(stack):
    c = stack["client"]
    _set(stack, stack["wid"])
    c.post("/api/workspace/state", headers=_h(), json={"workspaces": [stack["wid"]], "state": "hidden"})
    head = c.get("/api/workspace/states?session=pchat-new2", headers=_h()).json()
    assert head["chat"]["target"] == "personal" and "hidden" in head["chat"]["note"]
    c.post("/api/chat", json={"prompt": "hi", "session": "pchat-new2"}, headers=_h())
    assert stack["sessions"].target(PERSON, "pchat-new2") == ""


def test_the_defaults_name_the_default_target(stack):
    _set(stack, stack["wid"])
    v = stack["client"].get("/api/workspace/states", headers=_h()).json()
    name = next(w["display"] for w in v["workspaces"] if w["slug"] == stack["wid"])
    assert v["defaults"]["target"] == stack["wid"] and v["defaults"]["target_name"] == name


def test_two_workspaces_with_one_name_are_told_apart(stack):
    from control_plane.workspace_attach import create_workspace
    root = stack["root"]
    (root / PERSON).mkdir(exist_ok=True)
    create_workspace(root, PERSON, name="Biz")
    create_workspace(root, PERSON, name="Biz")
    rows = stack["client"].get("/api/workspace/states", headers=_h()).json()["workspaces"]
    biz = [r for r in rows if r["name"] == "Biz"]
    assert len(biz) == 2 and len({r["display"] for r in biz}) == 2
    assert all("yours" in r["display"] and "empty" in r["display"] for r in biz)
    listed = stack["client"].get("/api/workspace/shared", headers=_h()).json()["all"]
    assert len({r["display"] for r in listed if r["name"] == "Biz"}) == 2
