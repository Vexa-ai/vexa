"""`POST /api/desktop/attention` and `POST /api/desktop/refs` — the attention home's agent-api half
(home R3 / E1, Vexa-ai/vexa#1783).

The status cards, the Now strip and rich READMEs read these. A read model: every item comes from the
ONE owner that already holds that fact, composed per request — nothing stored, no model call — and
nothing the viewer cannot open is ever surfaced or named.

L2: the same app and fakes as `test_desktop_sections.py` (real git workspaces, a sqlite session store,
the in-memory chat index, a fake flows client, meetings list and LiveKit room reader).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import jsonschema
import pytest

from control_plane import home_model
from control_plane import routine_flows
from control_plane.identity_token import DELEGATION_HEADERS
from tests.test_desktop_sections import (H, _Flows, _Meetings, _Rooms, _Runtime, _iso, _meeting, _policy,  # noqa: F401
                                         _shared, env)

SCHEMA = Path(__file__).resolve().parents[1] / "contracts" / "desktop.v1" / "desktop.schema.json"
ATT, REFS = "/api/desktop/attention", "/api/desktop/refs"
DELEGATED = {DELEGATION_HEADERS["workspaces"]: "*", DELEGATION_HEADERS["regime"]: "human"}



@pytest.fixture(autouse=True)
def _home2_on(env, monkeypatch):
    """`home2` is EXPLICIT: never in `all`, so these tests switch it on by name (after `env`)."""
    monkeypatch.setenv("VEXA_FEATURES", "all,home2")

def _valid(body, shape):
    schema = json.loads(SCHEMA.read_text())
    jsonschema.Draft202012Validator({"$ref": f"#/$defs/{shape}", "$defs": schema["$defs"]}).validate(body)


def _att(env, who="u1", **body):
    r = env["c"].post(ATT, headers=H(who), json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    _valid(out, "Attention")
    return out


def _refs(env, refs, who="u1", **body):
    r = env["c"].post(REFS, headers=H(who), json={"refs": refs, **body})
    assert r.status_code == 200, r.text
    out = r.json()
    _valid(out, "Refs")
    return out["refs"]


def _ids(needs):
    return [n["id"] for n in needs]


def _flow(name, scope="user:u1", last=None, thread=None, status="active"):
    f = {"name": name, "version": 1, "scope": scope, "trigger": {"kind": "schedule", "cron": "0 7 * * *"},
         "steps": ["agent_run"], "status": status, "params": {"instruction": "Brief me"},
         "schedule": "daily at 07:00 UTC", "next_run_at": time.time() + 3600}
    if thread:
        f["params"]["thread"] = thread
    if last:
        f["last_run"] = last
    return f


def _room(env, owner, name, target, *others, title="Plan"):
    row = env["store"].create(owner, name=name, title=title)
    env["sess"].upsert(owner, name, title=title)
    env["sess"].set_target(owner, name, target)
    for o in others:
        env["store"].add_participant(row["id"], owner, o)
    return row


# ── the switch and who may ask ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [ATT, REFS])
def test_switched_off_both_routes_refuse_typed(env, monkeypatch, url):
    monkeypatch.setenv("VEXA_FEATURES", "all,-home2")
    r = env["c"].post(url, headers=H("u1"), json={})
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "feature_disabled" and r.json()["detail"]["feature"] == "home2"


@pytest.mark.parametrize("url", [ATT, REFS])
def test_a_delegated_worker_is_refused(env, url):
    r = env["c"].post(url, headers={**H("u1"), **DELEGATED}, json={})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "person_only"


def test_too_many_refs_or_cards_is_refused(env):
    assert env["c"].post(REFS, headers=H("u1"), json={"refs": [f"room:s-{i}" for i in range(101)]}).status_code == 422
    assert env["c"].post(ATT, headers=H("u1"), json={"cards": [{"id": str(i)} for i in range(101)]}).status_code == 422


# ── the shape ──────────────────────────────────────────────────────────────────────────────────

def test_a_new_person_has_their_desk_and_nothing_waiting(env):
    out = _att(env)
    assert set(out["workspaces"]) == {"personal"}
    assert out["needs"] == [] and isinstance(out["generated_at"], int)
    assert out["workspaces"]["personal"] == {"needs": [], "working": [], "rooms_active": 0, "live_call": None,
                                             "last": out["workspaces"]["personal"]["last"]}


def test_the_keys_are_the_slugs_the_sections_route_lists(env):
    wid = _shared(env, "Client X", "u2", u1="contributor")
    theirs = _shared(env, "Theirs", "u2")
    _policy(env, "presence_visibility: workspace_members")
    sections = env["c"].post("/api/desktop/sections", headers=H("u1"), json={"refs": []}).json()
    out = _att(env)
    assert set(out["workspaces"]) == {w["slug"] for w in sections["workspaces"]}
    assert wid in out["workspaces"] and theirs not in out["workspaces"]


# ── needs you ──────────────────────────────────────────────────────────────────────────────────

def test_a_desk_proposal_is_an_accept(env):
    r = env["c"].post("/api/proposals", headers=H("u1"), json={"source": "meeting:9", "act": "Send the recap"})
    assert r.status_code == 201
    out = _att(env)
    [n] = [n for n in out["needs"] if n["kind"] == "proposal"]
    assert n["text"] == "Send the recap" and n["workspace"] == "personal"
    assert n["act"]["kind"] == "accept-proposal" and n["act"]["proposal"] == r.json()["id"]
    assert n["act"]["source"] == "desk"


def test_an_ended_call_makes_no_review_item_on_its_own(env):
    """Founder ruling 2026-10-16: "Review: Vexa Call meeting" twice, and Review did nothing. A meeting
    that ended is not a decision: the post-call run's own proposals stay in that call's chat. A review
    item exists only when an agent asks (the desk's short list / an inline card)."""
    wid = _shared(env, "Client X", "u1", u2="contributor")
    row = _room(env, "u1", "pchat-call", wid, "u2")
    env["store"].propose(row["id"], owner="u1", kind="page", body="x", title="Vexa Call meeting",
                         workspace=wid, path="notes/call.md")
    env["store"].propose(row["id"], owner="u1", kind="commitment", body="y", title="Vexa Call meeting")
    out = _att(env)
    assert [n for n in out["needs"] if n["kind"] == "proposal"] == []
    assert "Vexa Call meeting" not in json.dumps(out)


def test_a_mention_opens_the_chat_and_goes_when_you_lose_the_chat(env):
    wid = _shared(env, "Client X", "u2", u1="contributor")
    row = _room(env, "u2", "pchat-plan", wid, "u1", title="Launch plan")
    env["c"].app.state.notifier.emit(["u1"], kind="chat_mentioned", actor="u2", actor_name="Ana",
                                     object_kind="chat", object_id=row["id"], object_name="Launch plan")
    out = _att(env)
    [m] = [n for n in out["needs"] if n["kind"] == "mention"]
    assert m["workspace"] == wid and m["act"] == {"label": "Open", "kind": "open-session", "session": row["id"],
                                                    "notification": m["act"]["notification"]}
    assert m in out["workspaces"][wid]["needs"]
    env["store"].remove_participant(row["id"], "u2", "u1")
    assert [n for n in _att(env)["needs"] if n["kind"] == "mention"] == []


def test_mentions_follow_the_notifications_switch(env, monkeypatch):
    row = _room(env, "u2", "pchat-plan", "", "u1")
    env["c"].app.state.notifier.emit(["u1"], kind="chat_mentioned", actor="u2", actor_name="Ana",
                                     object_kind="chat", object_id=row["id"], object_name="Plan")
    assert [n for n in _att(env)["needs"] if n["kind"] == "mention"]
    monkeypatch.setenv("VEXA_FEATURES", "all,home2,-notifications")
    assert [n for n in _att(env)["needs"] if n["kind"] == "mention"] == []


def _card(cid, waiting, ws=(), title="Renewal", members=("u1",)):
    return {"id": cid, "title": title, "state": "Proposal", "next": "Send terms", "waiting": waiting, "due": None,
            "workspaces": list(ws), "members": [{"subject": m, "status": "member"} for m in members],
            "extra_field": "ignored"}


def test_a_card_waiting_on_you_and_never_one_from_a_foreign_workspace(env):
    wid = _shared(env, "Client X", "u2", u1="contributor")
    theirs = _shared(env, "Theirs", "u2")
    cards = [_card("c1", "u1@example.test", [wid]), _card("c2", "u1", [theirs], title="Secret deal"),
             _card("c3", "the client", [wid], title="Not you")]
    out = _att(env, cards=cards)
    cs = [n for n in out["needs"] if n["kind"] == "card"]
    assert _ids(cs) == ["card:c1"] and cs[0]["workspace"] == wid
    blob = json.dumps(out)
    assert "Secret deal" not in blob and theirs not in blob


def test_an_upcoming_call_today_is_a_need_where_it_is_bound(env):
    wid = _shared(env, "Client X", "u1")
    soon = time.time() + 3 * 3600
    up = {**_meeting(601, status="scheduled", title="Kickoff", workspace_id=wid, scheduled_at=_iso(soon)),
          "start_time": None, "end_time": None}
    far = {**_meeting(602, status="scheduled", title="Later", scheduled_at=_iso(time.time() + 3 * 86400)),
           "start_time": None, "end_time": None}
    _Meetings.by_subject = {"u1": [up, far], "u2": [{**_meeting(603, status="scheduled", title="Board prep",
                                                                scheduled_at=_iso(soon)), "start_time": None}]}
    out = _att(env)
    ups = [n for n in out["needs"] if n["kind"] == "upcoming"]
    assert _ids(ups) == ["upcoming:601"] and ups[0]["workspace"] == wid and "Kickoff" in ups[0]["text"]
    assert ups[0]["act"]["meeting"] == "601"
    assert "Board prep" not in json.dumps(out)


def test_a_failed_routine_of_yours_is_a_retry_and_anothers_never_shows(env):
    wid = _shared(env, "Client X", "u1")
    env["sess"].set_target("u1", "pchat-mb", wid)
    _Flows.by_subject = {"u1": [_flow("brief", thread="pchat-mb",
                                      last={"id": "rx-1", "status": "failed", "reason": "no access",
                                            "ended_at": time.time() - 60}),
                                _flow("theirs", scope="user:u2", last={"status": "failed"})]}
    out = _att(env)
    fs = [n for n in out["needs"] if n["kind"] == "failed"]
    assert _ids(fs) == ["failed:flow:brief"] and fs[0]["workspace"] == wid
    assert fs[0]["act"] == {"label": "Retry", "kind": "retry-routine", "routine": "flow:brief", "reaction": "rx-1"}
    assert "flow:theirs" not in json.dumps(out)
    work = out["workspaces"][wid]["working"]
    assert work and work[0]["kind"] == "routine" and work[0]["state"] == "failed"


# ── working here ───────────────────────────────────────────────────────────────────────────────

def test_a_live_call_and_an_active_room_are_working_here(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    row = _room(env, "u1", "pchat-call", wid, "u2", title="Weekly sync")
    env["store"].bind_call(row["id"], room="call-a1", meeting_id=41, owner="u1", share_token="41.x")
    _Rooms.rooms["call-a1"] = {"name": "call-a1", "num_participants": 2, "metadata": {}}
    w = _att(env)["workspaces"][wid]
    assert w["live_call"]["meeting"] == "41" and w["rooms_active"] == 1
    kinds = {x["kind"] for x in w["working"]}
    assert {"call", "room"} <= kinds
    call = next(x for x in w["working"] if x["kind"] == "call")
    assert call["state"] == "live" and call["ref"] == "meeting:41"


# ── one workspace (the workspace page) ─────────────────────────────────────────────────────────

def test_one_workspace_answers_its_key_with_richer_lists(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    room = _room(env, "u1", "pchat-room", wid, "u2", title="Room talk")
    env["sess"].upsert("u1", "pchat-own", title="My notes")
    env["sess"].set_target("u1", "pchat-own", wid)
    env["sess"].upsert("u1", "pchat-elsewhere", title="Elsewhere")
    _Flows.by_subject = {"u1": [_flow("brief", thread="pchat-own", last={"status": "done", "ended_at": time.time() - 60})]}
    _Meetings.by_subject = {"u1": [_meeting(701, title="Review", workspace_id=wid)]}
    out = _att(env, workspace=wid)
    assert list(out["workspaces"]) == [wid]
    w = out["workspaces"][wid]
    assert [r["session"] for r in w["rooms"]] == ["pchat-room"] and w["rooms"][0]["members"] == 2   # own: by name
    assert room["name"] == "pchat-room"
    assert [c["session"] for c in w["chats"]] == ["pchat-own"] and w["chats"][0]["name"] == "My notes"
    assert [r["ref"] for r in w["routines"]] == ["agent:flow:brief"] and w["routines"][0]["runs"]
    # its row opens the routine's own chat (founder 2026-10-16)
    assert w["routines"][0]["session"] == "pchat-own"
    assert [c["meeting"] for c in w["calls"]] == ["701"] and w["calls"][0]["state"] == "ended"
    assert all(n["workspace"] == wid for n in out["needs"])


def test_one_foreign_or_unknown_workspace_is_an_empty_answer(env):
    theirs = _shared(env, "Theirs", "u2")
    for slug in (theirs, "nope"):
        out = _att(env, workspace=slug)
        assert out["workspaces"] == {} and out["needs"] == []
        assert "Theirs" not in json.dumps(out)


# ── refs ───────────────────────────────────────────────────────────────────────────────────────

def _invisible(out, ref, *secrets):
    kind = ref.split(":", 1)[0] if ":" in ref else ref
    assert out[ref] == {"kind": out[ref]["kind"], "visible": False}, ref
    assert set(out[ref]) == {"kind", "visible"}
    blob = json.dumps(out[ref])
    for s in secrets:
        assert s not in blob
    return kind


def test_visible_refs_resolve(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    room = _room(env, "u1", "pchat-room", wid, "u2", title="Room talk")
    env["sess"].upsert("u1", "pchat-own", title="My notes")
    (env["root"] / "u1" / "notes").mkdir()
    (env["root"] / "u1" / "notes" / "x.md").write_text("---\ntitle: Pricing notes\n---\n")
    (env["root"] / wid / "plan.md").write_text("# Launch plan\n")
    _Meetings.by_subject = {"u1": [_meeting(801, title="Review")]}
    _Flows.by_subject = {"u1": [_flow("brief", last={"status": "done", "ended_at": time.time() - 60})]}
    out = _refs(env, [f"workspace:{wid}", "workspace:personal", f"room:{room['id']}", "chat:pchat-own",
                      "call:801", "file:notes/x.md", f"file:{wid}/plan.md", "person:u2", "person:u1",
                      "routine:flow:brief", "card:c1"], workspace="personal",
                cards=[_card("c1", "u1", [wid])])
    assert out[f"workspace:{wid}"] == {"kind": "workspace", "visible": True, "name": "Client X", "slug": wid,
                                       "members": 2}
    assert out["workspace:personal"]["visible"] and out["workspace:personal"]["slug"] == "personal"
    r = out[f"room:{room['id']}"]
    assert r["visible"] and r["name"] == "Room talk" and r["session"] == "pchat-room" and r["members"] == 2
    assert out["chat:pchat-own"]["name"] == "My notes" and out["chat:pchat-own"]["session"] == "pchat-own"
    c = out["call:801"]
    assert c["visible"] and c["name"] == "Review" and c["meeting"] == "801" and c["state"] == "ended"
    assert out["file:notes/x.md"] == {"kind": "file", "visible": True, "name": "Pricing notes",
                                      "path": "notes/x.md", "slug": "personal"}
    assert out[f"file:{wid}/plan.md"]["name"] == "Launch plan" and out[f"file:{wid}/plan.md"]["slug"] == wid
    p = out["person:u2"]
    assert p["visible"] and p["name"] and p["initials"] and "Client X" in p["sub"]
    assert out["person:u1"]["visible"]
    rt = out["routine:flow:brief"]
    assert rt["visible"] and rt["name"] == "Brief" and rt["state"] == "ok" and rt["runs"]
    assert out["card:c1"]["visible"] and out["card:c1"]["name"] == "Renewal"


def test_every_kind_has_one_invisible_shape(env):
    theirs = _shared(env, "Hidden Co", "u2", u3="contributor")
    (env["root"] / theirs / "secret.md").write_text("# Merger memo\n")
    their_room = _room(env, "u2", "pchat-their", theirs, "u3", title="Merger room")
    env["sess"].upsert("u2", "pchat-private", title="Private thoughts")
    _Meetings.by_subject = {"u2": [_meeting(901, title="Board prep")]}
    _Flows.by_subject = {"u2": [_flow("payroll", scope="user:u2")]}
    refs = [f"workspace:{theirs}", f"room:{their_room['id']}", f"chat:{their_room['id']}", "chat:pchat-private",
            "call:901", f"file:{theirs}/secret.md", "file:secret.md", "person:u3", "person:nobody@example.test",
            "routine:flow:payroll", "card:c9", "card:c2", "weird:x", "nonsense", "room:", "file:_system/x.md"]
    out = _refs(env, refs, workspace=theirs, cards=[_card("c2", "u1", [theirs], title="Secret deal")])
    secrets = ("Hidden Co", "Merger", "Private thoughts", "Board prep", "payroll", "Secret deal", "u3")
    for ref in refs:
        _invisible(out, ref, *secrets)
    blob = json.dumps(list(out.values()))       # the keys are the caller's own refs, echoed
    for s in secrets:
        assert s not in blob


def test_a_room_you_left_turns_invisible(env):
    row = _room(env, "u2", "pchat-plan", "", "u1", title="Plan")
    assert _refs(env, [f"room:{row['id']}"])[f"room:{row['id']}"]["visible"]
    env["store"].remove_participant(row["id"], "u2", "u1")
    assert _refs(env, [f"room:{row['id']}"])[f"room:{row['id']}"] == {"kind": "room", "visible": False}


# ── plain code ─────────────────────────────────────────────────────────────────────────────────

def test_no_model_is_called_nothing_is_stored_and_it_is_deterministic(env, monkeypatch):
    for name in dir(home_model):
        if not name.startswith("_") and callable(getattr(home_model, name)):
            monkeypatch.setattr(home_model, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("model")))
    _shared(env, "Client X", "u1", u2="contributor")
    before = sorted(p.name for p in (env["root"] / "u1").rglob("*") if ".git" not in p.parts)
    a, b = _att(env), _att(env)
    a.pop("generated_at"), b.pop("generated_at")
    assert a == b and _Runtime.spawned == []
    after = sorted(p.name for p in (env["root"] / "u1").rglob("*") if ".git" not in p.parts)
    assert before == after


# ── the gaps closed: a routine's run history, a room's unread and closed ──────────────────────

class _FlowsWithRuns(_Flows):
    """flows-api's `GET /flows/runs` (the person's own runs, newest first) beside their routines."""
    runs_by_subject: dict = {}

    def runs(self, subject, limit=100):
        return list(self.runs_by_subject.get(subject, []))[:limit]


def test_a_routine_carries_its_run_history_newest_first_and_only_its_own(env, monkeypatch):
    wid = _shared(env, "Client X", "u1")
    env["sess"].upsert("u1", "pchat-own", title="My notes")
    env["sess"].set_target("u1", "pchat-own", wid)
    now = time.time()
    _Flows.by_subject = {"u1": [_flow("brief", thread="pchat-own", last={"status": "done", "ended_at": now - 60})]}
    monkeypatch.setattr(routine_flows, "FlowsClient", _FlowsWithRuns)
    _FlowsWithRuns.runs_by_subject = {"u1": [
        {"flow": "brief", "status": "done", "started_at": now - 60, "updated_at": now - 50},
        {"flow": "other", "status": "failed", "started_at": now - 90, "updated_at": now - 85},
        {"flow": "brief", "status": "failed", "reason": "mail timed out", "started_at": now - 86400, "updated_at": now - 86300},
        {"flow": "brief", "status": "done", "started_at": now - 2 * 86400, "updated_at": now - 2 * 86400 + 5},
    ]}
    w = _att(env, workspace=wid)["workspaces"][wid]
    runs = w["routines"][0]["runs"]
    assert [r["ok"] for r in runs] == [True, False, True]
    assert runs[0]["at"] >= runs[1]["at"] >= runs[2]["at"]
    assert "mail timed out" in runs[1]["what"]
    refs = _refs(env, ["routine:brief"])["routine:brief"]
    assert [r["ok"] for r in refs["runs"]] == [True, False, True]


def test_without_a_run_history_the_last_run_still_shows(env):
    wid = _shared(env, "Client X", "u1")
    env["sess"].upsert("u1", "pchat-own", title="My notes")
    env["sess"].set_target("u1", "pchat-own", wid)
    _Flows.by_subject = {"u1": [_flow("brief", thread="pchat-own", last={"status": "done", "ended_at": time.time() - 60})]}
    w = _att(env, workspace=wid)["workspaces"][wid]
    assert len(w["routines"][0]["runs"]) == 1


def test_a_room_counts_what_others_wrote_since_you_last_read_it(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    room = _room(env, "u1", "pchat-room", wid, "u2", title="Room talk")
    env["store"].mark_read(room["id"], "u1")
    env["store"].add_note(room["id"], "u2", "first")
    env["store"].add_note(room["id"], "u2", "second")
    env["store"].add_note(room["id"], "u1", "mine never counts")
    w = _att(env, workspace=wid)["workspaces"][wid]
    assert w["rooms"][0]["unread"] == 2
    assert _refs(env, ["room:pchat-room"])["room:pchat-room"]["unread"] == 2
    # reading the room (the person's own app reads its messages) clears it
    r = env["c"].get("/api/sessions/pchat-room/messages", headers=H("u1"))
    assert r.status_code == 200, r.text
    assert _att(env, workspace=wid)["workspaces"][wid]["rooms"][0]["unread"] == 0


def test_a_room_never_read_counts_since_you_joined_it(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    room = _room(env, "u1", "pchat-room", wid, "u2", title="Room talk")
    env["store"].add_note(room["id"], "u2", "hello")
    assert _att(env, workspace=wid)["workspaces"][wid]["rooms"][0]["unread"] == 1


def test_an_agent_reading_the_room_does_not_mark_it_read(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    room = _room(env, "u1", "pchat-room", wid, "u2", title="Room talk")
    env["store"].add_note(room["id"], "u2", "hello")
    env["c"].get("/api/sessions/pchat-room/messages", headers={**H("u1"), **DELEGATED})
    assert _att(env, workspace=wid)["workspaces"][wid]["rooms"][0]["unread"] == 1


def test_a_closed_room_says_so_when_its_row_does(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    room = _room(env, "u1", "pchat-room", wid, "u2", title="Room talk")
    out = _refs(env, ["room:pchat-room"])["room:pchat-room"]
    assert out["closed"] is False                     # no room record yet: open
    env["store"].put_room(room["id"], visibility="closed", workspace=wid)
    assert _refs(env, ["room:pchat-room"])["room:pchat-room"]["closed"] is True
    assert _att(env, workspace=wid)["workspaces"][wid]["rooms"][0]["closed"] is True
    env["store"].put_room(room["id"], visibility="open")
    assert _att(env, workspace=wid)["workspaces"][wid]["rooms"][0]["closed"] is False


def test_a_new_workspaces_proposed_image_is_a_need_on_its_card_for_the_owner(env):
    from control_plane import workspace_icon as wi
    wid = _shared(env, "Kestrel pilot", "u1", u2="contributor")
    mark = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24" fill="#345"/></svg>'
    wi.propose(env["root"] / wid, mark.encode(), "svg", by="u1", source="designed")
    mine = _att(env)["workspaces"][wid]["needs"]
    assert [n["act"]["source"] for n in mine] == ["identity"] and mine[0]["act"]["slug"] == wid
    assert _att(env, who="u2")["workspaces"][wid]["needs"] == []      # a contributor answers nothing
