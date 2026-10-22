""""Call tester-b" — the agent starts a Vexa call with a person (founder bug 2026-10-18; UI 7hh, 7ii; FD4).

The founder typed "Call tester-b" in his own chat and the agent answered that Vexa cannot place a call,
offering a Google Meet link. ``POST /api/rooms/call`` (tool ``call_person``) composes what exists:
the directory (people you share a workspace with), the room of two (made once), a Vexa call started
on meeting-api as the caller, and the ring. These tests pin the composition and its deny rules.

L2, in-process, the S4 rig: a real FastAPI app over a sqlite session store, the file policy store, a
fake LiveKit room reader, a captured notification bus and a FAKE meeting-api (``call_starter``).
Workspace ``team``: alice owner; bob, dan, tester-a, tester-b contributors. ``handbook``: carol owner,
erin. Synthetic people only.
"""
from __future__ import annotations

import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from control_plane import pair_rooms, policy_decide as pd, private_taint, workspace_membership as m
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.livekit_rooms import LiveKit
from control_plane.routers import person_call
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

from tests._session_fakes import MemBlobs
from tests.test_session_routes import _FakeIdentity, _FakeReader, _FakeRuntime

SECRET = "s" * 32


def H(who: str, **extra) -> dict:
    return {"X-User-Id": who, "x-user-email": f"{who}@acme.test", "x-user-credential-class": "session", **extra}


#: The person's own chat agent, the person in the loop this turn ("Call tester-b").
AGENT = {"x-user-regime": "human", "x-user-delegation-workspaces": "team", "x-user-delegation-unit": "u-1"}


class _Rooms(LiveKit):
    rooms: dict = {}

    def room(self, name):
        return self.rooms.get(name)


class _Meetings:
    """meeting-api's half: makes the LiveKit room (as the fake server sees it) and the share."""

    def __init__(self):
        self.calls: list = []
        self.n = 0

    def __call__(self, identity, session_id, transcribe):
        self.n += 1
        room = f"call-fake{self.n:04d}"
        self.calls.append({"identity": identity, "session_id": session_id, "transcribe": transcribe})
        _Rooms.rooms[room] = {"name": room, "num_participants": 0,
                              "metadata": {"session_id": session_id, "transcribed": transcribe}}
        return {"room": room, "meeting_id": 500 + self.n if transcribe else None,
                "share_token": f"{500 + self.n}.tok" if transcribe else "", "transcribed": transcribe}


def _git(ws):
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "t"], check=True)


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    monkeypatch.setenv("VEXA_FEATURES", "all,rooms")
    monkeypatch.setenv("VEXA_INTERNAL_API_SECRET", "internal-test-secret-0123456789abcdef")
    root = tmp_path / "ws"
    (root / "_global").mkdir(parents=True)
    idx = m.InMemoryMembershipIndex()
    for ws, owner in (("team", "alice"), ("handbook", "carol")):
        (root / ws).mkdir()
        _git(root / ws)
        m.ensure_owner(root, ws, owner, index=idx, email=f"{owner}@acme.test")
    for who in ("bob", "dan", "tester-a", "tester-b"):
        m.grant_membership(root, "team", who, "contributor", added_by="alice", index=idx, email=f"{who}@acme.test")
    m.grant_membership(root, "handbook", "erin", "contributor", added_by="carol", index=idx, email="erin@acme.test")
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="", internal_api_secret=SECRET,
                             global_system_workspace_path=str(root / "_global"), ui_url="https://app.test")
    dispatcher = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
    dispatcher.dispatch = lambda inv, **kw: "unit-1"
    lk = _Rooms(url="wss://calls.example.test", api_key="APIkey1", api_secret=SECRET)
    _Rooms.rooms = {}
    bus: list = []
    meetings = _Meetings()
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store, membership_index=idx, livekit=lk,
                     call_share_redeemer=lambda *_a: True,
                     notify_publish=lambda ch, payload: bus.append((ch, json.loads(payload))),
                     call_starter=meetings)
    c = TestClient(app, raise_server_exceptions=False)
    yield type("W", (), {"c": c, "store": store, "root": root, "idx": idx, "bus": bus, "meetings": meetings})
    store.close()


def _notices(w, who: str, kind: str) -> list:
    return [p["notification"] for ch, p in w.bus
            if ch == f"u:{who}:notifications" and p.get("type") == "notification" and p["notification"]["kind"] == kind]


def _rings(w, who: str) -> list:
    return [p for ch, p in w.bus if ch == f"u:{who}:notifications" and p.get("type") == "ring"]


def _call(w, body: dict, who="alice", **headers):
    return w.c.post("/api/rooms/call", json=body, headers=H(who, **headers))


# ── the founder's sentence ──────────────────────────────────────────────────────────────────────

def test_call_tester_b_from_the_persons_own_chat_starts_a_vexa_call_and_rings_them(w):
    r = _call(w, {"person": "tester-b"}, **AGENT)
    assert r.status_code == 200, r.text
    out = r.json()
    sid = out["session_id"]
    # the room of two (FD4): the pair workspace, both owners, the opener pays (created_by, I20)
    room = w.store.room(sid)
    assert room["workspace"] == pair_rooms.pair_id("alice", "tester-b") and room["created_by"] == "alice"
    assert out["room_created"] is True and out["with"]["subject"] == "tester-b"
    assert out["room_ref"] == f"[[room:{sid}]]" and out["room_ref"] in out["say"]
    # the caller's tab is asked to join the call it started (the first-join window is 30 s)
    assert out["ui_action"] == "join_call"
    # a Vexa call in that room, bound as the Call button binds it, owned by the caller
    assert w.meetings.calls == [{"identity": "", "session_id": sid, "transcribe": True}]
    live = w.store.active_call(sid)
    assert live["owner"] == "alice" and live["room"] == out["call"]["room"] and out["call"]["transcribed"] is True
    # tester-b is rung in the app (a ring frame + a call_started notice), and nobody else is
    assert out["rang"] == ["tester-b"]
    assert _rings(w, "tester-b") and _notices(w, "tester-b", "call_started")
    assert not _rings(w, "bob") and not _rings(w, "alice")


def test_the_same_pair_always_lands_in_the_same_room(w):
    first = _call(w, {"person": "tester-b"}).json()
    _Rooms.rooms.clear()                     # the first call ended
    second = _call(w, {"person": "tester-b"}, **AGENT).json()
    assert second["session_id"] == first["session_id"] and second["room_created"] is False


def test_a_live_call_is_reused_and_they_are_rung_again(w):
    first = _call(w, {"person": "tester-b"}).json()
    again = _call(w, {"person": "tester-b"}).json()
    assert again["already_live"] is True and again["call"]["room"] == first["call"]["room"]
    assert len(w.meetings.calls) == 1 and again["rang"] == ["tester-b"]


def test_transcribe_off_in_the_room_starts_the_call_audio_only(w):
    sid = _call(w, {"person": "bob"}).json()["session_id"]
    _Rooms.rooms.clear()
    w.c.post(f"/api/rooms/{sid}/switches", json={"switch": "transcribe", "on": False}, headers=H("bob"))
    out = _call(w, {"person": "bob"}).json()
    assert w.meetings.calls[-1]["transcribe"] is False and out["call"]["transcribed"] is False


def test_an_existing_room_can_be_called(w):
    sid = w.c.post("/api/rooms", json={"workspace": "team", "name": "venue", "visibility": "closed",
                                       "members": ["bob"]}, headers=H("alice")).json()["session_id"]
    out = _call(w, {"room": sid}, **AGENT).json()
    assert out["session_id"] == sid and out["with"] is None and out["rang"] == ["bob"]


# ── who: the directory, and one short question ──────────────────────────────────────────────────

def test_matching_is_by_id_then_whole_name_then_word_prefix():
    people = [{"subject": "7", "name": "Maya Ortiz", "email": "maya@acme.test"},
              {"subject": "8", "name": "", "email": "tester-a@acme.test"},
              {"subject": "9", "name": "", "email": "tester-b@acme.test"},
              {"subject": "10", "name": "Mark Twain", "email": "mt@acme.test"}]
    assert person_call.match_people(people, "9") == ("one", [people[2]])
    assert person_call.match_people(people, "@tester-b") == ("one", [people[2]])
    assert person_call.match_people(people, "tester-b@acme.test")[1] == [people[2]]
    assert person_call.match_people(people, "maya") == ("one", [people[0]])
    assert person_call.match_people(people, "Maya O")[1] == [people[0]]
    assert person_call.match_people(people, "tester")[0] == "many"
    assert person_call.match_people(people, "ma")[0] == "many"
    assert person_call.match_people(people, "zed") == ("none", [])


def test_ambiguous_asks_one_question_and_names_no_address(w):
    r = _call(w, {"person": "tester"}, **AGENT)
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["refused"] == "ambiguous_person" and {c["subject"] for c in d["candidates"]} == {"tester-a", "tester-b"}
    assert "@" not in json.dumps(d["candidates"]) and "ONE short question" in d["instruction"]
    assert not w.meetings.calls and not w.store.list_for("tester-a")


def test_someone_outside_the_directory_is_not_found_and_nothing_is_made(w):
    # erin has an account but shares no workspace with alice; carol neither with bob
    for who, asked in (("alice", "erin"), ("bob", "carol"), ("alice", "nobody-at-all")):
        r = _call(w, {"person": asked}, who=who, **AGENT)
        assert r.status_code == 404 and r.json()["detail"]["refused"] == "not_in_directory", (who, asked)
        assert "calendar" in r.json()["detail"]["instruction"]          # never a Meet link instead
    assert not w.meetings.calls and not (w.root / pair_rooms.pair_id("alice", "erin")).exists()


def test_calling_yourself_or_naming_both_or_neither_is_refused(w):
    assert _call(w, {"person": "alice"}).status_code in (404, 422)
    assert _call(w, {}).status_code == 422
    assert _call(w, {"person": "bob", "room": "s-0123456789abcdef"}).status_code == 422


# ── deny: the person in the loop, never a shared turn ───────────────────────────────────────────

def test_an_unwatched_agent_never_calls_anyone(w):
    r = _call(w, {"person": "tester-b"}, **{**AGENT, "x-user-regime": "autonomous"})
    assert r.status_code == 403
    assert not w.meetings.calls and not _rings(w, "tester-b")
    assert not (w.root / pair_rooms.pair_id("alice", "tester-b")).exists()


def test_a_turn_other_people_read_never_calls_a_person(w):
    for extra in ({"x-user-delegation-shared": "1"}, {"x-user-principal-kind": "workspace"}):
        r = _call(w, {"person": "tester-b"}, **{**AGENT, **extra})
        assert r.status_code == 403, extra
    assert not w.meetings.calls and not _rings(w, "tester-b")


def test_the_floor_denies_a_workspace_principal_and_allows_a_person():
    assert not pd.decide(pd.Ask(principal="76", action="call.person", principal_kind="ws")).allowed
    assert pd.decide(pd.Ask(principal="2", action="call.person", credential=pd.DELEGATED)).allowed


def test_a_workspace_rule_can_deny_calls_in_its_rooms(w):
    sid = w.c.post("/api/rooms", json={"workspace": "team", "name": "quiet", "visibility": "closed",
                                       "members": ["bob"]}, headers=H("alice")).json()["session_id"]
    (w.root / "team" / "policy").mkdir(exist_ok=True)
    (w.root / "team" / "policy" / "rules.json").write_text(json.dumps(
        [{"id": "team.no-calls", "effect": "deny", "action": "call.person"}]))
    r = _call(w, {"room": sid})
    assert r.status_code == 403 and r.json()["detail"]["rule"].startswith("team.no-calls@")
    assert not w.meetings.calls


def test_a_named_room_outside_the_dispatch_ceiling_is_refused(w):
    sid = w.c.post("/api/rooms", json={"workspace": "team", "name": "x", "visibility": "closed",
                                       "members": ["bob"]}, headers=H("alice")).json()["session_id"]
    r = _call(w, {"room": sid}, **{**AGENT, "x-user-delegation-workspaces": "handbook"})
    assert r.status_code == 403 and not w.meetings.calls


def test_the_rooms_feature_off_refuses(w, monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all")
    assert _call(w, {"person": "tester-b"}, **AGENT).status_code == 403
    assert not w.meetings.calls


# ── deny: the privacy hold (D6) ─────────────────────────────────────────────────────────────────

def test_a_session_marked_with_private_reads_holds_the_ring_until_the_person_confirms(w):
    private_taint.mark("u-1", "crm")                 # the edge saw a CRM read: not fingerprinted
    r = _call(w, {"person": "tester-b"}, **AGENT)
    assert r.status_code == 409 and r.json()["detail"]["refused"] == private_taint.REASON
    assert not w.meetings.calls and not _rings(w, "tester-b")
    # the person's own "confirm <code>" lets exactly this call through once
    code = r.json()["detail"]["confirm_code"]
    assert private_taint.confirm_from_text("alice", f"confirm {code}")
    assert _call(w, {"person": "tester-b"}, **AGENT).status_code == 200
    assert _rings(w, "tester-b")


# ── meeting-api is called as the caller ─────────────────────────────────────────────────────────

def test_meeting_api_is_called_with_the_callers_own_signed_identity_only():
    seen = []

    def http(method, url, headers, body=None, timeout=0):
        seen.append((method, url, dict(headers), body))
        if url.endswith("/bots"):
            return 201, {"id": 77, "native_meeting_id": "call-abc123"}
        return 200, {"token": "77.share"}

    start = person_call.meeting_api_starter("http://meeting-api:8080/", http=http)
    out = start("signed-token", "s-0123456789abcdef", True)
    assert out == {"room": "call-abc123", "meeting_id": 77, "share_token": "77.share", "transcribed": True}
    assert [(m_, u) for m_, u, _h, _b in seen] == [("POST", "http://meeting-api:8080/bots"),
                                                   ("POST", "http://meeting-api:8080/meetings/77/share")]
    for _m, _u, h, _b in seen:
        assert h == {"x-vexa-identity": "signed-token"}               # no internal secret, no x-user-*
    assert seen[0][3] == {"platform": "vexa_call", "session_id": "s-0123456789abcdef"}


def test_meeting_api_refusing_the_call_is_a_typed_answer():
    start = person_call.meeting_api_starter("http://m", http=lambda *a, **k: (403, {"detail": "bot limit"}))
    with pytest.raises(person_call.CallRefused) as exc:
        start("t", "s-0123456789abcdef", True)
    assert exc.value.status == 403 and exc.value.detail["refused"] == "call_not_started"
    quiet = person_call.meeting_api_starter(
        "http://m", http=lambda *a, **k: (201, {"id": None, "native_meeting_id": "call-q1"}))
    assert quiet("t", "s-0123456789abcdef", False)["transcribed"] is False


# ── the agent knows calls exist ─────────────────────────────────────────────────────────────────

def test_the_tool_is_served_allow_listed_and_person_only():
    from pathlib import Path
    agent = Path(__file__).resolve().parents[1]
    tools = {t["name"]: t for t in json.loads((agent / "mcp.tools.v1.json").read_text())["tools"]}
    assert tools["call_person"]["route"] == {"method": "POST", "path": "/api/rooms/call"}
    assert "Meet" in tools["call_person"]["note"] and "ONE short question" in tools["call_person"]["note"]
    assert "call_person" in json.loads((agent / "worker" / "mcp_tools.v1.json").read_text())["tools"]
    routes = json.loads((agent / "routes.v1.json").read_text())
    assert {"method": "POST", "path": "/agent/rooms/call", "person": True, "shared": "refuse"} in routes["verbs"]
    row = next(r for r in routes["routes"] if r["path"] == "/agent/rooms/call")
    assert row["mcp_reentry"] is True and row["shared"] == "refuse"


def test_call_tester_b_is_an_imperative_and_every_turn_knows_vexa_calls_exist():
    from worker import engine
    text = engine.imperative_preamble("Call tester-b")
    assert "call `call_person` first" in text
    assert "call `call_person` first" in engine.imperative_preamble("can you start a call with Maya")
    for not_asked in ("join the call", "call the bot", "what call was that?", "don't call bob yet"):
        assert "call_person" not in engine.imperative_preamble(not_asked), not_asked
    assert "call_person" in engine.CALLS_PREAMBLE and "Meet" in engine.CALLS_PREAMBLE


# ── the ring limit (gate Low row: no rate limit on rings) ─────────────────────────────────────────

def test_a_caller_who_rang_one_person_too_often_is_refused_before_anything_rings(w, monkeypatch):
    """call_person re-rings a live call on every call: unbounded, a person (or their prompted agent)
    rang someone without end. The fourth ring to one person inside the window is a 429 with
    Retry-After, and nothing is rung or started."""
    monkeypatch.setenv("VEXA_ROOM_RING_PER_PERSON", "3")
    for _ in range(3):
        assert _call(w, {"person": "tester-b"}).status_code == 200
    assert len(_rings(w, "tester-b")) == 3
    started = len(w.meetings.calls)
    r = _call(w, {"person": "tester-b"})
    assert r.status_code == 429, r.text
    assert r.json()["detail"]["refused"] == "ring_rate_limited"
    assert int(r.headers["Retry-After"]) > 0
    assert len(_rings(w, "tester-b")) == 3 and len(w.meetings.calls) == started


def test_rings_under_the_limit_ring_and_another_person_is_counted_apart(w, monkeypatch):
    """The control: under the limit every call rings, and one person's count does not refuse a call
    to someone else."""
    monkeypatch.setenv("VEXA_ROOM_RING_PER_PERSON", "2")
    assert _call(w, {"person": "tester-b"}).status_code == 200
    assert _call(w, {"person": "tester-b"}).status_code == 200
    assert _call(w, {"person": "tester-b"}).status_code == 429
    r = _call(w, {"person": "tester-a"})
    assert r.status_code == 200, r.text
    assert r.json()["rang"] == ["tester-a"] and len(_rings(w, "tester-a")) == 1


def test_a_caller_is_limited_across_everyone_they_ring(w, monkeypatch):
    monkeypatch.setenv("VEXA_ROOM_RING_PER_CALLER", "2")
    assert _call(w, {"person": "tester-a"}).status_code == 200
    assert _call(w, {"person": "tester-b"}).status_code == 200
    r = _call(w, {"person": "dan"})
    assert r.status_code == 429 and _rings(w, "dan") == []
