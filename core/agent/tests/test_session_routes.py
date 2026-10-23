"""ADR-0048 over HTTP — participants, the rail from the store, the session gate on a chat turn,
messages the agent was not asked to answer, and the leak rule reaching the dispatch.

L2: a real FastAPI app over a sqlite session store and fakes; no redis, no runtime, no model.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests._session_fakes import MemBlobs
from control_plane import session_blobs
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

_RAISE: list = []
SUBJECTS = {"bob@acme.test": "bob", "vic@acme.test": "vic"}


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
        yield {"type": "session-bundle", "session_id": "x", "base": 0, "files": {}}
        yield {"type": "message-delta", "text": "ok"}
        yield {"type": "turn-complete"}


def co_members(root, *subjects):
    """The people these chats add share a workspace (the add rule: a person by id or address is
    one the adder shares a workspace with)."""
    p = root / "people" / "policy"
    p.mkdir(parents=True, exist_ok=True)
    (p / "members.json").write_text(json.dumps(
        [{"subject": s, "role": "owner" if i == 0 else "contributor"}
         for i, s in enumerate(subjects or ("alice", "bob", "vic", "carol"))]))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "ws"
    root.mkdir()
    co_members(root)
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="",
                             global_system_workspace_path=str(root / "_global"))
    dispatcher = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
    calls = []

    def _dispatch(inv, **kw):
        if _RAISE:
            raise _RAISE.pop()
        calls.append((inv, kw))
        return "unit-1"

    dispatcher.dispatch = _dispatch
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store,
                     email_subject_lookup=lambda a: SUBJECTS.get(str(a).lower()))
    yield TestClient(app), store, calls
    store.close()


def _h(who):
    return {"X-User-Id": who}


def test_owner_adds_a_person_by_email_and_they_see_the_chat(env):
    client, store, _ = env
    r = client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test", "history": "all"},
                    headers=_h("alice"))
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    assert [p["principal"] for p in r.json()["participants"]] == ["alice", "bob"]
    rows = client.get("/api/sessions", headers=_h("bob")).json()["sessions"]
    assert [x["session"] for x in rows] == [sid] and rows[0]["shared"] and rows[0]["role"] == "member"
    assert client.get(f"/api/sessions/{sid}/participants", headers=_h("bob")).status_code == 200


def test_a_member_adding_someone_starts_their_own_chat_and_outsiders_get_404(env):
    client, store, _ = env
    sid = client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test", "history": "all"},
                      headers=_h("alice")).json()["session_id"]
    r = client.post(f"/api/sessions/{sid}/participants", json={"email": "vic@acme.test"}, headers=_h("bob"))
    assert r.status_code == 200 and r.json()["session_id"] != sid and r.json()["owner"] == "bob"
    assert "vic" not in [p["principal"] for p in store.participants(sid)]
    assert client.get(f"/api/sessions/{sid}/participants", headers=_h("mallory")).status_code == 404
    assert client.get(f"/api/sessions/{sid}/history", headers=_h("mallory")).status_code == 404
    # an address with no account is answered like one with an account, and nobody is added
    r = client.post("/api/sessions/x/participants", json={"email": "nobody@acme.test", "history": "all"}, headers=_h("alice"))
    assert r.status_code == 200 and [p["principal"] for p in r.json()["participants"]] == ["alice"]


def test_removing_and_leaving(env):
    client, store, _ = env
    sid = client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test", "history": "all"},
                      headers=_h("alice")).json()["session_id"]
    assert client.delete(f"/api/sessions/{sid}/participants/alice", headers=_h("bob")).status_code == 403
    r = client.delete(f"/api/sessions/{sid}/participants/bob", headers=_h("bob"))
    assert r.json()["removed"] is True
    assert client.get("/api/sessions", headers=_h("bob")).json()["sessions"] == []


def test_a_message_without_a_mention_is_kept_not_answered(env):
    client, store, calls = env
    sid = client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test", "history": "all"},
                      headers=_h("alice")).json()["session_id"]
    r = client.post("/api/chat/submit", json={"session": sid, "prompt": "I'll take the migration"},
                    headers=_h("bob"))
    assert r.status_code == 200 and r.json()["spoken"] is False
    assert calls == []                                              # no agent turn
    msgs = client.get(f"/api/sessions/{sid}/messages", headers=_h("alice")).json()["messages"]
    assert [(m["author"], m["text"]) for m in msgs] == [("bob", "I'll take the migration")]


def test_a_viewer_cannot_post(env):
    client, store, calls = env
    sid = client.post("/api/sessions/pchat-1/participants", json={"email": "vic@acme.test", "history": "all", "role": "viewer"},
                      headers=_h("alice")).json()["session_id"]
    r = client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa hi"}, headers=_h("vic"))
    assert r.status_code == 403 and r.json()["detail"] == "session_read_only"


def test_a_mentioned_turn_in_a_shared_session_is_dispatched_under_the_leak_rule(env):
    client, store, calls = env
    sid = client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test", "history": "all"},
                      headers=_h("alice")).json()["session_id"]
    r = client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa what's on my desk?"},
                    headers=_h("alice"))
    assert r.status_code == 200, r.text
    (inv, kw), = calls
    scope = kw["shared_scope"]
    assert "alice" in scope.withheld and "_system" in scope.withheld
    prompt = inv["start"]["entrypoint"]["inline"]
    assert "This is a shared conversation" in prompt and "private chat" in prompt
    assert str(inv["context"]["mount_gen"]).startswith("0s")


def test_a_private_chat_is_dispatched_unchanged(env):
    client, store, calls = env
    r = client.post("/api/chat/submit", json={"session": "pchat-9", "prompt": "hello"}, headers=_h("alice"))
    assert r.status_code == 200
    (inv, kw), = calls
    assert kw["shared_scope"] is None
    assert "shared conversation" not in inv["start"]["entrypoint"]["inline"]


def test_the_stream_never_relays_the_conversation_to_the_browser(env):
    client, store, calls = env
    with client.stream("POST", "/api/chat", json={"session": "pchat-9", "prompt": "hello"},
                       headers=_h("alice")) as r:
        body = "".join(r.iter_text())
    assert "session-bundle" not in body and "message-delta" in body


def test_a_shared_chat_someone_posted_in_is_one_row(env):
    client, store, calls = env
    sid = client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test", "history": "all"},
                      headers=_h("alice")).json()["session_id"]
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa hi"}, headers=_h("bob"))
    rows = client.get("/api/sessions", headers=_h("bob")).json()["sessions"]
    assert [r["session"] for r in rows] == [sid] and rows[0]["shared"] is True


# ── S6: a store that cannot answer never runs a shared turn wide open ─────────────────────────
def _break(store, monkeypatch, *methods):
    def _down(*a, **k):
        raise ConnectionError("store down")
    for m in methods:
        monkeypatch.setattr(store, m, _down)


def test_a_store_error_on_a_store_id_is_503_never_a_wide_turn(env, monkeypatch):
    client, store, calls = env
    sid = client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test", "history": "all"},
                      headers=_h("alice")).json()["session_id"]
    _break(store, monkeypatch, "get")
    r = client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa hi"}, headers=_h("bob"))
    assert r.status_code == 503, r.text
    assert r.json()["fault"]["kind"] == "session_unavailable"
    assert calls == []


def test_a_store_error_on_a_shared_own_thread_is_503(env, monkeypatch):
    client, store, calls = env
    client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test", "history": "all"}, headers=_h("alice"))
    _break(store, monkeypatch, "participants")
    r = client.post("/api/chat/submit", json={"session": "pchat-1", "prompt": "@vexa hi"},
                    headers=_h("alice"))
    assert r.status_code == 503 and r.json()["fault"]["kind"] == "session_unavailable"
    assert calls == []


def test_a_store_error_on_a_private_thread_falls_back_to_the_old_layout(env, monkeypatch):
    client, store, calls = env
    _break(store, monkeypatch, "by_name")
    r = client.post("/api/chat/submit", json={"session": "pchat-7", "prompt": "hello"}, headers=_h("alice"))
    assert r.status_code == 200, r.text
    (inv, kw), = calls
    assert kw["shared_scope"] is None


# ── S5: a turn the session cannot take now is a typed 409, and a refused save shows in history ─
def test_a_busy_session_is_a_typed_409(env):
    from control_plane import session_sync

    client, store, calls = env
    sid = client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test", "history": "all"},
                      headers=_h("alice")).json()["session_id"]
    _RAISE.append(session_sync.SessionBusy(sid))
    r = client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa hi"}, headers=_h("bob"))
    assert r.status_code == 409, r.text
    assert r.json()["fault"]["kind"] == "session_conflict"


def test_history_carries_a_refused_save(env, monkeypatch):
    import fakeredis

    from control_plane import session_sync

    client, store, calls = env
    sid = client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test", "history": "all"},
                      headers=_h("alice")).json()["session_id"]
    r = fakeredis.FakeRedis(decode_responses=True)
    session_sync.record_fault(r, sid, session_sync.fault("session_conflict", sid, base=0, head=1))
    monkeypatch.setattr(session_sync, "_redis_for", lambda url: r)
    body = client.get(f"/api/sessions/{sid}/history", headers=_h("bob")).json()
    assert body["fault"]["kind"] == "session_conflict"
    # the terminal renders it as it renders every typed fault: who, what kind, a sentence, a remedy
    assert body["fault"]["source"] == "session-store"
    assert "not saved" in body["fault"]["detail"] and body["fault"]["remedy"]
