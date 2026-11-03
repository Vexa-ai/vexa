"""The chat list reads each thing once per listing, not once per chat (live app.dev 2026-10-18).

`GET /api/sessions` for a person with 262 chats took p50 1.6 s after the auto-title fix. A profile
put the rest in reads repeated per row: three session-store reads per stored session (participants,
room, the room again), a sweep of every workspace folder for each nameless chat with no thread, and
a full membership scan per person the list names. The listing must answer the same rows from one
read per kind; these tests count the reads and check the rows did not change.

L2, in-process: a real FastAPI app over a sqlite session store, the file policy store, an in-memory
session index. No redis, no containers. Synthetic people only.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from control_plane import workspace_membership as m
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

from tests._session_fakes import MemBlobs
from tests.test_s4_rooms import ACCOUNTS, H, SECRET, _git, _Rooms
from tests.test_session_routes import _FakeIdentity, _FakeReader, _FakeRuntime


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    monkeypatch.setenv("VEXA_FEATURES", "all,rooms,transcript_signals")
    root = tmp_path / "ws"
    (root / "_global").mkdir(parents=True)
    idx = m.InMemoryMembershipIndex()
    (root / "team").mkdir()
    _git(root / "team")
    m.ensure_owner(root, "team", "alice", index=idx, email="alice@acme.test")
    for who in ("bob", "dan"):
        m.grant_membership(root, "team", who, "contributor", added_by="alice", index=idx, email=f"{who}@acme.test")
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="", internal_api_secret=SECRET,
                             global_system_workspace_path=str(root / "_global"), ui_url="https://app.test")
    dispatcher = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
    dispatcher.dispatch = lambda inv, **kw: "unit-1"
    sess = _Sessions()
    reader = WorkspaceReader(str(root))
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=reader, sessions=sess,
                     session_store=store, membership_index=idx,
                     livekit=_Rooms(url="wss://calls.example.test", api_key="APIkey1", api_secret=SECRET),
                     call_share_redeemer=lambda *_a: True, notify_publish=lambda ch, payload: None,
                     email_subject_lookup=lambda a: ACCOUNTS.get(str(a).lower()))
    c = TestClient(app, raise_server_exceptions=False)
    yield type("W", (), {"c": c, "store": store, "root": root, "sess": sess, "reader": reader})
    store.close()


def _count(monkeypatch, obj, name: str) -> list:
    calls: list = []
    real = getattr(obj, name)

    def counted(*a, **k):
        calls.append(a)
        return real(*a, **k)
    monkeypatch.setattr(obj, name, counted)
    return calls


def _rows(w, who="alice") -> list:
    r = w.c.get("/api/sessions", headers=H(who))
    assert r.status_code == 200, r.text
    return r.json()["sessions"]


def _talk(w, who: str, name: str, text: str = "hello") -> str:
    """A stored chat of ``who``'s with one note in it, listed on their rail."""
    row = w.store.create(who, name=name, title=f"chat {name}")
    w.store.add_note(row["id"], who, text)
    w.sess.upsert(who, name, title=f"chat {name}")
    return row["id"]


def test_the_store_is_read_once_per_kind_however_many_chats_and_rooms(w, monkeypatch):
    for i in range(12):
        _talk(w, "alice", f"pchat-own{i:02d}")
    pairs = [w.c.post("/api/rooms/direct", json={"subject": who}, headers=H("alice")).json()["session_id"]
             for who in ("bob", "dan")]
    for sid in pairs:
        w.c.post(f"/api/sessions/{sid}/messages", json={"text": "hi"}, headers=H("bob"))
    before = _rows(w)
    participants = _count(monkeypatch, w.store, "participants")
    room = _count(monkeypatch, w.store, "room")
    after = _rows(w)
    assert after == before                                   # the same rows, field for field
    # one participants read per kind of thing: none per session (the old listing read one per
    # stored session, 14 here); a room is read again only where its membership authority is asked
    assert len(participants) <= len(pairs), len(participants)
    assert len(room) <= 2 * len(pairs), len(room)
    direct = {r["session_id"]: r for r in after if r.get("visibility") == "direct"}
    assert set(direct) == set(pairs)
    assert {r["other_subject"] for r in direct.values()} == {"bob", "dan"}
    assert all(r["other_name"] for r in direct.values())


def _pointer(ws, sid: str, prompt: str) -> None:
    thread = f"t-{sid}"
    (ws / ".claude" / "sessions").mkdir(parents=True, exist_ok=True)
    (ws / ".claude" / "sessions" / f"{sid}.session").write_text(thread + "\n")
    proj = ws / ".claude" / "projects" / "-w"
    proj.mkdir(parents=True, exist_ok=True)
    at = "2026-10-18T07:00:00Z"
    lines = [{"type": "user", "timestamp": at, "message": {"content": prompt}},
             {"type": "assistant", "timestamp": at, "message": {"content": [{"type": "text", "text": "ok"}]}}]
    (proj / f"{thread}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def test_nameless_chats_with_no_thread_are_not_swept_for_one_by_one(w, monkeypatch):
    # twenty records a scope write made before anybody said anything: nameless, no thread anywhere
    for i in range(20):
        w.sess.upsert("alice", f"pchat-empty{i:02d}")
    # and two nameless chats with a pointer: one in alice's own folder, one in a SHARED workspace.
    # A pointer in a shared tree names no subject, so it is nobody's thread in particular and is
    # never read as alice's (S02C-3): the chat with no thread of its own is not listed.
    w.sess.upsert("alice", "pchat-mine")
    w.sess.upsert("alice", "pchat-swept")
    _pointer(w.root / "alice", "pchat-mine", "what is on today")
    _pointer(w.root / "team", "pchat-swept", "draft the memo")
    sweeps = _count(monkeypatch, WorkspaceReader, "_swept_roots")
    listed = {r["session"] for r in _rows(w)}
    assert "pchat-mine" in listed and "pchat-swept" not in listed
    assert not any(s.startswith("pchat-empty") for s in listed)
    # the old listing swept every workspace folder once per nameless chat with no pointer in the
    # caller's own folders: 21 sweeps here. Now only a chat whose pointer exists somewhere is read.
    assert len(sweeps) <= 1, len(sweeps)


def test_a_read_scope_scans_a_workspace_with_nothing_to_adopt_once(w, monkeypatch):
    (w.root / "desk-of-someone").mkdir()                     # a folder the policy store never heard of
    legacy = _count(monkeypatch, m, "_legacy_sources")
    m._ensure_migrated(w.root, "desk-of-someone")
    m._ensure_migrated(w.root, "desk-of-someone")
    assert len(legacy) == 2                                  # outside a scope: asked every time
    legacy.clear()
    with m.read_scope():
        m._ensure_migrated(w.root, "desk-of-someone")
        m._ensure_migrated(w.root, "desk-of-someone")
        with m.read_scope():                                 # re-entrant: the outer scope holds
            m._ensure_migrated(w.root, "desk-of-someone")
    assert len(legacy) == 1
    m._ensure_migrated(w.root, "desk-of-someone")
    assert len(legacy) == 2                                  # nothing outlives the scope


def test_the_listing_names_each_person_from_one_membership_scan(w, monkeypatch):
    for who in ("bob", "dan"):
        sid = w.c.post("/api/rooms/direct", json={"subject": who}, headers=H("alice")).json()["session_id"]
        w.c.post(f"/api/sessions/{sid}/messages", json={"text": "hi"}, headers=H(who))
    for i in range(6):
        (w.root / f"desk{i}").mkdir()                       # personal desks: nothing to adopt
    before = _rows(w)
    legacy = _count(monkeypatch, m, "_legacy_sources")
    after = _rows(w)
    assert after == before
    desks = [a for a in legacy if str(a[1]).startswith("desk")]
    # each desk is looked at once per listing, not once per person the listing names
    assert len(desks) <= 6, len(desks)
