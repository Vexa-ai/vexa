"""ROOMS (founder 2026-10-15, decided): chats are always private; rooms are made in a workspace,
OPEN by default (every member of the workspace sees and joins it), closed optional. All behind the
``rooms`` feature, off by default — off, shared chats are exactly what they were.

* with rooms on, nobody is added to a chat; a room's member invites a workspace member into it;
* in a room a person's message starts no turn: it goes to the room's log and stream
  (`room:{session}:msgs`, agent-api the one writer) for the sensor; rule 1 — `@vexa`, the agent's
  name, a reply to the agent's message — starts a normal trusted turn at once;
* proactivity (off / quiet / active) and the brief are changed by members and said in the room;
* a room's sensor wakes the agent IN the room (`room.signal`, `target.chat`) on the chat's
  `-signal` unit, with proposals as its only write;
* the agent in room A never reads room B unless everybody reading A's answer is in B.
"""
from __future__ import annotations

import fakeredis
import pytest

from control_plane import events, rooms, signal_scope, workspace_membership
from shared import units

from tests.test_session_routes import _h, env  # noqa: F401 — the fixture

SECRET = "s" * 32


def _share(client, owner="alice", who="bob@acme.test"):
    return client.post("/api/sessions/pchat-1/participants", json={"email": who, "history": "all"},
                       headers=_h(owner)).json()["session_id"]


def _room(client, owner="alice", visibility="open", members=("bob",), name="Exchange integration"):
    r = client.post("/api/rooms", json={"workspace": "team", "name": name, "visibility": visibility,
                                        "members": list(members)}, headers=_h(owner))
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


TEAM = ("alice", "bob", "carol")


@pytest.fixture(autouse=True)
def team(monkeypatch):
    monkeypatch.setattr(workspace_membership, "is_member",
                        lambda root, ws, who: "contributor" if ws in ("team", "people") and who in TEAM else None)


@pytest.fixture(autouse=True)
def rooms_on(request, monkeypatch):
    """Room mode is a feature, off by default; these tests are about it switched on — except the
    ones marked ``rooms_off``, which prove nothing changes without it."""
    monkeypatch.setenv("VEXA_FEATURES", "all" if "rooms_off" in request.keywords else "all,rooms,transcript_signals")


@pytest.fixture
def redis(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(rooms, "redis_for", lambda url: r)
    return r


# ── the mode flip ────────────────────────────────────────────────────────────────────────────────

def test_with_rooms_on_a_chat_stays_private(env):  # noqa: F811
    client, store, _ = env
    r = client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test", "history": "all"},
                    headers=_h("alice"))
    assert r.status_code == 409 and r.json()["detail"]["refused"] == "chats_are_private"
    row = store.by_name("alice", "pchat-1")
    assert row is None or [p["principal"] for p in store.participants(row["id"])] == ["alice"]


def test_a_room_is_made_in_a_workspace_open_by_default(env):  # noqa: F811
    client, store, _ = env
    sid = _room(client)
    st = client.get(f"/api/sessions/{sid}/room", headers=_h("bob")).json()
    assert st["room"] is True and st["visibility"] == "open" and st["proactivity"] == "quiet"
    assert st["workspace"] == "team" and [p["principal"] for p in store.participants(sid)] == ["alice", "bob"]
    r = client.post("/api/rooms", json={"workspace": "elsewhere", "name": "x"}, headers=_h("alice"))
    assert r.status_code == 403
    r = client.post("/api/rooms", json={"workspace": "team", "name": "x", "members": ["mallory"]},
                    headers=_h("alice"))
    assert r.status_code == 403


def test_a_closed_rooms_member_invites_a_workspace_member(env, monkeypatch):  # noqa: F811
    client, store, _ = env
    from tests.test_session_routes import SUBJECTS
    monkeypatch.setitem(SUBJECTS, "carol@acme.test", "carol")
    sid = _room(client, visibility="closed")
    r = client.post(f"/api/sessions/{sid}/participants", json={"email": "carol@acme.test"}, headers=_h("bob"))
    assert r.status_code == 200, r.text
    assert "carol" in [p["principal"] for p in store.participants(sid)]
    r = client.post(f"/api/sessions/{sid}/participants", json={"subject": "mallory"}, headers=_h("bob"))
    assert r.status_code == 403


def test_a_new_chat_is_private_and_has_no_room(env):  # noqa: F811
    client, store, _ = env
    row = store.create("alice", name="pchat-solo")
    st = client.get(f"/api/sessions/{row['id']}/room", headers=_h("alice")).json()
    assert st["room"] is False and st["visibility"] == "private" and store.room(row["id"]) is None


def test_a_plain_room_message_starts_no_turn_and_reaches_the_rooms_stream(env, redis):  # noqa: F811
    client, store, calls = env
    sid = _room(client)
    r = client.post("/api/chat/submit", json={"session": sid, "prompt": "I'll take the migration"},
                    headers=_h("bob"))
    assert r.status_code == 200 and r.json()["spoken"] is False
    assert calls == []
    import json as _json
    entries = [_json.loads(e[1]["payload"]) for e in redis.xrange(rooms.stream_key(sid))]
    assert [(e["author"]["kind"], e["author"]["id"], e["text"]) for e in entries] == [
        ("person", "bob", "I'll take the migration")]
    assert entries[0]["session"] == sid and entries[0]["subject"] == "alice"     # the room's owner
    assert entries[0]["message_id"].startswith("n-")


@pytest.mark.parametrize("body", [
    {"prompt": "@vexa can you check the proxy docs?"},
    {"prompt": "Vexa, what did we decide?"},
    {"prompt": "yes, that one", "reply_to": "turn-agent-7"},
])
def test_rule_one_starts_a_trusted_turn_at_once(env, redis, body):  # noqa: F811
    client, store, calls = env
    sid = _room(client)
    r = client.post("/api/chat/submit", json={"session": sid, **body}, headers=_h("bob"))
    assert r.status_code == 200, r.text
    (inv, _kw), = calls
    assert inv["trigger"] == "message"                    # a person asked: a normal, trusted turn
    assert redis.xlen(rooms.stream_key(sid)) == 0


# ── visibility, proactivity, membership ──────────────────────────────────────────────────────────

def test_any_member_changes_proactivity_and_it_is_said(env):  # noqa: F811
    client, store, _ = env
    sid = _room(client)
    r = client.patch(f"/api/sessions/{sid}/room", json={"proactivity": "active"}, headers=_h("bob"))
    assert r.status_code == 200 and r.json()["proactivity"] == "active"
    assert store.notes(sid)[-1]["text"] == "Vexa here is now active ▾"
    assert client.patch(f"/api/sessions/{sid}/room", json={"proactivity": "loud"},
                        headers=_h("bob")).status_code == 422
    assert client.patch(f"/api/sessions/{sid}/room", json={"proactivity": "off"},
                        headers=_h("mallory")).status_code == 404


def test_the_room_brief_keeps_addressed_to_vexa_and_versions(env):  # noqa: F811
    client, store, _ = env
    sid = _room(client)
    r = client.patch(f"/api/sessions/{sid}/room", headers=_h("alice"),
                     json={"listen": {"focus": "the TLS rollout", "labels": [{"name": "question"}]}})
    assert r.status_code == 200 and r.json()["listen_version"] == 1
    names = [lab["name"] for lab in store.room(sid)["listen"]["labels"]]
    assert "addressed_to_vexa" in names and "question" in names


def test_an_unwatched_agent_cannot_reconfigure_create_or_join_a_room(env):  # noqa: F811
    client, store, _ = env
    sid = _room(client)
    worker = {"X-User-Id": "alice", "x-user-regime": "autonomous", "x-user-delegation-workspaces": ""}
    assert client.patch(f"/api/sessions/{sid}/room", json={"proactivity": "active"},
                        headers=worker).status_code == 403
    assert client.post(f"/api/rooms/{sid}/join", headers=worker).status_code == 403


def test_open_rooms_are_listed_and_joined_closed_rooms_are_invisible(env):  # noqa: F811
    client, store, _ = env
    open_id = _room(client, members=())
    closed_id = _room(client, visibility="closed", name="Pricing internal")
    listed = {x["id"] for x in client.get("/api/rooms?workspace=team", headers=_h("carol")).json()["rooms"]}
    assert open_id in listed and closed_id not in listed
    assert client.post(f"/api/rooms/{closed_id}/join", headers=_h("carol")).status_code == 404
    j = client.post(f"/api/rooms/{open_id}/join", headers=_h("carol"))
    assert j.status_code == 200 and j.json()["joined"] is True
    assert client.post(f"/api/rooms/{open_id}/join", headers=_h("dave")).status_code == 404
    assert client.get(f"/api/sessions/{closed_id}/room", headers=_h("carol")).status_code == 404


def test_reopening_a_closed_room_asks_first(env):  # noqa: F811
    client, store, _ = env
    sid = _room(client, visibility="closed")
    r = client.patch(f"/api/sessions/{sid}/room", json={"visibility": "open"}, headers=_h("bob"))
    assert r.status_code == 409 and r.json()["detail"]["refused"] == "confirm_history"
    r = client.patch(f"/api/sessions/{sid}/room", json={"visibility": "open", "confirm_history": True},
                     headers=_h("bob"))
    assert r.status_code == 200 and r.json()["visibility"] == "open"


def _room_event(sid, subject="alice"):
    return {"name": "room.signal", "subject": subject, "target": {"chat": sid},
            "plan": {"prompt": "Messages since you last spoke (score 1.10)."},
            "data": {"session": sid, "source": {"kind": "chat", "id": sid}, "score": 1.1, "reason": "threshold",
                     "buffer": [{"segment_id": f"{sid}:3", "speaker": "bob",
                                 "text": "does the proxy do TLS 1.3?", "scores": {"question": 0.9}}]}}


def test_a_room_signal_runs_in_the_room_on_its_signal_unit(env):  # noqa: F811
    inv = events.event_to_invocation(_room_event("s-abc"))
    assert inv["trigger"] == "signal" and units.dispatch_id(inv) == units.signal_unit_id("alice", "s-abc")
    text = inv["start"]["entrypoint"]["inline"]
    assert text.startswith(events.ROOM_SIGNAL_MARK) and 'room="s-abc"' in text
    with pytest.raises(ValueError):          # a room signal wakes only the room it came from
        events.event_to_invocation({**_room_event("s-abc"), "target": {"chat": "s-other"}})


@pytest.fixture
def door(tmp_path, monkeypatch):
    """The session-routes app, with an internal tier and signals switched on."""
    from fastapi.testclient import TestClient
    from tests._session_fakes import MemBlobs
    from tests.test_session_routes import SUBJECTS, _FakeIdentity, _FakeReader, _FakeRuntime
    from control_plane.api import _Sessions, create_app
    from control_plane.dispatch import Dispatcher
    from control_plane.session_store import SessionStore
    from control_plane.workspace_reader import WorkspaceReader
    from shared.config import load_settings
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "ws"
    (root / "_global").mkdir(parents=True)
    (root / "_global" / "POLICIES.md").write_text("---\ntranscript_signals: on\n---\n")
    # the room's workspace, with its owner: a room's wake is its workspace's agent (C1)
    (root / "team").mkdir()
    (root / "team" / "policy").mkdir()
    (root / "team" / "policy" / "members.json").write_text(
        '[{"subject": "alice", "role": "owner"}, {"subject": "bob", "role": "contributor"}]')
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="", internal_api_secret=SECRET,
                             global_system_workspace_path=str(root / "_global"))
    dispatcher = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
    calls = []
    dispatcher.dispatch = lambda inv, **kw: calls.append((inv, kw)) or "unit-1"
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store,
                     email_subject_lookup=lambda a: SUBJECTS.get(str(a).lower()))
    yield TestClient(app), store, calls
    store.close()


def test_the_event_door_wakes_a_room_with_no_page_to_write(door):
    client, store, calls = door
    sid = _room(client)
    secret = {"X-Internal-Secret": SECRET}
    r = client.post("/events", json=_room_event(sid), headers=secret)
    assert r.status_code == 202, r.text
    (inv, kw), = calls
    assert inv["trigger"] == "signal" and kw["signal_page"] is None    # proposals only
    assert units.dispatch_id({**inv, "context": {**inv["context"], "session": sid}}).endswith("-signal")
    store.put_room(sid, proactivity="off")
    r = client.post("/events", json=_room_event(sid), headers=secret)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "room_proactivity_off"
    assert client.post("/events", json=_room_event(sid, subject="mallory"), headers=secret).status_code == 404
    lone = store.create("alice", name="pchat-lone")["id"]                # a private chat
    r = client.post("/events", json=_room_event(lone), headers=secret)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "not_a_room"


def test_a_room_wake_may_only_propose(env):  # noqa: F811
    client, store, _ = env
    unit = units.signal_unit_id("alice", "s-room")
    signal_scope.record(None, unit, None)
    worker = {"X-User-Id": "alice", "x-user-regime": "autonomous", "x-user-delegation-workspaces": "",
              "x-user-delegation-unit": unit}
    r = client.put("/api/workspace/file", headers=worker, json={"path": "notes/x.md", "content": "x\n"})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "signal_scope"
    r = client.post("/api/meeting/listen", headers=worker, json={"meeting": "1", "brief": {}})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "signal_scope"


# ── the agent in room A never reads room B unless everyone reading A is in B ─────────────────────

def test_a_room_turn_does_not_read_a_closed_room_its_readers_are_not_in(env):  # noqa: F811
    client, store, _ = env
    a = _room(client)                                            # room A: alice + bob
    b = store.create("alice", name="pchat-b")["id"]              # room B: alice + vic, closed
    store.add_participant(b, "alice", "vic")
    store.put_room(b, visibility="closed")
    shared_worker = {"X-User-Id": "alice", "x-user-regime": "human", "x-user-delegation-workspaces": "*",
                     "x-user-delegation-shared": "1", "x-user-delegation-unit": units.chat_unit_id("alice", a)}
    r = client.get(f"/api/chats/{b}/turns", headers=shared_worker)
    assert r.status_code in (403, 404), r.text
    # …and alice's own request (nobody else reads the answer) reads the room she is in
    assert client.get(f"/api/chats/{b}/turns", headers=_h("alice")).status_code in (200, 404)


def test_signals_reads_a_rooms_brief_over_the_internal_tier_only(door):
    client, store, _calls = door
    sid = _room(client)
    url = f"/internal/chats/{sid}/listen?subject=alice"
    assert client.get(url, headers=_h("alice")).status_code == 403
    got = client.get(url, headers={"X-Internal-Secret": SECRET}).json()
    assert got["chat_session"] == sid and got["room"] is True and got["proactivity"] == "quiet"
    assert any(lab["name"] == "addressed_to_vexa" for lab in got["brief"]["labels"])
    assert got["policy"]["transcript_signals"] == "on" and got["stream"] == f"room:{sid}:msgs"
    assert client.get(f"/internal/chats/{sid}/listen?subject=mallory",
                      headers={"X-Internal-Secret": SECRET}).status_code == 404


# ── switched off (the default): today's shared chats are unchanged ──────────────────────────────

@pytest.mark.rooms_off
def test_with_rooms_off_a_shared_chat_is_exactly_what_it_was(env, redis):  # noqa: F811
    client, store, calls = env
    sid = _share(client)
    assert [m for m in store.notes(sid) if m["author"] == "vexa"] == []        # no room line
    assert store.room(sid) is None
    # an unaddressed message is what it was before rooms (`session_agent_speaks`), and nothing
    # reaches a room stream
    r = client.post("/api/chat/submit", json={"session": sid, "prompt": "I'll take the migration"},
                    headers=_h("bob"))
    assert r.status_code == 200 and r.json()["spoken"] is False and calls == []
    assert redis.xlen(rooms.stream_key(sid)) == 0
    # a reply carries no new meaning
    r = client.post("/api/chat/submit", json={"session": sid, "prompt": "ok", "reply_to": "t-1"},
                    headers=_h("bob"))
    assert r.json()["spoken"] is False and calls == []
    # @vexa still speaks, as it always did
    r = client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa hi"}, headers=_h("bob"))
    assert r.status_code == 200 and len(calls) == 1
    # the room doors are closed
    for method, path in (("GET", f"/api/sessions/{sid}/room"), ("PATCH", f"/api/sessions/{sid}/room"),
                         ("POST", f"/api/rooms/{sid}/join"), ("GET", "/api/rooms?workspace=team")):
        resp = client.request(method, path, headers=_h("alice"), json={} if method == "PATCH" else None)
        assert resp.status_code in (403, 404), (method, path, resp.status_code)
    client.delete(f"/api/sessions/{sid}/participants/bob", headers=_h("bob"))
    assert [m for m in store.notes(sid) if m["author"] == "vexa"] == []


@pytest.mark.rooms_off
def test_with_rooms_off_a_room_signal_wakes_nothing(door):
    client, store, calls = door
    sid = _share(client)
    r = client.post("/events", json=_room_event(sid), headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "rooms_off" and calls == []


def test_rooms_is_not_switched_on_by_all():
    """app.dev runs `VEXA_FEATURES=all`: rooms must stay off there until switched on by name."""
    from control_plane import features as F
    assert F.install_defaults({F.ENV: "all"})["rooms"] is False
    assert F.install_defaults({F.ENV: "all,rooms"})["rooms"] is True
    assert F.install_defaults({F.ENV: "all,rooms,-rooms"})["rooms"] is False
    assert F.install_defaults({F.ENV: "all"})["shared_chats"] is True
