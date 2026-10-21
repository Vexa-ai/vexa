"""Deny vectors: adding people to a chat (ADR-0048).

With ``history: none`` or an excerpt, a private chat becomes shared by forking. The newcomer gets a
new session that starts empty or with an excerpt the owner picked (visible text only); the private conversation, its bundle and its tool
traffic stay the owner's. Adding by email answers the same for a known and an unknown address, and
only an owner or member adds (test_access_sessions_share_history.py has the history choice). The leak preamble never names what it withholds.

Pure unit tests: a FastAPI app over a sqlite session store and fakes; no network.
"""
from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from control_plane import session_access, session_fork, session_sync
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests._session_fakes import MemBlobs

SUBJECTS = {"bob@acme.test": "bob", "carol@acme.test": "carol", "mallory@else.test": "mallory"}
SECRET_TOOL_INPUT = "label:private-desk-query"
SECRET_TOOL_RESULT = "PRIVATE-MAIL-BODY-7731"
PROMPT = "[composed context: PRIVATE-PREAMBLE]\nsummarise my inbox"


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _FakeReader:
    def read(self, unit_id, resume=None, fresh=False):
        yield {"type": "message-delta", "text": "ok"}
        yield {"type": "turn-complete"}


def _private_bundle() -> dict:
    transcript = "\n".join(json.dumps(x) for x in [
        {"type": "user", "message": {"content": PROMPT}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Reading your mail."},
            {"type": "tool_use", "id": "t1", "name": "gmail_search", "input": {"q": SECRET_TOOL_INPUT}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": SECRET_TOOL_RESULT}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "You have two threads."}]}},
        {"type": "user", "message": {"content": "thanks"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Any time."}]}},
    ])
    records = [{"key": hashlib.sha256(PROMPT.encode()).hexdigest(), "user_text": "summarise my inbox"},
               {"key": hashlib.sha256(b"thanks").hexdigest(), "user_text": "thanks"}]
    return {
        ".claude/sessions/conversation.session": b"sid-1\n",
        ".claude/sessions/conversation.turns.jsonl": "".join(json.dumps(r) + "\n" for r in records).encode(),
        ".claude/projects/-w/sid-1.jsonl": transcript.encode(),
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "ws"
    root.mkdir()
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="",
                             global_system_workspace_path=str(root / "_global"))
    dispatcher = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
    dispatcher.dispatch = lambda inv, **kw: "unit-1"
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store,
                     email_subject_lookup=lambda a: SUBJECTS.get(str(a).lower()))
    # alice, bob and carol share a workspace (the people alice may add); mallory has an account
    # here and shares nothing with her
    (root / "team" / "policy").mkdir(parents=True)
    (root / "team" / "policy" / "members.json").write_text(json.dumps(
        [{"subject": "alice", "role": "owner"}, {"subject": "bob", "role": "contributor"},
         {"subject": "carol", "role": "contributor"}]))
    private = store.create("alice", name="pchat-1")
    store.save_bundle(private["id"], 0, _private_bundle())
    yield TestClient(app), store, private["id"]
    store.close()


def _h(who):
    return {"X-User-Id": who}


def _add(client, who="bob@acme.test", **extra):
    return client.post("/api/sessions/pchat-1/participants", json={"email": who, **extra},
                       headers=_h("alice"))


# ── S-H2: a private chat becomes shared by forking ──────────────────────────────────────────────
def test_adding_a_person_forks_to_a_new_empty_session(env):
    client, store, private = env
    r = _add(client, history="none")
    assert r.status_code == 200, r.text
    shared = r.json()["session_id"]
    assert shared != private and shared.startswith("s-")
    assert [p["principal"] for p in store.participants(shared)] == ["alice", "bob"]
    assert [p["principal"] for p in store.participants(private)] == ["alice"]   # stays private
    assert store.load_bundle(shared) == {"version": 0, "files": {}}
    h = client.get(f"/api/sessions/{shared}/history", headers=_h("bob")).json()
    assert h["turns"] == [] and h["messages"] == []


def test_the_newcomer_cannot_read_the_private_session(env):
    client, store, private = env
    _add(client, history="none")
    assert client.get(f"/api/sessions/{private}/history", headers=_h("bob")).status_code == 404
    assert client.get(f"/api/sessions/{private}/participants", headers=_h("bob")).status_code == 404
    assert client.get(f"/api/sessions/{private}/messages", headers=_h("bob")).status_code == 404
    assert session_sync.bundle_for(store, "bob", private) is None
    # …and naming the owner's thread by its name reaches only bob's own (new, empty) thread
    assert session_sync.bundle_for(store, "bob", "pchat-1")["files"] == {}
    rail = client.get("/api/sessions", headers=_h("bob")).json()["sessions"]
    assert private not in [x["session"] for x in rail]


def test_the_newcomers_turn_is_handed_no_private_bundle(env):
    client, store, private = env
    shared = _add(client, history="none").json()["session_id"]
    b = session_sync.bundle_for(store, "bob", shared)
    assert b["id"] == shared and b["version"] == 0 and b["files"] == {}


def test_an_excerpt_carries_only_the_chosen_visible_text(env):
    client, store, private = env
    turns = client.get("/api/sessions/pchat-1/history", headers=_h("alice")).json()["turns"]
    assert [t["role"] for t in turns] == ["user", "agent", "user", "agent"]
    r = _add(client, excerpt=[0, 1])
    assert r.status_code == 200, r.text
    shared = r.json()["session_id"]
    msgs = client.get(f"/api/sessions/{shared}/messages", headers=_h("bob")).json()["messages"]
    texts = [m["text"] for m in msgs]
    assert texts == ["(excerpt) me: summarise my inbox",
                     "(excerpt) agent: Reading your mail.You have two threads."]
    blob = json.dumps(client.get(f"/api/sessions/{shared}/history", headers=_h("bob")).json())
    for private_bit in (SECRET_TOOL_INPUT, SECRET_TOOL_RESULT, "gmail_search", "PRIVATE-PREAMBLE",
                        "Any time.", "thanks"):
        assert private_bit not in blob
    assert store.load_bundle(shared)["files"] == {}


def test_an_excerpt_naming_a_missing_turn_is_refused_and_nothing_is_shared(env):
    client, store, private = env
    r = _add(client, excerpt=[99])
    assert r.status_code == 422
    assert [s["id"] for s in store.list_for("bob")] == []


def test_an_excerpt_is_only_for_a_chat_becoming_shared(env):
    client, store, private = env
    shared = _add(client, history="none").json()["session_id"]
    r = client.post(f"/api/sessions/{shared}/participants",
                    json={"email": "carol@acme.test", "excerpt": [0]}, headers=_h("alice"))
    assert r.status_code == 422


def test_a_session_already_shared_is_joined_not_forked(env):
    client, store, private = env
    shared = _add(client, history="none").json()["session_id"]
    r = client.post(f"/api/sessions/{shared}/participants", json={"email": "carol@acme.test"},
                    headers=_h("alice"))
    assert r.status_code == 200 and r.json()["session_id"] == shared
    assert [p["principal"] for p in store.participants(shared)] == ["alice", "bob", "carol"]


def test_a_chat_back_to_one_person_forks_again(env):
    client, store, private = env
    shared = _add(client, history="none").json()["session_id"]
    client.delete(f"/api/sessions/{shared}/participants/bob", headers=_h("alice"))
    r = client.post(f"/api/sessions/{shared}/participants", json={"email": "carol@acme.test", "history": "none"},
                    headers=_h("alice"))
    again = r.json()["session_id"]
    assert again not in (shared, private)
    assert client.get(f"/api/sessions/{shared}/history", headers=_h("carol")).status_code == 404


def test_adding_an_agent_does_not_fork(env):
    client, store, private = env
    r = client.post("/api/sessions/pchat-1/participants", json={"agent": "helper"}, headers=_h("alice"))
    assert r.status_code == 200 and r.json()["session_id"] == private


# ── S-L3: the owner first, and an unknown address answered like a known one ────────────────────
def test_a_viewer_is_refused_before_any_address_is_looked_up(env):
    client, store, private = env
    shared = _add(client, history="none", role="viewer").json()["session_id"]
    looked = []
    for who in ("carol@acme.test", "nobody@acme.test"):
        r = client.post(f"/api/sessions/{shared}/participants", json={"email": who}, headers=_h("bob"))
        looked.append((r.status_code, r.json()))
    assert looked[0] == looked[1] and looked[0][0] == 403


def test_known_and_unknown_addresses_get_the_same_answer(env):
    client, store, private = env
    known = client.post("/api/sessions/other-1/participants", json={"email": "bob@acme.test", "history": "none"},
                        headers=_h("alice"))
    unknown = client.post("/api/sessions/other-2/participants", json={"email": "nobody@acme.test", "history": "none"},
                          headers=_h("alice"))
    assert known.status_code == unknown.status_code == 200
    assert set(known.json()) == set(unknown.json())


# ── S-L2: the preamble never names what it withholds ───────────────────────────────────────────
def test_the_leak_preamble_does_not_name_withheld_workspaces():
    scope = session_access.SharedScope(allowed=["_global"], withheld=["alice", "_system", "secret-deal"])
    text = session_access.leak_preamble(scope, names={"secret-deal": "Secret Deal Room"}, people=2)
    assert "Secret Deal Room" not in text and "secret-deal" not in text and "alice" not in text
    assert "some workspaces are withheld" in text.lower()


def test_visible_text_never_carries_operations_or_composed_prompts():
    assert session_fork.visible_text({"role": "user", "text": PROMPT}) is None
    assert session_fork.visible_text({"role": "agent", "text": "", "ops": [{"name": "x"}]}) is None
    assert session_fork.visible_text({"role": "agent", "text": "hi", "ops": [{"name": "x"}]}) == ("agent", "hi")


def test_an_address_outside_the_owners_people_is_answered_like_no_account(env):
    """Add-by-email holds the co-member rule an id is held to: an account the owner shares no
    workspace with is added nowhere, and the answer is the very one an unknown address gets."""
    client, store, private = env
    outsider = client.post("/api/sessions/pchat-1/participants", json={"email": "mallory@else.test"},
                           headers=_h("alice"))
    unknown = client.post("/api/sessions/pchat-1/participants", json={"email": "nobody@else.test"},
                          headers=_h("alice"))
    assert outsider.status_code == unknown.status_code == 200
    assert outsider.json() == unknown.json()
    assert store.role_of(private, "mallory") is None
    assert client.get("/api/sessions/pchat-1/history", headers=_h("mallory")).status_code == 404
