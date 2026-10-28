"""0.13.4 S4 — rooms and tagging (PRD D36, D21, FD4, M1, M8; UI 7z, 7hh).

L2, in-process: a real FastAPI app over a sqlite session store, the file policy store (the registry's
stand-in), a fake LiveKit room reader and a captured notification bus. No redis, no containers.

Workspace ``team``: alice owner, bob and dan contributors, vic a reader. ``handbook``: carol owner,
alice and erin contributors (so alice knows erin, who is not in ``team``). Synthetic people only.
"""
from __future__ import annotations

import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from control_plane import pair_rooms, room_membership as rm, room_ring, workspace_membership as m
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.livekit_rooms import LiveKit
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

from tests._session_fakes import MemBlobs
from tests.test_session_routes import _FakeIdentity, _FakeReader, _FakeRuntime

SECRET = "s" * 32
ACCOUNTS = {"alice@acme.test": "alice", "bob@acme.test": "bob", "vic@acme.test": "vic",
            "dan@acme.test": "dan", "erin@other.test": "erin", "carol@acme.test": "carol",
            "frank@acme.test": "frank"}


def H(who: str, **extra) -> dict:
    # a person at the terminal: identity's session credential class (S5, D13), stamped by the guard
    return {"X-User-Id": who, "x-user-email": f"{who}@acme.test", "x-user-credential-class": "session", **extra}


AGENT = {"x-user-regime": "human", "x-user-delegation-workspaces": "team", "x-user-delegation-unit": "u-1"}


class _Rooms(LiveKit):
    rooms: dict = {}

    def room(self, name):
        return self.rooms.get(name)


def _git(ws):
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "t"], check=True)


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    monkeypatch.setenv("VEXA_FEATURES", "all,rooms,transcript_signals")
    monkeypatch.setenv("VEXA_INTERNAL_API_SECRET", "internal-test-secret-0123456789abcdef")
    root = tmp_path / "ws"
    (root / "_global").mkdir(parents=True)
    idx = m.InMemoryMembershipIndex()
    for ws, owner in (("team", "alice"), ("handbook", "carol")):
        (root / ws).mkdir()
        _git(root / ws)
        m.ensure_owner(root, ws, owner, index=idx, email=f"{owner}@acme.test")
    for who, role in (("bob", "contributor"), ("dan", "contributor"), ("vic", "viewer")):
        m.grant_membership(root, "team", who, role, added_by="alice", index=idx, email=f"{who}@acme.test")
    for who, mail in (("alice", "alice@acme.test"), ("erin", "erin@other.test")):
        m.grant_membership(root, "handbook", who, "contributor", added_by="carol", index=idx, email=mail)
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="", internal_api_secret=SECRET,
                             global_system_workspace_path=str(root / "_global"), ui_url="https://app.test")
    dispatcher = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
    turns = []
    dispatcher.dispatch = lambda inv, **kw: turns.append((inv, kw)) or "unit-1"
    lk = _Rooms(url="wss://calls.example.test", api_key="APIkey1", api_secret=SECRET)
    _Rooms.rooms = {}
    bus = []
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store, membership_index=idx, livekit=lk,
                     call_share_redeemer=lambda *_a: True,
                     notify_publish=lambda ch, payload: bus.append((ch, json.loads(payload))),
                     email_subject_lookup=lambda a: ACCOUNTS.get(str(a).lower()))
    c = TestClient(app, raise_server_exceptions=False)
    yield type("W", (), {"c": c, "store": store, "root": root, "idx": idx, "bus": bus, "turns": turns})
    store.close()


def _room(w, *, owner="alice", members=(), visibility="closed", name="test"):
    r = w.c.post("/api/rooms", json={"workspace": "team", "name": name, "visibility": visibility,
                                     "members": list(members)}, headers=H(owner))
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def _frames(w, who: str, kind: str) -> list:
    return [p for ch, p in w.bus if ch == f"u:{who}:notifications" and p.get("type") == kind]


def _notices(w, who: str, kind: str) -> list:
    return [p["notification"] for p in _frames(w, who, "notification") if p["notification"]["kind"] == kind]


def _start_call(w, sid, *, owner="alice", room="call-abc123", transcribed=True):
    _Rooms.rooms[room] = {"name": room, "num_participants": 0,
                          "metadata": {"owner": owner, "session_id": sid, "transcribed": transcribed,
                                       "meeting_id": 41 if transcribed else None}}
    body = {"room": room, **({"meeting_id": 41, "share_token": "41.secret"} if transcribed else {})}
    return w.c.post(f"/api/sessions/{sid}/call", headers=H(owner), json=body)


# ── D36: who is in a room ───────────────────────────────────────────────────────────────────────

def test_an_open_rooms_members_are_its_workspaces(w):
    sid = _room(w, visibility="open")
    got = {x["principal"]: x for x in w.c.get(f"/api/rooms/{sid}/members", headers=H("bob")).json()["members"]}
    assert set(got) == {"alice", "bob", "dan", "vic"} and got["alice"]["role"] == "owner"
    assert all(x["derived"] for x in got.values())
    assert w.store.role_of(sid, "vic") == "member"                 # a reader takes part
    assert w.store.role_of(sid, "carol") is None                   # not in the workspace, not in its rooms
    # leaving the workspace is leaving its open rooms — no room row anywhere to forget
    m.remove_member(w.root, "team", "dan", index=w.idx)
    assert w.store.role_of(sid, "dan") is None
    assert w.c.get(f"/api/rooms/{sid}/members", headers=H("dan")).status_code == 404


def test_a_closed_rooms_members_are_explicit_registry_rows(w):
    sid = _room(w, members=("bob",))
    rows = json.loads((w.root / ".rooms" / f"{sid}.json").read_text())
    assert [(r["subject"], r["role"]) for r in rows] == [("alice", "owner"), ("bob", "member")]
    assert w.store.role_of(sid, "dan") is None                     # a workspace member is not in it
    # the participant row is a projection, never the authority: a forged row grants nothing
    w.store.join(sid, "dan")
    assert w.store.role_of(sid, "dan") is None
    assert sid not in {r["id"] for r in w.store.list_for("dan")}


def test_a_closed_room_adopts_its_participant_rows_once(w):
    sid = _room(w, members=("bob",))
    (w.root / ".rooms" / f"{sid}.json").unlink()                     # a room made before the registry
    assert w.store.role_of(sid, "bob") == "member"
    assert (w.root / ".rooms" / f"{sid}.json").exists()


def test_an_unreadable_room_registry_refuses(w):
    sid = _room(w, members=("bob",))
    (w.root / ".rooms" / f"{sid}.json").write_text("{not json")
    assert w.store.role_of(sid, "bob") is None and w.store.role_of(sid, "alice") is None
    assert w.c.get(f"/api/rooms/{sid}/members", headers=H("alice")).status_code == 404


def test_closing_an_open_room_keeps_its_followers_and_opening_drops_the_rows(w):
    sid = _room(w, visibility="open", members=("bob",))
    r = w.c.patch(f"/api/sessions/{sid}/room", json={"visibility": "closed"}, headers=H("alice"))
    assert r.status_code == 200, r.text
    assert w.store.role_of(sid, "bob") == "member" and w.store.role_of(sid, "dan") is None
    r = w.c.patch(f"/api/sessions/{sid}/room", json={"visibility": "open", "confirm_history": True},
                  headers=H("alice"))
    assert r.status_code == 200 and not (w.root / ".rooms" / f"{sid}.json").exists()
    assert w.store.role_of(sid, "dan") == "member"


# ── D21: tagging, four cases ────────────────────────────────────────────────────────────────────

def test_case1_a_room_member_is_only_mentioned(w):
    sid = _room(w, members=("bob",))
    r = w.c.post(f"/api/rooms/{sid}/tag", json={"subject": "bob"}, headers=H("alice")).json()
    assert r["case"] == "member" and r["card"] is None


def test_case2_a_workspace_member_gets_the_three_way_card_and_add_tells_her(w):
    sid = _room(w)
    r = w.c.post(f"/api/rooms/{sid}/tag", json={"subject": "bob"}, headers=H("alice")).json()
    assert r["case"] == "workspace_member"
    assert r["card"]["actions"] == ["add", "always", "mention"] and r["card"]["only_you"] is True
    assert w.store.role_of(sid, "bob") is None                      # the tag itself changed nothing
    done = w.c.post(f"/api/rooms/{sid}/members", json={"subject": "bob"}, headers=H("alice"))
    assert done.status_code == 200 and done.json()["added"] is True, done.text
    assert w.store.role_of(sid, "bob") == "member"
    assert _notices(w, "bob", "room_added")
    lines = [n["text"] for n in w.store.notes(sid)]
    assert any("added" in t and "#test" in t for t in lines)


def test_always_by_the_owner_applies_the_line_and_the_next_tag_adds_with_undo(w):
    sid = _room(w)
    r = w.c.post(f"/api/rooms/{sid}/members", json={"subject": "bob", "always": True}, headers=H("alice")).json()
    assert r["always"]["applied"] is True
    rules = m.read_rules(w.root, "team")
    assert any(x["action"] == "room.mention_adds_member" and x["principals"] == ["alice"] for x in rules)
    log = subprocess.run(["git", "-C", str(w.root / "team"), "log", "-1", "--format=%B"],
                         capture_output=True, text=True).stdout
    assert "Approved-by: alice" in log
    t = w.c.post(f"/api/rooms/{sid}/tag", json={"subject": "dan"}, headers=H("alice")).json()
    assert t["case"] == "added_by_rule" and w.store.role_of(sid, "dan") == "member"
    undo = w.c.delete(f"/api/rooms/{sid}/members/dan", headers=H("alice"))
    assert undo.status_code == 200 and w.store.role_of(sid, "dan") is None
    # the line is alice's own: bob's tag still asks
    w.c.post(f"/api/rooms/{sid}/members", json={"subject": "bob"}, headers=H("alice"))
    assert w.c.post(f"/api/rooms/{sid}/tag", json={"subject": "dan"}, headers=H("bob")).json()["case"] == "workspace_member"


def test_always_by_a_contributor_is_a_proposal_for_the_owner(w):
    sid = _room(w, members=("bob",))
    r = w.c.post(f"/api/rooms/{sid}/members", json={"subject": "dan", "always": True}, headers=H("bob")).json()
    assert r["added"] is True and r["always"]["applied"] is False and r["always"]["decides"] == "alice"
    assert m.read_rules(w.root, "team") == []
    waiting = w.c.get("/api/workspace/policy/proposals", headers=H("alice")).json()["proposals"]
    assert any(p["id"] == r["always"]["proposal"] for p in waiting)


def test_case3_a_known_person_outside_the_workspace_gets_an_invite_that_lands_in_the_room(w):
    sid = _room(w)
    r = w.c.post(f"/api/rooms/{sid}/tag", json={"subject": "erin"}, headers=H("alice")).json()
    assert r["case"] == "known" and r["card"]["actions"] == ["invite", "mention"]
    assert r["email"] == "…@other.test"                            # her address is never shown (M1)
    inv = w.c.post(f"/api/rooms/{sid}/members", json={"subject": "erin"}, headers=H("alice")).json()
    assert inv["invited"] is True and inv["role"] == "contributor"
    token = inv["link"].split("i=", 1)[1]
    joined = w.c.post("/api/workspace/invites/accept", json={"token": token},
                      headers={"X-User-Id": "erin", "x-user-email": "erin@other.test"})
    assert joined.status_code == 200, joined.text
    assert (joined.json().get("room") or {}).get("id") == sid and w.store.role_of(sid, "erin") == "member"


def test_case4_an_unknown_address_is_invited_by_the_workspace_default(w):
    sid = _room(w)
    r = w.c.post(f"/api/rooms/{sid}/tag", json={"email": "marta@venue.example"}, headers=H("alice")).json()
    assert r["case"] == "unknown_email" and r["seat"] == "invite"
    assert r["seat_rule"].startswith("floor.no-guest-seats@")     # decision 9: a floor holds guests
    assert "always" not in r["card"]["actions"]


def test_an_invite_answers_the_same_with_or_without_an_account(w):
    """M1: tagging by address is no oracle — the answer's shape does not say whether it has an account."""
    sid = _room(w)
    a = w.c.post(f"/api/rooms/{sid}/members", json={"email": "frank@acme.test"}, headers=H("alice")).json()
    b = w.c.post(f"/api/rooms/{sid}/members", json={"email": "nobody@nowhere.example"}, headers=H("alice")).json()
    assert set(a) == set(b) and a["case"] == b["case"] == "unknown_email"
    ta = w.c.post(f"/api/rooms/{sid}/tag", json={"email": "frank@acme.test"}, headers=H("alice")).json()
    tb = w.c.post(f"/api/rooms/{sid}/tag", json={"email": "nobody@nowhere.example"}, headers=H("alice")).json()
    assert ta["case"] == tb["case"] and set(ta) == set(tb)


def test_a_reader_invites_as_a_reader_only(w):
    sid = _room(w, owner="alice", members=("vic",))
    r = w.c.post(f"/api/rooms/{sid}/tag", json={"email": "new@venue.example"}, headers=H("vic")).json()
    assert r["role"] == "reader"


def test_an_agent_never_adds_anyone(w):
    sid = _room(w)
    r = w.c.post(f"/api/rooms/{sid}/members", json={"subject": "bob"}, headers=H("alice", **AGENT))
    assert r.status_code == 403 and w.store.role_of(sid, "bob") is None


def test_the_members_panel(w):
    sid = _room(w, members=("bob",))
    w.c.post(f"/api/rooms/{sid}/members", json={"email": "marta@venue.example"}, headers=H("alice"))
    p = w.c.get(f"/api/rooms/{sid}/people", headers=H("bob")).json()
    assert [x["principal"] for x in p["members"]] == ["alice", "bob"]
    assert {x["principal"] for x in p["workspace_others"]} == {"dan", "vic"}
    assert len(p["invited"]) == 1 and p["invited"][0]["label"] == "someone invited"     # bob is not its owner
    assert all("email" not in x for x in p["members"])
    owner = w.c.get(f"/api/rooms/{sid}/people", headers=H("alice")).json()
    assert owner["invited"][0]["label"] == "marta@venue.example"


# ── ringing: room members only ──────────────────────────────────────────────────────────────────

def test_a_room_call_rings_its_members_and_nobody_else(w):
    sid = _room(w, members=("bob",))
    r = _start_call(w, sid)
    assert r.status_code == 200, r.text
    assert r.json()["rang"] == ["bob"]
    assert _frames(w, "bob", "ring") and not _frames(w, "dan", "ring")
    ring = _notices(w, "bob", "call_started")[0]
    assert ring["expires_at"] > time.time()                         # a ring is a notice that expires


def test_an_empty_room_says_how_to_ring_someone(w):
    sid = _room(w)
    out = _start_call(w, sid).json()
    assert out["rang"] == [] and "@mention or Invite" in out["hint"]


def test_a_member_added_mid_call_is_rung_at_once(w):
    sid = _room(w)
    _start_call(w, sid)
    out = w.c.post(f"/api/rooms/{sid}/members", json={"subject": "dan"}, headers=H("alice")).json()
    assert out["rang"] == ["dan"] and _frames(w, "dan", "ring")


def test_an_open_room_rings_only_members_who_opened_it_lately(w, monkeypatch):
    sid = _room(w, visibility="open", members=("bob",))
    w.c.post(f"/api/rooms/{sid}/join", headers=H("dan"))
    with w.store._tx():                                             # dan opened it 40 days ago
        w.store._x("UPDATE agent_session_participant SET added = ?, read_at = NULL WHERE principal = 'dan'",
                   (time.time() - 40 * 86400,))
    assert sorted(rm.ringable(w.store, w.root, sid, exclude="alice")) == ["bob"]      # vic never opened it
    monkeypatch.setenv("VEXA_ROOM_RING_DERIVED_DAYS", "60")
    assert sorted(rm.ringable(w.store, w.root, sid, exclude="alice")) == ["bob", "dan"]


# ── FD4: rooms of two ───────────────────────────────────────────────────────────────────────────

def test_message_a_person_opens_one_room_of_two_and_reuses_it(w):
    a = w.c.post("/api/rooms/direct", json={"subject": "bob"}, headers=H("alice"))
    assert a.status_code == 200 and a.json()["created"] is True, a.text
    b = w.c.post("/api/rooms/direct", json={"subject": "alice"}, headers=H("bob")).json()
    assert b["session_id"] == a.json()["session_id"] and b["created"] is False
    sid = b["session_id"]
    ws = w.store.room(sid)["workspace"]
    assert ws == pair_rooms.pair_id("alice", "bob") and pair_rooms.is_pair(w.root, ws)
    assert {r["role"] for r in m.read_members(w.root, ws)} == {"owner"}       # both are owners
    assert w.store.room(sid)["created_by"] == "alice"                           # bills_to: the opener
    # hidden: never a workspace in a list, a mount or the people directory
    assert ws not in {r["workspace_id"] for r in m.list_memberships(w.root, "alice")}
    assert ws not in json.dumps(w.c.get("/api/people/mentionable", headers=H("alice")).json())


def test_a_room_of_two_refuses_a_third_person(w):
    sid = w.c.post("/api/rooms/direct", json={"subject": "bob"}, headers=H("alice")).json()["session_id"]
    assert w.c.post(f"/api/rooms/{sid}/tag", json={"subject": "dan"}, headers=H("alice")).json()["case"] == "pair_full"
    assert w.c.post(f"/api/rooms/{sid}/members", json={"subject": "dan"}, headers=H("alice")).status_code == 409
    ws = w.store.room(sid)["workspace"]
    with pytest.raises(m.MembershipError):
        m.grant_membership(w.root, ws, "dan", "contributor", added_by="alice", index=w.idx)


def test_only_someone_you_share_a_workspace_with(w):
    assert w.c.post("/api/rooms/direct", json={"subject": "carol"}, headers=H("bob")).status_code == 404


def test_start_call_in_a_room_of_two_rings_the_other_person(w):
    sid = w.c.post("/api/rooms/direct", json={"subject": "bob"}, headers=H("alice")).json()["session_id"]
    assert _start_call(w, sid).json()["rang"] == ["bob"]


# ── the switches (7hh) ──────────────────────────────────────────────────────────────────────────

def _pair(w):
    return w.c.post("/api/rooms/direct", json={"subject": "bob"}, headers=H("alice")).json()["session_id"]


def test_in_a_room_of_two_either_turns_off_and_on_needs_both(w):
    sid = _pair(w)
    off = w.c.post(f"/api/rooms/{sid}/switches", json={"switch": "vexa", "on": False}, headers=H("bob")).json()
    assert off["applied"] is True and off["vexa"] is False
    ask = w.c.post(f"/api/rooms/{sid}/switches", json={"switch": "vexa", "on": True}, headers=H("alice")).json()
    assert ask["applied"] is False and ask["asked"] == "bob" and ask["vexa"] is False
    assert _notices(w, "bob", "room_switch_ask")
    # alice cannot answer her own ask
    assert w.c.post(f"/api/rooms/{sid}/switches/vexa/answer", json={"accept": True}, headers=H("alice")).status_code == 409
    done = w.c.post(f"/api/rooms/{sid}/switches/vexa/answer", json={"accept": True}, headers=H("bob")).json()
    assert done["applied"] is True and done["vexa"] is True and done["asks"] == {}


def test_a_keep_off_answer_keeps_it_off(w):
    sid = _pair(w)
    w.c.post(f"/api/rooms/{sid}/switches", json={"switch": "transcribe", "on": False}, headers=H("alice"))
    w.c.post(f"/api/rooms/{sid}/switches", json={"switch": "transcribe", "on": True}, headers=H("alice"))
    out = w.c.post(f"/api/rooms/{sid}/switches/transcribe/answer", json={"accept": False}, headers=H("bob")).json()
    assert out["transcribe"] is False and out["asks"] == {}


def test_transcribe_off_refuses_a_transcribed_call_and_binds_audio_only(w):
    sid = _pair(w)
    w.c.post(f"/api/rooms/{sid}/switches", json={"switch": "transcribe", "on": False}, headers=H("bob"))
    r = _start_call(w, sid)
    assert r.status_code == 409 and r.json()["detail"]["refused"] == "transcription_off_in_this_room"
    assert _start_call(w, sid, room="call-quiet1", transcribed=False).status_code == 200


def test_in_a_bigger_room_owners_switch_and_policy_can_lock(w):
    sid = _room(w, members=("bob",))
    r = w.c.post(f"/api/rooms/{sid}/switches", json={"switch": "vexa", "on": False}, headers=H("bob"))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "owners_switch"
    assert w.c.post(f"/api/rooms/{sid}/switches", json={"switch": "vexa", "on": False},
                    headers=H("alice")).json()["vexa"] is False
    w.c.post(f"/api/rooms/{sid}/switches", json={"switch": "vexa", "on": True}, headers=H("alice"))
    # the workspace locks transcription on: a DENY line on turning it off
    (w.root / "team" / "policy").mkdir(exist_ok=True)
    (w.root / "team" / "policy" / "rules.json").write_text(json.dumps(
        [{"id": "team.calls-have-notes", "effect": "deny", "action": "room.switch.transcribe.off"}]))
    r = w.c.post(f"/api/rooms/{sid}/switches", json={"switch": "transcribe", "on": False}, headers=H("alice"))
    assert r.status_code == 423 and r.json()["detail"]["rule"].startswith("team.calls-have-notes@")
    view = w.c.get(f"/api/rooms/{sid}/switches", headers=H("bob")).json()
    assert view["locks"]["transcribe"]["off"].startswith("team.calls-have-notes@") and view["can_switch"] is False


def test_an_agent_never_flips_a_switch(w):
    sid = _pair(w)
    r = w.c.post(f"/api/rooms/{sid}/switches", json={"switch": "vexa", "on": False}, headers=H("alice", **AGENT))
    assert r.status_code == 403


# ── M1: the invite preview, unauthenticated ─────────────────────────────────────────────────────

def test_an_anonymous_invite_preview_shows_the_workspace_name_only(w):
    sid = _room(w)
    inv = w.c.post(f"/api/rooms/{sid}/members", json={"email": "marta@venue.example"}, headers=H("alice")).json()
    token = inv["link"].split("i=", 1)[1]
    anon = w.c.get("/api/workspace/invites/preview", params={"token": token}).json()
    assert anon["name"] and "shared_by" not in anon and "purpose" not in anon
    signed = w.c.get("/api/workspace/invites/preview", params={"token": token}, headers=H("bob")).json()
    assert "shared_by" in signed


def test_ring_helper_never_raises(w):
    assert room_ring.ring(w.store, w.root, None, "s-nope", caller="alice") == []


# ── founder I20: who pays a room's turn ───────────────────────────────────────────────────────────
def test_a_room_of_two_bills_whoever_started_the_turn_and_an_ordinary_room_its_owner(w):
    from control_plane import room_principal as rp
    sid = w.c.post("/api/rooms/direct", json={"subject": "bob"}, headers=H("alice")).json()["session_id"]
    for speaker in ("alice", "bob"):
        p = rp.resolve(w.store, w.root, sid, speaker=speaker)
        assert p is not None and p.pair is True
        assert p.payer == speaker, (speaker, p.payer)          # the initiator, not the pair's creator
    team = _room(w)
    p = rp.resolve(w.store, w.root, team, speaker="bob")
    assert p.pair is False and p.payer == "alice"              # rooms bill the workspace owner (as built)


def test_the_chat_list_names_a_room_of_two_by_the_other_person(w):
    sid = w.c.post("/api/rooms/direct", json={"subject": "bob"}, headers=H("alice")).json()["session_id"]
    w.c.post(f"/api/sessions/{sid}/messages", json={"text": "hi alice"}, headers=H("bob"))
    rows = {r.get("session_id"): r for r in w.c.get("/api/sessions", headers=H("alice")).json()["sessions"]}
    row = rows[sid]
    assert row["visibility"] == "direct" and row["other_subject"] == "bob" and row["other_name"]
    assert row["unread"] >= 1
    team = _room(w)
    assert rows.get(team) is None or rows[team].get("visibility") == "closed"
