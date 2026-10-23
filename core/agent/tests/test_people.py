"""People (#1783): who to follow up with and who is worth knowing — as agent tools, and as candidates
the new-chat home ranks into its one stream. Organisation-scoped: colleagues come only from the
workspaces the viewer shares; externals only from the viewer's own meetings and queue.

L2: the home and people routers over a sqlite session store, fakes for every other source."""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane import home_sources, people
from control_plane.routers import home as home_router
from control_plane.routers import people as people_router
from control_plane.session_store import SessionStore
from tests._session_fakes import MemBlobs

DAY = 86400.0
SECRET = "s3cret"


class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _meeting(mid, title, end, attendees, status="completed"):
    return {"id": mid, "status": status, "title": title, "platform": "google_meet",
            "start_time": _iso(end - 1800), "end_time": _iso(end), "data": {"attendees": attendees}}


ANN = {"name": "Ann Lee", "email": "ann@corp.example"}
BOB = {"subject": "u_bob", "name": "Bob Stone", "email": "bob@corp.example", "workspaces": ["sales"]}
CY = {"subject": "u_cy", "name": "Cy Park", "email": "cy@corp.example", "workspaces": ["sales", "ops"]}
EXT = {"name": "Erin Vale", "email": "erin@acme.example"}
FAY = {"name": "Fay Moss", "email": "fay@globex.example"}


@pytest.fixture
def env(tmp_path):
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    gdir = tmp_path / "_global"
    gdir.mkdir()
    clock = Clock()
    state = {"meetings": [], "waiting": [], "colleagues": [BOB, CY], "pages": []}

    class Notifier:
        def listing(self, *a, **k):
            return {"notifications": []}

        def send_frame(self, *a):
            pass

    deps = dict(subject_of=lambda r: r.headers["x-user-id"], session_store=store, notifier=Notifier(),
                _schedule_source=lambda uid: state["meetings"], _global_root=lambda: gdir,
                _internal_caller=lambda r: r.headers.get("x-internal-secret") == SECRET, dispatcher=None,
                home_clock=clock, home_ranker=lambda c, s: None, home_queue_source=lambda uid: state["waiting"],
                home_publish=lambda *a: True, home_colleagues=lambda uid: state["colleagues"],
                people_colleagues=lambda uid: state["colleagues"], people_pages=lambda uid: state["pages"],
                people_self_email=lambda uid: ANN["email"])
    app = FastAPI()
    app.include_router(home_router.build(**deps))
    app.include_router(people_router.build(**deps))
    yield {"c": TestClient(app), "store": store, "clock": clock, "state": state}
    home_sources.SOURCES.pop("people", None)
    store.close()


def _tool(env, which, who="u_ann"):
    r = env["c"].get(f"/api/people/{which}", headers={"x-user-id": who})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _dm(env, other="u_bob", note_from=None):
    s = env["store"]
    sid = "dm-0000000000000001"
    s.create("u_ann", session_id=sid, name=sid, title="Direct chat")
    s.add_participant(sid, "u_ann", other)
    if note_from:
        s.add_note(sid, note_from, "Can you send the deck?")
    return sid


# ── follow up ────────────────────────────────────────────────────────────────────────────────

def test_an_unanswered_direct_chat_is_owed_after_four_hours_and_continue_opens_it(env):
    sid = _dm(env, note_from="u_bob")
    env["clock"].t += 2 * 3600
    assert _tool(env, "followups") == []
    env["clock"].t += 3 * 3600
    [it] = _tool(env, "followups")
    assert it["kind"] == "unanswered" and it["person"]["name"] == "Bob Stone"
    assert it["actions"][0] == {"label": "Continue chat", "open": {"chat": sid}}
    assert "send the deck" not in str(it)          # never the message itself
    env["store"].add_note(sid, "u_ann", "Sent.")
    assert _tool(env, "followups") == []


def test_a_meeting_with_no_follow_up_is_owed_and_drafting_needs_an_explicit_go(env):
    now = env["clock"].t
    env["state"]["meetings"] = [_meeting(7, "Pricing review", now - 2 * DAY, [ANN, EXT])]
    [it] = _tool(env, "followups")
    assert it["kind"] == "promised" and it["person"]["name"] == "Erin Vale"   # never the viewer herself
    draft = next(a for a in it["actions"] if a["label"] == "Draft follow-up")
    assert draft["needs_go"] is True and "explicit go" in draft["prompt"]
    env["c"].post("/api/home/item", headers={"x-user-id": "u_ann"}, json={"item": "m-7", "state": "follow_up"})
    assert _tool(env, "followups") == []


def test_a_contact_going_quiet_against_their_own_cadence(env):
    now = env["clock"].t
    env["state"]["meetings"] = [_meeting(i, "Weekly with Acme", now - (30 + 7 * i) * DAY, [ANN, EXT]) for i in range(4)]
    items = _tool(env, "followups")
    cold = [i for i in items if i["kind"] == "cold"]
    assert len(cold) == 1 and "every 7 days" in cold[0]["reason"]
    env["state"]["meetings"] = [_meeting(i, "Weekly with Acme", now - (20 + 7 * i) * DAY, [ANN, EXT]) for i in range(4)]
    env["state"]["meetings"].append(_meeting(9, "Weekly with Acme", now - 20 * DAY - 3 * 3600, [ANN, EXT]))
    assert [i for i in _tool(env, "followups") if i["kind"] == "cold"]   # median gap 7d, 20d > 14d
    env["state"]["meetings"] = [_meeting(i, "Weekly with Acme", now - (5 + 7 * i) * DAY, [ANN, EXT]) for i in range(4)]
    assert not [i for i in _tool(env, "followups") if i["kind"] == "cold"]


def test_the_queue_naming_a_person_is_unanswered_mail(env):
    env["state"]["waiting"] = [{"id": "q1", "flow": "gmail_triage", "say": "Erin asked for the contract",
                                "email": "erin@acme.example", "since": env["clock"].t - DAY}]
    [it] = _tool(env, "followups")
    assert it["kind"] == "unanswered" and it["actions"][0]["needs_go"] is True


def test_done_and_snooze_from_the_home_hold_the_item(env):
    env["state"]["meetings"] = [_meeting(7, "Pricing review", env["clock"].t - 2 * DAY, [ANN, EXT])]
    [it] = _tool(env, "followups")
    env["c"].post("/api/home/feedback", headers={"x-user-id": "u_ann"},
                  json={"card": it["id"], "kind": "waiting", "signal": "dismiss", "reason": "done"})
    assert _tool(env, "followups") == []


# ── discover ─────────────────────────────────────────────────────────────────────────────────

def test_colleagues_never_messaged_only_after_a_real_interaction_and_opt_out_respected(env):
    """Founder, live home: a "Colleague · tester-b · you have not messaged yet" card. A shared
    workspace alone is not a reason to message someone; a meeting or a chat together is."""
    _dm(env, "u_bob")
    assert not [i for i in _tool(env, "discover") if i["kind"] == "colleague"]
    env["state"]["meetings"] = [_meeting(9, "Ops sync", env["clock"].t - DAY, [ANN, CY])]
    items = _tool(env, "discover")
    assert [i["person"]["name"] for i in items if i["kind"] == "colleague"] == ["Cy Park"]
    assert "2 workspaces" in items[0]["evidence"][0]["text"]
    env["state"]["colleagues"] = [BOB, {**CY, "discoverable": False}]
    assert not [i for i in _tool(env, "discover") if i["kind"] == "colleague"]


def test_frequent_external_attendees_without_a_page_are_offered_one(env):
    now = env["clock"].t
    env["state"]["colleagues"] = []
    env["state"]["meetings"] = [_meeting(1, "Acme kickoff", now - 3 * DAY, [ANN, EXT]),
                                _meeting(2, "Acme pilot", now - DAY, [ANN, EXT])]
    [it] = [i for i in _tool(env, "discover") if i["kind"] == "frequent"]
    assert it["actions"][0]["label"] == "Create page" and "erin@acme.example" in it["actions"][0]["prompt"]
    env["state"]["pages"] = [{"path": "kg/entities/person/erin.md", "email": "erin@acme.example", "title": "Erin"}]
    assert not [i for i in _tool(env, "discover") if i["kind"] == "frequent"]


def test_an_introduction_between_two_contacts_who_never_met(env):
    now = env["clock"].t
    env["state"]["colleagues"] = []
    env["state"]["meetings"] = [_meeting(1, "Onboarding automation", now - 3 * DAY, [ANN, EXT]),
                                _meeting(2, "Automation roadmap", now - DAY, [ANN, FAY])]
    [it] = [i for i in _tool(env, "discover") if i["kind"] == "intro"]
    assert set(it["with"]) == {"Erin Vale", "Fay Moss"} and it["actions"][0]["needs_go"] is True
    env["state"]["meetings"].append(_meeting(3, "Automation sync", now - 2 * DAY, [ANN, EXT, FAY]))
    assert not [i for i in _tool(env, "discover") if i["kind"] == "intro"]


# ── the home ─────────────────────────────────────────────────────────────────────────────────

def test_people_items_rank_into_the_one_home_stream(env):
    now = env["clock"].t
    env["state"]["meetings"] = [_meeting(7, "Pricing review", now - 2 * DAY, [ANN, EXT]),
                                _meeting(8, "Ops sync", now - 3 * DAY, [ANN, CY])]
    r = env["c"].post("/api/home/compose", headers={"x-user-id": "u_ann", "x-internal-secret": SECRET},
                      json={"trigger": "home.stale"})
    assert r.status_code == 200, r.text
    view = env["c"].get("/api/home", headers={"x-user-id": "u_ann"}).json()["view"]
    ids = [c["id"] for c in view["cards"]]
    assert "fu-m-7" in ids and "dc-p-u-cy" in ids
    card = next(c for c in view["cards"] if c["id"] == "dc-p-u-cy")
    assert {"type": "action", "label": "Message", "primary": True, "message": "p-u-cy"} in card["blocks"]
    assert view["people"]["p-u-cy"]["name"] == "Cy Park" and view["people"]["p-u-cy"]["vexa"] is True
    assert view["people"]["p-erin-acme-example"]["vexa"] is False


def test_the_candidate_shape_is_the_home_interface():
    d = people.Directory("u_ann", ANN["email"], [BOB])
    p = d.colleague("u_bob")
    it = people._item(id="dc-p-u-bob", person=p, kind="colleague", label="Colleague", reason="r", score=0.5,
                      evidence=[], actions=[{"type": "action", "label": "Message", "message": p["key"]}])
    [c] = people.candidates([it], d, 0.0, prior_scale=0.6)
    assert c["prior"] == 0.3 and c["card"]["id"] == "dc-p-u-bob" and set(c["people_map"]) == {"p-u-bob"}
    assert c["people"] == ["p-u-bob"] and c["source"] == "people:dc-p-u-bob"


def test_everyone_the_viewer_interacted_with_once_each_most_recent_first(env):
    """Founder 2026-10-15: "we need to have all people we have interacted with on the main page." Met,
    chatted with, sharing a workspace — one person per account or address, never a test account, never
    an unidentified speaker, never the viewer."""
    now = env["clock"].t
    env["state"]["colleagues"] = [BOB, CY, {"subject": "u_t", "name": "tester-b", "email": "tester-b@vexa.test", "workspaces": ["sales"]}]
    env["state"]["meetings"] = [
        _meeting(1, "Acme kickoff", now - 3 * DAY, [ANN, EXT, {"name": "Bob Stone", "email": "bob@corp.example"}]),
        {**_meeting(2, "Pilot", now - DAY, [ANN, FAY]), "data": {"attendees": [ANN, FAY], "participants": ["Speaker 2", "Fay Moss", "Ann Lee"]}},
    ]
    env["state"]["pages"] = [{"path": "kg/entities/person/erin.md", "email": "erin@acme.example", "title": "Erin Vale"}]
    _dm(env, "u_cy")
    r = env["c"].get("/api/people/known", headers={"x-user-id": "u_ann"})
    assert r.status_code == 200, r.text
    rows = r.json()["people"]
    names = [p["name"] for p in rows]
    assert names[:2] == ["Cy Park", "Fay Moss"]                          # the most recent touch first (a chat now, a meeting yesterday)
    assert sorted(names) == sorted(set(names))                            # once each
    assert {"Bob Stone", "Erin Vale", "Cy Park"} <= set(names)
    assert not any(n in names for n in ("tester-b", "Speaker 2", "Ann Lee"))
    bob = next(p for p in rows if p["name"] == "Bob Stone")
    assert set(bob["how"]) >= {"met", "workspace"}
    erin = next(p for p in rows if p["name"] == "Erin Vale")
    assert erin["page"] == "kg/entities/person/erin.md" and "page" in erin["how"]


def test_the_at_picker_searches_the_same_people(env):
    """Founder: "all those people should be available as @" — one server set, searched."""
    now = env["clock"].t
    env["state"]["meetings"] = [_meeting(1, "Acme kickoff", now - 3 * DAY, [ANN, EXT])]
    rows = env["c"].get("/api/people/known", params={"q": "acme"}, headers={"x-user-id": "u_ann"}).json()["people"]
    assert [r["name"] for r in rows] == ["Erin Vale"] and rows[0]["vexa"] is False and rows[0]["subject"] is None
    bob = env["c"].get("/api/people/known", params={"q": "bob"}, headers={"x-user-id": "u_ann"}).json()["people"]
    assert bob and bob[0]["vexa"] is True and bob[0]["subject"] == "u_bob"


def test_a_test_account_is_never_suggested():
    for n in ("tester-b", "qa-1", "Demo"):
        assert people.is_test_account(n)
    assert people.is_test_account("Bea", "bea@lab.test") and people.is_test_account("Al", "al@example.com")
    assert not people.is_test_account("Testarossa Lima", "t@corp.example")


# ── people I share a workspace with (UI-1 People list) ─────────────────────────────────────────

def test_peers_are_everyone_sharing_a_workspace_once_each_with_their_roles(env):
    env["state"]["colleagues"] = [
        {"subject": "u_bob", "name": "Bob Lee", "email": "bob@acme.example", "workspaces": ["acme", "garden"], "roles": {"acme": "owner", "garden": "member"}},
        {"subject": "u_cy", "name": "Cy Park", "email": "cy@acme.example", "workspaces": ["acme"]},
        {"subject": "u_nowhere", "name": "No Room", "email": "", "workspaces": []},
    ]
    r = env["c"].get("/api/people/peers", headers={"x-user-id": "u_ann"})
    assert r.status_code == 200, r.text
    rows = r.json()["people"]
    assert [p["subject"] for p in rows] == ["u_bob", "u_cy"]
    assert rows[0]["roles"] == {"acme": "owner", "garden": "member"} and rows[0]["workspaces"] == ["acme", "garden"]


def test_peers_refuse_a_shared_worker_so_a_room_cannot_enumerate_people(env):
    r = env["c"].get("/api/people/peers", headers={"x-user-id": "u_ann", "x-user-regime": "agent", "x-user-delegation-shared": "1"})
    assert r.status_code == 403 and r.json()["reason"] == "shared_conversation"
