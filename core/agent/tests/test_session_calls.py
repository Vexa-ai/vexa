"""ADR-0048 decisions 6 and 7 over HTTP — a call held in a session, a participant's room token, the
private chat's Share to the room, and the owner's review after the call.

L2: a real FastAPI app over a sqlite session store, a fake LiveKit room reader and a fake share
redeemer; no redis, no runtime, no LiveKit server.
"""
from __future__ import annotations

import base64
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from tests._session_fakes import MemBlobs
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.livekit_rooms import LiveKit
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

SECRET = "s" * 32


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


class _Rooms(LiveKit):
    """LiveKit with the room service replaced by a dict the test fills."""

    rooms: dict = {}

    def room(self, name):
        return self.rooms.get(name)


def _claims(token: str) -> dict:
    body = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def H(who: str) -> dict:
    return {"x-user-id": who, "x-user-email": f"{who}@acme.test"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    monkeypatch.setenv("VEXA_INTERNAL_API_SECRET", "internal-test-secret-0123456789abcdef")
    root = tmp_path / "ws"
    root.mkdir()
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="",
                             global_system_workspace_path=str(root / "_global"))
    dispatcher = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
    lk = _Rooms(url="wss://calls.example.test", api_key="APIkey1", api_secret=SECRET)
    _Rooms.rooms = {}
    redeemed = []
    dispatcher.dispatch = lambda inv, **kw: "unit-1"
    app = create_app(dispatcher, reader=WorkspaceReader(str(root)), sessions=_Sessions(),
                     stream_reader=_FakeReader(), session_store=store, livekit=lk,
                     call_share_redeemer=lambda subject, email, token: redeemed.append((subject, token)) or True,
                     email_subject_lookup=lambda a: None)
    client = TestClient(app)
    sid = store.create("alice", name="team")["id"]
    store.add_participant(sid, "alice", "bob", role="member")
    store.add_participant(sid, "alice", "vic", role="viewer")
    return client, store, sid, redeemed, root


def _start(client, sid, *, owner="bob", room="call-abc123", meeting_id=41):
    _Rooms.rooms[room] = {"name": room, "num_participants": 0,
                          "metadata": {"owner": owner, "session_id": sid, "transcribed": True,
                                       "meeting_id": meeting_id}}
    return client.post(f"/api/sessions/{sid}/call", headers=H(owner),
                       json={"room": room, "meeting_id": meeting_id, "share_token": f"{meeting_id}.secret"})


def test_a_member_binds_their_call_and_every_participant_sees_it(env):
    client, _store, sid, _r, _root = env
    resp = _start(client, sid)
    assert resp.status_code == 200, resp.text
    for who in ("alice", "bob", "vic"):
        got = client.get(f"/api/sessions/{sid}/call", headers=H(who)).json()
        assert got["call"]["room"] == "call-abc123" and got["call"]["transcribed"] is True
        assert "share_token" not in json.dumps(got)
        # each reader learns who THEY are, so the terminal can tell its own call (owner == you)
        assert got["you"] == who and got["call"]["owner"] == "bob"
    assert client.get(f"/api/sessions/{sid}/call", headers=H("eve")).status_code == 404


def test_a_new_chat_of_ones_own_can_hold_a_call_before_anyone_typed(env):
    """Think out loud (founder, 2026-10-14): a call starts in a new chat with no turn yet. Reading
    its call makes the caller's own thread, as a first turn would; another person's id stays 404."""
    client, store, _sid, _r, _root = env
    got = client.get("/api/sessions/pchat-thinking-1/call", headers=H("alice"))
    assert got.status_code == 200 and got.json()["call"] is None
    assert store.by_name("alice", "pchat-thinking-1") is not None
    assert store.by_name("bob", "pchat-thinking-1") is None


def test_a_call_binds_only_with_matching_room_metadata(env):
    client, _store, sid, _r, _root = env
    _Rooms.rooms["call-zzzz99"] = {"name": "call-zzzz99", "metadata": {"owner": "eve", "session_id": sid}}
    stolen = client.post(f"/api/sessions/{sid}/call", headers=H("bob"),
                         json={"room": "call-zzzz99", "meeting_id": 5, "share_token": "5.secretvalue"})
    assert stolen.status_code == 404                     # someone else's room
    _Rooms.rooms["call-other1"] = {"name": "call-other1", "metadata": {"owner": "bob", "session_id": "s-other"}}
    other = client.post(f"/api/sessions/{sid}/call", headers=H("bob"),
                        json={"room": "call-other1", "meeting_id": 5, "share_token": "5.secretvalue"})
    assert other.status_code == 404                      # bob's room, but for another session
    viewer = _start(client, sid, owner="vic", room="call-vvvv11")
    assert viewer.status_code == 403                     # a viewer cannot start one here


def test_an_untranscribed_call_binds_with_no_meeting_and_shares_no_transcript(env):
    """A private or incognito chat's call starts no transcriber and has no meeting (meeting-api):
    it binds by its room alone, joining redeems nothing, and it reads as not transcribed."""
    client, _store, sid, redeemed, _root = env
    _Rooms.rooms["call-quiet1"] = {"name": "call-quiet1", "num_participants": 0,
                                   "metadata": {"owner": "bob", "session_id": sid, "transcribed": False}}
    bound = client.post(f"/api/sessions/{sid}/call", headers=H("bob"), json={"room": "call-quiet1"})
    assert bound.status_code == 200, bound.text
    call = bound.json()["call"]
    assert call["meeting_id"] is None and call["transcribed"] is False
    joined = client.post(f"/api/sessions/{sid}/call/token", headers=H("alice"))
    assert joined.status_code == 200 and redeemed == []
    # a transcribed room (its metadata names its meeting) cannot be bound as if it had none
    _Rooms.rooms["call-loud11"] = {"name": "call-loud11", "metadata": {"owner": "bob", "session_id": sid,
                                                                       "transcribed": True, "meeting_id": 9}}
    assert client.post(f"/api/sessions/{sid}/call", headers=H("bob"), json={"room": "call-loud11"}).status_code == 404


def test_a_participant_gets_a_short_lived_token_for_this_room_only(env):
    client, _store, sid, redeemed, _root = env
    _start(client, sid)
    alice = client.post(f"/api/sessions/{sid}/call/token", headers=H("alice"))
    assert alice.status_code == 200, alice.text
    claims = _claims(alice.json()["token"])
    assert claims["sub"] == "alice" and claims["video"]["room"] == "call-abc123"
    assert claims["video"]["canPublish"] is True and claims["exp"] - claims["nbf"] <= 600
    # M8 (0.13.4 S4): joining no longer inherits the transcript — the owner shares it per person
    assert redeemed == [] and alice.json()["transcript_shared"] is False
    assert client.post(f"/api/sessions/{sid}/call/share", headers=H("alice"),
                       json={"subject": "vic"}).status_code == 403          # only the call's owner
    assert client.post(f"/api/sessions/{sid}/call/share", headers=H("bob"),
                       json={"subject": "alice"}).status_code == 200
    again = client.post(f"/api/sessions/{sid}/call/token", headers=H("alice"))
    assert again.json()["transcript_shared"] is True and redeemed == [("alice", "41.secret")]
    vic = _claims(client.post(f"/api/sessions/{sid}/call/token", headers=H("vic")).json()["token"])
    assert vic["video"]["canPublish"] is False            # a viewer listens
    assert len(redeemed) == 1                             # …and is given nothing they were not handed
    bob = client.post(f"/api/sessions/{sid}/call/token", headers=H("bob"))
    assert bob.status_code == 200 and len(redeemed) == 1  # the owner redeems nothing
    assert client.post(f"/api/sessions/{sid}/call/token", headers=H("eve")).status_code == 404


def test_a_closed_room_ends_the_call(env):
    client, store, sid, _r, _root = env
    _start(client, sid)
    del _Rooms.rooms["call-abc123"]
    assert client.get(f"/api/sessions/{sid}/call", headers=H("alice")).json()["call"] is None
    assert store.active_call(sid) is None
    assert client.post(f"/api/sessions/{sid}/call/token", headers=H("alice")).status_code == 404


def test_stop_then_start_again_in_the_same_chat_starts_a_fresh_transcribed_call(env):
    # LIVE BUG app.dev 2026-10-18 (founder: "if I stop and start the call again here, transcription does
    # not start the second time"). The listener left and the meeting completed, but the room was still
    # standing (its departure timeout, then the person's own rejoin kept it up): GET /call answered it
    # as live, so the terminal skipped the start and took a token for a room with no listener. meeting-api
    # now marks the room `ended` when its meeting is terminal; a room so marked is not the live call.
    client, store, sid, _r, _root = env
    assert _start(client, sid, room="call-first1", meeting_id=161).status_code == 200
    _Rooms.rooms["call-first1"]["metadata"]["ended"] = True          # the meeting completed: left_alone
    got = client.get(f"/api/sessions/{sid}/call", headers=H("bob")).json()
    assert got["call"] is None, "an ended call's room must never be rejoined"
    assert store.active_call(sid) is None
    assert store.call(sid, "call-first1")["ended"] is not None, "the past call stays on record, ended"
    assert client.post(f"/api/sessions/{sid}/call/token", headers=H("bob")).status_code == 404
    # …so the terminal starts again: a fresh meeting and room, bound to the same chat, live
    assert _start(client, sid, room="call-second", meeting_id=162).status_code == 200
    live = client.get(f"/api/sessions/{sid}/call", headers=H("bob")).json()["call"]
    assert live["room"] == "call-second" and live["meeting_id"] == 162 and live["transcribed"] is True
    tok = client.post(f"/api/sessions/{sid}/call/token", headers=H("bob")).json()
    assert _claims(tok["token"])["video"]["room"] == "call-second"
    assert [c["meeting_id"] for c in store.calls_of(sid)] == [161, 162]


def test_share_to_the_room_posts_a_line_without_a_turn(env):
    client, _store, sid, _r, _root = env
    ok = client.post(f"/api/sessions/{sid}/messages", headers=H("bob"), json={"text": "From my notes: ship Friday"})
    assert ok.status_code == 200, ok.text
    lines = client.get(f"/api/sessions/{sid}/messages", headers=H("alice")).json()["messages"]
    assert lines[-1]["author"] == "bob" and "ship Friday" in lines[-1]["text"]
    assert client.post(f"/api/sessions/{sid}/messages", headers=H("vic"), json={"text": "hi"}).status_code == 403


def test_the_meeting_owner_reviews_proposals_and_an_accepted_page_is_committed_as_them(env):
    client, store, sid, _r, root = env
    desk = root / "bob"
    desk.mkdir()
    subprocess.run(["git", "init", "-q", str(desk)], check=True)
    internal = {"x-internal-secret": "internal-test-secret-0123456789abcdef"}
    refused = client.post(f"/api/sessions/{sid}/proposals", headers=H("bob"),
                          json={"owner": "bob", "kind": "note", "body": "x"})
    assert refused.status_code == 403                    # only the post-meeting run records one
    page = client.post(f"/api/sessions/{sid}/proposals", headers=internal, json={
        "owner": "bob", "kind": "page", "workspace": "bob", "path": "notes/call.md",
        "title": "Call notes", "body": "# Call\n", "meeting_id": 41}).json()["proposal"]
    note = client.post(f"/api/sessions/{sid}/proposals", headers=internal, json={
        "owner": "bob", "kind": "commitment", "body": "Bob sends the deck"}).json()["proposal"]
    assert not (desk / "notes" / "call.md").exists()     # nothing lands before the owner accepts
    listed = client.get(f"/api/sessions/{sid}/proposals", headers=H("alice")).json()["proposals"]
    assert {p["id"] for p in listed} == {page["id"], note["id"]}
    assert client.post(f"/api/sessions/{sid}/proposals/{page['id']}/accept", headers=H("alice"), json={}).status_code == 403
    done = client.post(f"/api/sessions/{sid}/proposals/{page['id']}/accept", headers=H("bob"),
                       json={"body": "# Call (edited)\n"})
    assert done.status_code == 200, done.text
    assert (desk / "notes" / "call.md").read_text() == "# Call (edited)\n"
    author = subprocess.run(["git", "-C", str(desk), "log", "-1", "--format=%an <%ae>"],
                            capture_output=True, text=True).stdout.strip()
    assert author == "bob <bob@acme.test>"
    rej = client.post(f"/api/sessions/{sid}/proposals/{note['id']}/reject", headers=H("bob"))
    assert rej.json()["proposal"]["status"] == "rejected"
    again = client.post(f"/api/sessions/{sid}/proposals/{note['id']}/accept", headers=H("bob"), json={})
    assert again.status_code == 409


def test_no_livekit_means_no_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "ws"
    root.mkdir()
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="",
                             global_system_workspace_path=str(root / "_global"))
    app = create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store)
    client = TestClient(app)
    sid = store.create("alice", name="team")["id"]
    assert client.get(f"/api/sessions/{sid}/call", headers=H("alice")).json() == {
        "session_id": sid, "calls": False, "you": "alice", "call": None}
    assert client.post(f"/api/sessions/{sid}/call/token", headers=H("alice")).status_code == 503


def test_the_shared_chat_follows_the_live_call(env, monkeypatch):
    client, store, sid, _r, _root = env
    _start(client, sid)
    from control_plane.routers import chats as chats_mod

    seen = []
    orig = chats_mod._context_grounding

    def _grounding(body, session, redis_url, **kw):
        seen.append(body.context.focus if body.context else None)
        return orig(body, session, redis_url, **kw)

    monkeypatch.setattr(chats_mod, "_context_grounding", _grounding)
    resp = client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa what did we agree?"},
                headers=H("alice"))
    assert seen, resp.text
    assert seen[0] == {"kind": "meeting", "meeting_id": "41", "status": "active"}


def test_auto_accept_lands_a_proposal_in_the_owners_name_only_when_policy_says_on(env):
    client, store, sid, _r, root = env
    desk = root / "bob"
    desk.mkdir()
    subprocess.run(["git", "init", "-q", str(desk)], check=True)
    as_bob = {"x-internal-secret": "internal-test-secret-0123456789abcdef", "x-user-id": "bob"}
    page = {"owner": "bob", "kind": "page", "workspace": "bob", "path": "notes/auto.md", "body": "# Auto\n"}
    off = client.post(f"/api/sessions/{sid}/proposals", headers=as_bob, json=page).json()
    assert off["proposal"]["status"] == "open" and not (desk / "notes" / "auto.md").exists()
    (root / "_global").mkdir(exist_ok=True)
    (root / "_global" / "POLICIES.md").write_text("---\npost_meeting_auto_accept: on\n---\n")
    on = client.post(f"/api/sessions/{sid}/proposals", headers=as_bob,
                     json={**page, "path": "notes/auto2.md"}).json()
    assert on["proposal"]["status"] == "accepted" and on.get("auto_accepted") is True
    assert (desk / "notes" / "auto2.md").read_text() == "# Auto\n"
    author = subprocess.run(["git", "-C", str(desk), "log", "-1", "--format=%an"],
                            capture_output=True, text=True).stdout.strip()
    assert author == "bob"


# ── SHOW TO EVERYONE (founder 2026-10-15): a page opens for everyone in the call ─────────────────

class _SentRooms(_Rooms):
    sent: list = []

    def send_data(self, room, payload, *, topic):
        self.sent.append((room, topic, payload))
        return True


def _members(root, ws, *subjects):
    from control_plane import workspace_membership as m
    (root / ws).mkdir(parents=True, exist_ok=True)
    m.write_policy(root, ws, "members", [{"subject": s, "role": "owner" if i == 0 else "contributor"}
                                         for i, s in enumerate(subjects)])


def test_show_opens_the_page_for_everyone_and_leaves_one_line_in_the_chat(env, monkeypatch):
    client, store, sid, _r, root = env
    _SentRooms.sent = []
    monkeypatch.setattr(_Rooms, "send_data", lambda self, room, payload, *, topic: _SentRooms.sent.append((room, topic, payload)) or True, raising=False)
    _start(client, sid)
    _members(root, "ait-shared", "alice", "bob", "vic")
    (root / "ait-shared" / "sponsor-plan.md").write_text("# Sponsor plan\n\n## MongoDB\nKeen.\n")
    resp = client.post(f"/api/sessions/{sid}/call/show", headers=H("bob"),
                       json={"workspace": "ait-shared", "path": "sponsor-plan.md", "anchor": "MongoDB"})
    assert resp.status_code == 200, resp.text
    room, topic, payload = _SentRooms.sent[-1]
    assert room == "call-abc123" and topic == "vexa.show"
    assert payload["type"] == "show" and payload["by"] == "bob" and payload["workspace"] == "ait-shared"
    assert payload["path"] == "sponsor-plan.md" and payload["anchor"] == "MongoDB" and payload["title"] == "Sponsor plan"
    msgs = client.get(f"/api/sessions/{sid}/messages", headers=H("alice")).json()
    assert any("showed “Sponsor plan”" in m["text"] for m in msgs.get("messages", [])), msgs
    hl = client.post(f"/api/sessions/{sid}/call/highlight", headers=H("bob"), json={"anchor": "Keen."})
    assert hl.status_code == 200 and _SentRooms.sent[-1][2] == {"type": "highlight", "by": "bob", "anchor": "Keen."}


def test_show_refuses_a_page_not_everyone_in_the_call_can_read(env, monkeypatch):
    client, _store, sid, _r, root = env
    _SentRooms.sent = []
    monkeypatch.setattr(_Rooms, "send_data", lambda self, room, payload, *, topic: _SentRooms.sent.append((room, topic, payload)) or True, raising=False)
    _start(client, sid)
    _members(root, "bobs-own", "bob", "alice")                 # vic is not in it
    (root / "bobs-own" / "plan.md").write_text("# Plan\n")
    resp = client.post(f"/api/sessions/{sid}/call/show", headers=H("bob"), json={"workspace": "bobs-own", "path": "plan.md"})
    assert resp.status_code == 409, resp.text
    body = resp.json()["detail"]
    assert body["error"] == "not_shared" and body["workspace"] == "bobs-own" and [m["subject"] for m in body["missing"]] == ["vic"]
    personal = client.post(f"/api/sessions/{sid}/call/show", headers=H("bob"), json={"workspace": "personal", "path": "x.md"})
    assert personal.status_code == 409
    assert _SentRooms.sent == []                                  # nothing reached the room
    nobody = client.post(f"/api/sessions/{sid}/call/highlight", headers=H("eve"), json={"anchor": "x"})
    assert nobody.status_code == 404                              # not in the chat: refused
    stranger = client.post(f"/api/sessions/{sid}/call/show", headers=H("eve"), json={"workspace": "bobs-own", "path": "plan.md"})
    assert stranger.status_code == 404 and _SentRooms.sent == []
    viewer = client.post(f"/api/sessions/{sid}/call/show", headers=H("vic"), json={"workspace": "bobs-own", "path": "plan.md"})
    assert viewer.status_code == 403                              # a viewer listens; it does not present
