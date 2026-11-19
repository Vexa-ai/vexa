"""0.13.4 slice S1 part E — the security floor's agent-api half (D24, D25, D27, decision 3).

* D24 — a routine an agent writes is a PROPOSAL: filed paused, armed only from the person's own
  session; the old-file migration fails closed.
* D25 — a turn an inbound email started runs on its own read-only `-mail` sibling and writes one
  page, the thread's reply file; everything else is a proposal.
* D27 — a room's grant is read every turn: call participants count in the audience, the unit id is
  versioned by the grant's digest, and a refreshed token is the same scope or no token.
* decision 3 — lives in `test_meeting_room.py` and deny test 9.

In-process only: fakes, sqlite, tmp dirs. No containers.
"""
from __future__ import annotations

import json
import time

import fakeredis
import pytest

from control_plane import delegation_refresh as drf
from control_plane import delegation_revocation as dr
from control_plane import room_echo
from control_plane import room_principal as rp
from control_plane import routine_flows as rf
from control_plane import signal_scope
from shared import delegation, units

from tests.test_r1_rooms import SECRET, _h, _is_room_unit, _room, world  # noqa: F401  (fixture)
from tests.test_routine_flows import _as, flows  # noqa: F401  (fixture)
from tests.test_routine_flows import world as routines_world  # noqa: F401  (fixture)


# ── D24: an agent proposes a routine; the person arms it ─────────────────────────────────────────

def test_an_agent_turns_routine_is_filed_paused(routines_world, flows):
    client, _sched, _root = routines_world
    r = client.post("/api/routines", headers=_as("human"),
                    json={"name": "Morning brief", "cron": "0 8 * * *", "prompt": "brief me"})
    assert r.status_code == 201, r.text
    assert [x["status"] for x in flows.rows] == ["draft"]
    card = r.json()["routine"]
    assert card["enabled"] is False and "turn it on" in card.get("paused_reason", "")


def _session() -> dict:
    """The person's signed-in browser session (D13: `credential_class: session`)."""
    from control_plane import identity_token
    from tests.test_routine_flows import KEY
    return {identity_token.HEADER: identity_token.sign(
        KEY, {"sub": "7", "email": "ada@example.com", "credential_class": "session"})}


def test_the_persons_own_routine_is_live_at_once(routines_world, flows):
    client, _sched, _root = routines_world
    r = client.post("/api/routines", headers=_session(),
                    json={"name": "Morning brief", "cron": "0 8 * * *", "prompt": "brief me"})
    assert r.status_code == 201 and [x["status"] for x in flows.rows] == ["active"]


def test_an_agent_turn_cannot_turn_a_routine_on(routines_world, flows):
    client, _sched, _root = routines_world
    flows.submit("7", "brief", "0 9 * * *", "x", activate=False)
    r = client.patch("/api/routines/brief/enabled", headers=_as("human"), json={"enabled": True})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "person_approval_required"
    assert [x["status"] for x in flows.rows] == ["draft"]
    # …switching one OFF stays open to it (a narrowing)
    flows.rows[0]["status"] = "active"
    assert client.patch("/api/routines/brief/enabled", headers=_as("human"),
                        json={"enabled": False}).status_code == 200


def _session() -> dict:
    """The person's signed-in browser session (D13: `credential_class: session`)."""
    from control_plane import identity_token
    from tests.test_routine_flows import KEY
    return {identity_token.HEADER: identity_token.sign(
        KEY, {"sub": "7", "email": "ada@example.com", "credential_class": "session"})}


def test_the_person_turns_the_proposal_on(routines_world, flows):
    client, _sched, _root = routines_world
    flows.submit("7", "brief", "0 9 * * *", "x", activate=False)
    r = client.patch("/api/routines/brief/enabled", headers=_session(), json={"enabled": True})
    assert r.status_code == 200 and [x["status"] for x in flows.rows] == ["active"]


def test_an_api_key_cannot_turn_a_draft_on(routines_world, flows):
    """D13: turning a routine on is a SESSION press — a key, whatever its scopes, is refused."""
    client, _sched, _root = routines_world
    flows.submit("7", "brief", "0 9 * * *", "x", activate=False)
    r = client.patch("/api/routines/brief/enabled", headers=_as(), json={"enabled": True})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "person_approval_required"
    assert [x["status"] for x in flows.rows] == ["draft"]
    # the refusal names the control the person has, never a board that does not exist
    said = json.dumps(r.json())
    assert "Routines board" not in said and "Turn on" in said


def test_a_draft_in_its_chat_is_listed_as_a_draft_with_its_chat(routines_world, flows):
    """The card the sidebar and the chat's Turn on line read: marked a draft, its schedule, its chat."""
    client, _sched, _root = routines_world
    flows.submit("7", "field-pulse", "0 7 * * *", "x", activate=False)
    flows.rows[-1]["params"]["thread"] = "pchat-abc123"
    card = next(c for c in client.get("/api/routines", headers=_session()).json()["routines"]
                if c["name"] == "field-pulse")
    assert card["draft"] is True and card["enabled"] is False and card["status"] == "draft"
    assert card["cron"] == "0 7 * * *" and card["thread"] == "pchat-abc123"


def test_turning_a_draft_on_marks_its_chat_a_routines(routines_world, flows):
    """Live app.dev 2026-10-18: the chat a draft was proposed in is the routine's chat from the
    moment it is turned on (rail icon, Routines filter), not from its first run tomorrow."""
    client, _sched, _root = routines_world
    flows.submit("7", "field-pulse", "0 7 * * *", "x", activate=False)
    flows.rows[-1]["params"]["thread"] = "pchat-abc123"
    r = client.patch("/api/routines/field-pulse/enabled", headers=_session(), json={"enabled": True})
    assert r.status_code == 200 and r.json()["thread"] == "pchat-abc123"
    rows = client.app.state.sessions.list("7")
    row = next(x for x in rows if x.get("session") == "pchat-abc123")
    assert (row.get("routine") or {}).get("flow") == "field-pulse"


def test_stood_behind_still_answers_yes_with_a_matching_approval(tmp_path):
    """Fail-closed is not fail-always: a person's recorded approval of these exact bytes arms it."""
    path = tmp_path / "alice" / "routines" / "daily.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\ncron: '0 7 * * *'\n---\nbrief me")
    (tmp_path / rf.STATE_DIR / "approvals").mkdir(parents=True)
    (tmp_path / rf.STATE_DIR / "approvals.v1.json").write_text("{}")
    (tmp_path / rf.STATE_DIR / "approvals" / "alice.json").write_text(json.dumps({"daily": "abc"}))
    assert rf._stood_behind(tmp_path, "alice", path, "abc") is True
    assert rf._stood_behind(tmp_path, "alice", path, "other") is False


# ── D25: a mail-triggered turn proposes only ─────────────────────────────────────────────────────

def test_a_mail_turn_has_its_own_sibling_unit():
    inv = units.make_dispatch(subject="alice", trigger="scheduled",
                              start=units.entrypoint(inline="x"),
                              context={"kind": "none", "session": "main", "flow_kind": "mail"})
    uid = units.dispatch_id(inv)
    assert units.is_mail_unit(uid) and uid == units.mail_unit_id("alice", "main")
    plain = units.make_dispatch(subject="alice", trigger="scheduled",
                                start=units.entrypoint(inline="x"),
                                context={"kind": "none", "session": "main"})
    assert units.dispatch_id(plain).endswith(units.ROUTINE_UNIT_SUFFIX)


def _mail_turn(client, session="main"):
    return client.post("/api/chat", json={"prompt": "[email-reply] hi", "session": session},
                       headers={**_h("alice"), "X-Internal-Secret": SECRET,
                                "X-Vexa-Flow-Regime": "autonomous", "X-Vexa-Flow": "email_chat",
                                "X-Vexa-Flow-Step": "feedback_turn", "X-Vexa-Flow-Kind": "mail"})


def test_a_mail_turn_runs_read_only_with_one_page(world):
    client, store, calls, spawned, root, _d = world
    r = _mail_turn(client)
    assert r.status_code == 200, r.text
    _inv, kw = calls[-1]
    assert kw["least_privilege"] is True and kw["target"] == ""
    assert kw["signal_page"]["path"] == "mail_outbox/main.md"
    uid, env = spawned[-1]
    assert units.is_mail_unit(uid)
    assert env["VEXA_LEAST_PRIVILEGE"] == "event_agent"
    assert not any(m["write"] for m in json.loads(env["VEXA_MOUNTS"])
                   if m.get("role") not in ("system",))


def test_a_mail_turn_writes_its_reply_file_and_nothing_else(world):
    client, store, calls, spawned, root, _d = world
    unit = units.mail_unit_id("alice", "main")
    signal_scope.record(None, unit, {"meeting": "", "workspace": "", "path": "mail_outbox/main.md"})
    hdr = {"X-User-Id": "alice", "x-user-regime": "autonomous", "x-user-delegation-workspaces": "",
           "x-user-delegation-unit": unit}
    r = client.put("/api/workspace/file", headers=hdr,
                   json={"path": "notes/settings.md", "content": "attacker text\n"})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "signal_scope"
    assert not (root / "alice" / "notes" / "settings.md").exists()
    r = client.put("/api/workspace/file", headers=hdr,
                   json={"path": "mail_outbox/main.md", "content": "Thanks — noted.\n"})
    assert r.status_code == 200, r.text


# ── D27: the room's grant, every turn ────────────────────────────────────────────────────────────

def test_a_guest_joining_moves_the_room_to_a_new_unit_and_audience(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa hi"}, headers=_h("bob"))
    before = spawned[-1][0]
    store.join(sid, "dave")                                   # not a member of the workspace
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa and now?"}, headers=_h("bob"))
    after, env = spawned[-1]
    assert _is_room_unit(before, sid) and _is_room_unit(after, sid) and before != after
    claims = delegation.verify_delegation("d" * 32, env["VEXA_MCP_DELEGATION_TOKEN"])
    assert claims["scope"]["audience"] == "cross_org"


def test_call_participants_count_in_the_audience(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    members = rp.resolve(store, root, sid, speaker="bob", participants=["alice", "bob"])
    assert members.audience == "org"
    outsider = rp.resolve(store, root, sid, speaker="bob", participants=["alice", "Mallory Vex"])
    assert outsider.audience == "cross_org"
    assert members.unit != outsider.unit                       # a different grant, a different worker


class _CallStore:
    """A room store whose room holds a live call (meeting 77)."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def active_call(self, session_id):
        return {"meeting_id": 77, "room": "r1"}


def test_a_live_call_nobody_can_read_is_cross_org(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    held = _CallStore(store)
    assert rp.resolve(held, root, sid, speaker="bob").audience == "cross_org"       # no lookup
    assert rp.resolve(held, root, sid, speaker="bob",
                      call_lookup=lambda mid: None).audience == "cross_org"       # unreadable
    row = {"id": 77, "data": {"participants": [{"name": "alice"}, "Vexa"], "bot_name": "Vexa"}}
    assert rp.resolve(held, root, sid, speaker="bob",
                      call_lookup=lambda mid: row).audience == "cross_org"        # a name places nobody
    row = {"id": 77, "data": {"participants": ["Vexa"], "bot_name": "Vexa"}}
    assert rp.resolve(held, root, sid, speaker="bob",
                      call_lookup=lambda mid: row).audience == "org"              # bot left out


def test_a_call_guest_named_like_a_member_is_not_placed():
    """A meeting display name is whatever its holder typed: a guest who calls themself "Alice" (or
    types Alice's address as their name) is placed nowhere. An entry that carries an address is
    placed by it."""
    roster = [{"subject": "7", "email": "alice@acme.test", "name": "Alice", "role": "owner"}]
    member = lambda ws, who: True  # noqa: E731 — the chat's people are not the question here
    row = {"data": {"participants": [{"name": "Alice"}, {"name": "A.", "email": "alice@acme.test"},
                                     "alice@acme.test", "Vexa"], "bot_name": "Vexa"}}
    people = rp.call_participants_of(row)
    assert people == ["name:Alice", "alice@acme.test", "name:alice@acme.test"]
    for who in (["Alice"], ["name:Alice"], ["name:alice@acme.test"]):
        assert rp.audience_of("/nowhere", "team", [], is_member=member, participants=who,
                              roster=roster) == "cross_org", who
    assert rp.audience_of("/nowhere", "team", [], is_member=member, participants=["alice@acme.test", "7"],
                          roster=roster) == "org"


def test_the_grant_hash_rides_the_unit_and_the_echo_still_finds_the_room():
    p = rp.RoomPrincipal(workspace="team", owner="alice", session="s-1", roster=("alice:owner",))
    q = rp.RoomPrincipal(workspace="team", owner="alice", session="s-1",
                         roster=("alice:owner", "bob:contributor"))
    assert p.unit != q.unit and p.unit.startswith(rp.room_unit_id("team", "s-1") + "-g")
    for u in (p.unit, p.unit + "-signal", q.unit + units.ROUTINE_UNIT_SUFFIX,
              rp.RoomPrincipal(workspace="team", owner="alice", session="s-1", writable=False).unit):
        assert room_echo.session_of(u) == "s-1", u


def test_a_refresh_that_would_change_the_scope_publishes_nothing(monkeypatch):
    secret, ttl = "test-delegation-secret", 1800
    store = fakeredis.FakeRedis(decode_responses=True)
    unit = rp.room_unit_id("team", "s-room1")
    now = int(time.time())
    tok = delegation.mint_delegation(secret, subject="alice", regime="human", workspaces=["team"],
                                     unit=unit, shared=True, audience=delegation.AUDIENCE_CROSS_ORG,
                                     ttl_sec=ttl, now=now)
    store.set(dr.CURRENT_PREFIX + unit, tok)
    real = delegation.mint_delegation

    def lossy(*a, **kw):                     # a future mint that forgets a narrowing
        kw.pop("audience", None)
        return real(*a, **kw)

    monkeypatch.setattr(drf.delegation, "mint_delegation", lossy)
    assert drf.Refresher(secret, lambda: ttl)(store, unit, now + ttl // 2 + 1) is None
    assert store.get(dr.CURRENT_PREFIX + unit) == tok          # the held token stands


@pytest.mark.parametrize("unit", ["agent-ws-team-room-s1-g0123456789-signal", "agent-x-main-signal"])
def test_every_signal_unit_is_held_to_one_page(unit):
    class _Req:
        headers = {signal_scope.UNIT_HEADER: unit}
    assert signal_scope.signal_unit(_Req()) == unit


def test_a_routine_an_api_key_files_is_a_draft_until_the_person_turns_it_on(routines_world, flows):
    """D13: creating a routine does not arm it unless the creator is the person's signed-in session."""
    client, _sched, _root = routines_world
    r = client.post("/api/routines", headers=_as(), json={"name": "keyed", "cron": "0 9 * * *", "prompt": "x"})
    assert r.status_code == 201, r.text
    assert [x["status"] for x in flows.rows if x["name"] == "keyed"] == ["draft"]
    s = client.post("/api/routines", headers=_session(), json={"name": "pressed", "cron": "0 9 * * *", "prompt": "x"})
    assert s.status_code == 201, s.text
    assert [x["status"] for x in flows.rows if x["name"] == "pressed"] == ["active"]
