"""CHAT PROFILES (Vexa-ai/vexa#1783, founder 2026-10-14): named presets of a chat's scope — where it
writes, what it reads, and a memory mode. The only built-in is
incognito (no workspaces, no memory, never read by another chat, out of chat search). Stored in the
person's attach state — no new store. One default profile for new chats."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import workspace_attach as attach
from control_plane import workspace_membership as m
from control_plane.api import _Sessions, create_app
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


class _Reader:
    def read(self, unit_id, resume=None, fresh=False):
        yield {"type": "turn-complete"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "ws"
    (root / "_global").mkdir(parents=True)
    from shared.seeding import seed_workspace
    (root / "u1").mkdir(parents=True)
    (root / "u1" / "CLAUDE.md").write_text("SEED")
    seed_workspace(root / "u1", None)
    sessions = _Sessions()
    c = TestClient(create_app(Dispatcher(load_settings(workspaces_dir=str(root)), _Runtime(), _Identity()),
                              stream_reader=_Reader(), reader=WorkspaceReader(str(root)),
                              membership_index=m.InMemoryMembershipIndex(), sessions=sessions))
    return root, sessions, c


H = {"X-User-Id": "u1"}


def test_starter_profiles_exist(env):
    _, _, c = env
    names = [p["name"] for p in c.get("/api/workspace/profiles", headers=H).json()["profiles"]]
    # founder 2026-10-15: "remove the prebuilt profiles, they will create one if needed" — only
    # Incognito is built in; everything else is a profile the person made.
    assert names == ["incognito"]


def test_edit_a_profile_and_use_it_in_a_chat(env):
    root, sessions, c = env
    a = attach.create_workspace(root, "u1", name="Acme").slug
    r = c.post("/api/workspace/profiles/edit", headers=H, json={"name": "work", "target": "Acme", "reads": ["Acme"]})
    assert r.status_code == 200, r.text
    r = c.post("/api/workspace/profile", headers=H, json={"name": "work", "session": "chat-w"})
    assert r.status_code == 200, r.text
    v = c.get("/api/workspace/states?session=chat-w", headers=H).json()
    assert v["chat"]["target"] == a and v["chat"]["reads"] == [a] and v["chat"]["profile"] == "work"


def test_incognito_mounts_nothing_and_says_so(env):
    root, sessions, c = env
    c.post("/api/workspace/profile", headers=H, json={"name": "incognito", "session": "chat-i"})
    v = c.get("/api/workspace/states?session=chat-i", headers=H).json()
    assert v["chat"]["target"] is None and v["chat"]["reads"] == [] and v["chat"]["profile"] == "incognito"
    assert v["line"] == "Incognito"
    assert sessions.no_target("u1", "chat-i") and sessions.profile("u1", "chat-i") == "incognito"


def test_the_default_profile_applies_to_a_new_chat_on_its_first_turn(env):
    root, sessions, c = env
    a = attach.create_workspace(root, "u1", name="Acme").slug
    c.post("/api/workspace/profiles/edit", headers=H, json={"name": "work", "target": "Acme", "reads": ["Acme", "personal"]})
    assert c.post("/api/workspace/profiles/edit", headers=H, json={"name": "work", "default": True}).status_code == 200
    assert c.post("/api/chat", json={"prompt": "hi", "session": "pchat-new"}, headers=H).status_code == 200
    assert sessions.target("u1", "pchat-new") == a and sessions.profile("u1", "pchat-new") == "work"


def test_an_unknown_profile_is_a_404(env):
    _, _, c = env
    assert c.post("/api/workspace/profile", headers=H, json={"name": "nope", "session": "x"}).status_code == 404


def test_an_idle_incognito_chat_is_deleted_on_schedule_and_others_stay(env, monkeypatch):
    root, sessions, c = env
    for s in ("pchat-inc", "pchat-keep"):
        assert c.post("/api/chat", json={"prompt": "hi", "session": s}, headers=H).status_code == 200
    c.post("/api/workspace/profile", headers=H, json={"name": "incognito", "session": "pchat-inc"})
    import time as _t
    real = _t.time
    monkeypatch.setattr(_t, "time", lambda: real() + 8 * 86400)
    rows = [r["session"] for r in c.get("/api/sessions", headers=H).json()["sessions"]]
    assert "pchat-inc" not in rows and "pchat-keep" in rows


def test_the_turn_writes_where_the_header_says(env, monkeypatch):
    """Founder 12:40: the header said Personal while the agent said it would write to another
    workspace. The turn's target and the header's come from one record."""
    root, sessions, c = env
    from control_plane.dispatch import Dispatcher as D
    seen = {}
    orig = D.dispatch

    def spy(self, invocation, **kw):
        seen.update(target=kw.get("target"), no_target=kw.get("no_target"), reads=kw.get("reads"))
        return orig(self, invocation, **kw)
    monkeypatch.setattr(D, "dispatch", spy)
    a = attach.create_workspace(root, "u1", name="Acme").slug
    c.put("/api/chat/default-target", headers=H, json={"workspace": a})
    head = c.get("/api/workspace/states?session=pchat-t", headers=H).json()["chat"]
    c.post("/api/chat", json={"prompt": "hi", "session": "pchat-t"}, headers=H)
    assert seen["target"] == head["target"] == a
    c.post("/api/workspace/profile", headers=H, json={"name": "incognito", "session": "pchat-t"})
    head = c.get("/api/workspace/states?session=pchat-t", headers=H).json()["chat"]
    c.post("/api/chat", json={"prompt": "again", "session": "pchat-t"}, headers=H)
    assert head["target"] is None and seen["no_target"] is True and seen["reads"] == []



def test_a_profile_the_person_makes_can_have_a_workspace_variable(env):
    """A profile whose target is a variable, bound when it is used ("use team for Collab Room")."""
    root, sessions, c = env
    a = attach.create_workspace(root, "u1", name="Collab Room").slug
    c.post("/api/workspace/profiles/edit", headers=H, json={"name": "team", "target": "{{workspace}}", "reads": ["{{workspace}}"]})
    prof = next(p for p in c.get("/api/workspace/profiles", headers=H).json()["profiles"] if p["name"] == "team")
    assert prof["variables"] == ["{{workspace}}"]
    r = c.post("/api/workspace/profile", headers=H, json={"name": "team", "session": "chat-s"})
    assert r.status_code == 422 and "{{workspace}}" in r.json()["detail"]
    r = c.post("/api/workspace/profile", headers=H, json={"name": "team", "session": "chat-s", "workspace": "Collab Room"})
    assert r.status_code == 200, r.text
    v = c.get("/api/workspace/states?session=chat-s", headers=H).json()
    assert v["chat"]["target"] == a and v["chat"]["reads"] == [a]
    assert v["line"] == "Team · writes to Collab Room"


def test_start_a_new_incognito_chat(env):
    _, sessions, c = env
    r = c.post("/api/workspace/profile", headers=H, json={"name": "incognito", "new_chat": True, "session": "chat-old"})
    assert r.status_code == 200 and r.json()["ui_action"] == "open_chat"
    new = r.json()["session"]
    assert new != "chat-old" and sessions.is_incognito("u1", new) and not sessions.is_incognito("u1", "chat-old")


def test_a_new_chat_from_the_tool_opens_in_the_terminal():
    import json
    from llm.tool_events import _OPEN_TOOLS, _open_event
    assert "mcp__vexa__chat_profile_set" in _OPEN_TOOLS
    ev = _open_event(json.dumps({"ui_action": "open_chat", "session": "pchat-abc123"}))
    assert ev == {"type": "open", "target": "chat", "workspace": "", "path": "chat:pchat-abc123"}
    assert _open_event(json.dumps({"ui_action": "open_chat", "session": "../x"})) is None


# ── the company seeds profiles and defaults from _global (Vexa-ai/vexa#1783) ─────────────────────

def _global(root: Path, name: str, text: str) -> None:
    from control_plane import system_mounts
    g = system_mounts.global_root(load_settings(workspaces_dir=str(root)), root)
    g.mkdir(parents=True, exist_ok=True)
    (g / name).write_text(text)


def test_the_company_seeds_profiles_and_a_person_s_edit_wins(env):
    root, _, c = env
    _global(root, "PROFILES.md", "---\nprofiles:\n  sales:\n    target: \"{{workspace}}\"\n"
                                 "    reads: [\"{{workspace}}\", personal]\n---\n# Company chat profiles\n")
    profs = {p["name"]: p for p in c.get("/api/workspace/profiles", headers=H).json()["profiles"]}
    assert profs["sales"]["variables"] == ["{{workspace}}"] and profs["sales"]["company"] is True
    attach.create_workspace(root, "u1", name="Acme")
    c.post("/api/workspace/profiles/edit", headers=H, json={"name": "sales", "target": "Acme"})
    profs = {p["name"]: p for p in c.get("/api/workspace/profiles", headers=H).json()["profiles"]}
    assert profs["sales"]["target"] == "acme"


def test_the_company_default_profile_applies_when_the_person_has_none(env):
    root, sessions, c = env
    _global(root, "POLICIES.md", "---\ndefault_chat_profile: incognito\n---\n")
    assert c.post("/api/chat", json={"prompt": "hi", "session": "pchat-co"}, headers=H).status_code == 200
    assert sessions.profile("u1", "pchat-co") == "incognito"
    c.post("/api/workspace/profiles/edit", headers=H, json={"name": "study", "target": "personal", "default": True})
    c.post("/api/chat", json={"prompt": "hi", "session": "pchat-me"}, headers=H)
    assert sessions.profile("u1", "pchat-me") == "study"


def test_joining_a_workspace_by_link_binds_the_next_new_chat_to_it(env):
    """Opening a workspace invite link queues "shared work" bound to that workspace for the person's
    next new chat — the shared case is a profile with a variable, not a special code path."""
    root, sessions, c = env
    a = attach.create_workspace(root, "u1", name="Collab Room").slug
    attach.queue_profile(root, "u1", "shared work", {"{{workspace}}": a})
    c.post("/api/chat", json={"prompt": "hi", "session": "pchat-j1"}, headers=H)
    # no prebuilt profile is NAMED any more: the chat simply writes to and reads that workspace
    assert sessions.target("u1", "pchat-j1") == a and sessions.reads("u1", "pchat-j1") == [a]
    assert sessions.profile("u1", "pchat-j1") == ""
    c.post("/api/chat", json={"prompt": "hi", "session": "pchat-j2"}, headers=H)
    assert sessions.target("u1", "pchat-j2") != a, "consumed once"


def test_the_shared_chat_seam_binds_shared_work_on_one_chat(env):
    root, sessions, c = env
    from control_plane.routers.workspace_states import bind_shared_work
    from control_plane import workspace_names
    a = attach.create_workspace(root, "u1", name="Collab Room").slug
    rows = workspace_names.listing(root, "u1")
    s = load_settings(workspaces_dir=str(root))
    assert bind_shared_work(sessions, s, root, "u1", "pchat-sh", a, rows) is True
    assert sessions.target("u1", "pchat-sh") == a and sessions.reads("u1", "pchat-sh") == [a]
    assert bind_shared_work(sessions, s, root, "u1", "pchat-sh2", "not-mine", rows) is False


# ── the header chip's menu (UI decision 6, founder 2026-10-14) ──────────────────────────────────

def test_the_chip_names_the_profile_first_and_says_when_it_was_changed(env):
    root, sessions, c = env
    a = attach.create_workspace(root, "u1", name="Acme").slug
    c.post("/api/workspace/profiles/edit", headers=H, json={"name": "work", "target": "Acme", "reads": ["Acme"]})
    c.post("/api/workspace/profile", headers=H, json={"name": "work", "session": "chat-c"})
    assert c.get("/api/workspace/states?session=chat-c", headers=H).json()["line"] == "Work · writes to Acme"
    c.post("/api/workspace/state", headers=H, json={"workspaces": ["personal"], "state": "active", "session": "chat-c"})
    v = c.get("/api/workspace/states?session=chat-c", headers=H).json()
    assert v["chat"]["changed"] is True and v["line"] == "Work (changed) · writes to Acme"
    assert c.get("/api/workspace/states?session=chat-none", headers=H).json()["line"] == "Writes to Personal"


def test_the_menu_lists_profiles_with_a_one_line_summary_and_the_active_one(env):
    root, sessions, c = env
    attach.create_workspace(root, "u1", name="Acme")
    c.post("/api/workspace/profiles/edit", headers=H, json={"name": "work", "target": "Acme", "reads": ["Acme", "personal"]})
    c.post("/api/workspace/profile", headers=H, json={"name": "work", "session": "chat-m"})
    out = c.get("/api/workspace/profiles?session=chat-m", headers=H).json()
    by = {p["name"]: p for p in out["profiles"]}
    assert out["active"] == "work"
    assert by["work"]["summary"] == "writes to Acme · reads 2 · memory on"
    assert by["incognito"]["summary"] == "no workspaces · nothing remembered"
    assert set(by) == {"work", "incognito"}


def test_a_profile_can_be_deleted_and_a_starter_is_reset(env):
    root, sessions, c = env
    c.post("/api/workspace/profiles/edit", headers=H, json={"name": "clients", "target": "personal", "default": True})
    assert any(p["name"] == "clients" for p in c.get("/api/workspace/profiles", headers=H).json()["profiles"])
    r = c.post("/api/workspace/profiles/delete", headers=H, json={"name": "clients"})
    assert r.status_code == 200, r.text
    out = c.get("/api/workspace/profiles", headers=H).json()
    assert not any(p["name"] == "clients" for p in out["profiles"]) and out["default"] is None
    c.post("/api/workspace/profiles/edit", headers=H, json={"name": "work", "reads": []})
    c.post("/api/workspace/profiles/delete", headers=H, json={"name": "work"})
    assert not any(p["name"] == "work" for p in c.get("/api/workspace/profiles", headers=H).json()["profiles"])



def test_switching_to_incognito_mid_chat_says_from_which_message_nothing_is_kept(env):
    root, sessions, c = env
    assert c.post("/api/chat", json={"prompt": "hi", "session": "pchat-mid"}, headers=H).status_code == 200
    r = c.post("/api/workspace/profile", headers=H, json={"name": "incognito", "session": "pchat-mid"})
    say = r.json()["say"]
    assert "From your next message" in say and "nothing" in say and "deleted after 7 days" in say



def test_a_never_edited_starter_left_as_a_default_starts_nothing(env):
    """app.dev has people whose default was a seeded profile they never edited: it is gone, and a new
    chat falls back to their default target instead of a profile that no longer exists."""
    root, sessions, c = env
    state_store = attach._store(root, "u1")
    st = attach._load_state(state_store)
    st["default_profile"] = "work"
    attach._save_state(state_store, st)
    out = c.get("/api/workspace/profiles", headers=H).json()
    assert out["default"] is None and [p["name"] for p in out["profiles"]] == ["incognito"]
    c.post("/api/chat", json={"prompt": "hi", "session": "pchat-x"}, headers=H)
    assert sessions.profile("u1", "pchat-x") == ""


def test_a_profile_someone_edited_survives_the_starters_going(env):
    root, sessions, c = env
    c.post("/api/workspace/profiles/edit", headers=H, json={"name": "hobby", "target": "personal"})
    assert "hobby" in [p["name"] for p in c.get("/api/workspace/profiles", headers=H).json()["profiles"]]
