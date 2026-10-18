"""THE CONNECTION POLICY IS A FILE THE ADMIN WRITES, ENFORCED HERE, AND THE PERSON MAY ONLY NARROW IT.

  C1  The keys agent-api reads are the keys the seeded `behavior/global/POLICIES.md` carries, with
      the same defaults, and the same ones `flows_steps.policies.DEFAULTS` lists (that table is the
      one list of every key the file may carry; its own test refuses a key it does not know).
  C2  Each key flips behaviour, server-side, before the broker is asked.
  C3  A value agent-api cannot read, or a misspelt key in the family, refuses every action, loudly.
  C4  The person's own setting narrows; a setting wider than the admin's is refused, by key.
  C5  Every READ the policy governs refuses a worker at the default policy, before the broker, and
      is declared `person: false` in routes.v1.json (the policy, not the gate, decides it). Every
      WRITE is declared `person: true`: no policy lets a worker without a person write (ADR-0040),
      and `connection_actions_unattended` may list reads only.
"""
from __future__ import annotations

import ast
import base64
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane import connection_policy as cp
from control_plane import route_policy
from control_plane.routers import connections

REPO = Path(__file__).resolve().parents[3]
SEED = REPO / "behavior" / "global" / "POLICIES.md"
FLOWS_POLICIES = REPO / "core" / "flows" / "src" / "flows_steps" / "policies.py"

SIGNED = "v1.signed-by-the-gateway.for-u1"
HUMAN = {"X-User-Id": "u1", "X-Vexa-Identity": SIGNED}
WORKER = {"X-User-Id": "u1", "X-User-Regime": "autonomous", "X-Vexa-Identity": SIGNED}
READY = [{"id": "a" * 32, "provider": "google_email", "status": "ready", "label": "Gmail"},
         {"id": "b" * 32, "provider": "google_calendar", "status": "ready", "label": ""}]

DRAFT = {"request_id": "req-00000001", "recipient": "ada@example.test", "subject": "s", "body": "b"}
EVENT = {"request_id": "req-00000002", "title": "Review",
         "start": {"date_time": "2026-10-20T10:00:00", "time_zone": "Europe/Lisbon"},
         "end": {"date_time": "2026-10-20T10:30:00", "time_zone": "Europe/Lisbon"}}


# ── C1 · one list of keys, one set of defaults ───────────────────────────────────────────────────

def _flows_defaults() -> dict:
    tree = ast.parse(FLOWS_POLICIES.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "DEFAULTS")
    out = {}
    for k, v in zip(node.value.keys, node.value.values):
        if isinstance(v, ast.Constant):
            out[k.value] = v.value
    return out


def test_the_seed_declares_every_connection_key_at_its_default():
    assert cp.resolve(SEED.read_text()) == cp.resolve(None)
    declared = cp.front_page.front_matter(SEED.read_text())
    assert set(cp.DEFAULTS) <= set(declared)


def test_the_flows_table_lists_the_same_keys_with_the_same_spelling():
    flows = _flows_defaults()
    for key, default in cp.DEFAULTS.items():
        assert key in flows, f"{key} is not in flows_steps.policies.DEFAULTS, so the seed may not carry it"
        assert str(flows[key]).lower() == (("on" if default else "off") if isinstance(default, bool)
                                           else str(default)).lower() or flows[key] == default, key


# ── the app under test ───────────────────────────────────────────────────────────────────────────

@pytest.fixture
def world(tmp_path, monkeypatch):
    glob = tmp_path / "_global"
    glob.mkdir()
    desk = tmp_path / "desk"
    desk.mkdir()
    state = {"prefs": {}, "calls": []}

    def fake(actor, method, path, payload=None, *, identity):
        state["calls"].append((method, path, payload))
        if path == "/api/connections":
            return {"connections": READY}
        if path == "/api/preferences":
            if method == "POST":
                state["prefs"] = payload
            return state["prefs"]
        return {"status": "ok", "sent": payload}

    monkeypatch.setattr(connections, "call_broker", fake)
    app = FastAPI(dependencies=[route_policy.PERSON_GATE])
    app.include_router(connections.build(subject_of=lambda r: r.headers["x-user-id"], wsr=object(),
                                         _global_root=lambda: glob, _read_target=lambda request, slug: desk))
    client = TestClient(app)

    def policy(**keys):
        lines = "".join(f"{k}: {v}\n" for k, v in keys.items())
        (glob / "POLICIES.md").write_text(f"---\nkind: policies\nprofile: default\n{lines}---\n\n# Policies\n")

    def sent():
        writes = [c for c in state["calls"] if c[0] == "POST" and c[1].startswith("/api/connections/")]
        return writes[-1][2] if writes else None

    return {"client": client, "policy": policy, "state": state, "sent": sent, "desk": desk}


def _refused(r, key, setting="deployment"):
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    # an unattended refusal is the shared human_session_required body, naming its remedy, with the key
    reason = "human_session_required" if key == "connection_actions_unattended" else "policy"
    assert detail["reason"] == reason and detail["policy_key"] == key and detail["setting"] == setting
    if reason == "human_session_required":
        assert detail["remedy"] == "ask_in_chat" and detail["tell_your_person"]
    return detail


# ── C2 · each key flips behaviour ────────────────────────────────────────────────────────────────

def test_defaults_serve_a_present_person_and_forward_the_safe_policy(world):
    r = world["client"].post("/api/connections/gmail/draft", headers=HUMAN, json=DRAFT)
    assert r.status_code == 200, r.text
    assert world["sent"]()["policy"] == {"unattended": False, "notify_attendees": False, "attendee_domains": None,
                                         "calendars": ["primary"], "recipient_domains": None, "attachments": False,
                                         "attachment_max_bytes": 5120 * 1024}


def test_a_reply_draft_may_leave_the_subject_out(world):
    """The tool says a reply may leave the subject empty for "Re: <original>"; omitting it answered
    422 "subject: Field required". The broker derives the subject from the thread."""
    reply = {k: v for k, v in DRAFT.items() if k != "subject"} | {"thread_id": "t1"}
    r = world["client"].post("/api/connections/gmail/draft", headers=HUMAN, json=reply)
    assert r.status_code == 200, r.text
    assert world["sent"]()["subject"] == "" and world["sent"]()["thread_id"] == "t1"


def test_a_reply_draft_may_send_an_empty_subject(world):
    """The empty-string spelling of the same reply: forwarded as "" for the broker to derive (#1783)."""
    r = world["client"].post("/api/connections/gmail/draft", headers=HUMAN,
                             json=DRAFT | {"subject": "", "thread_id": "t1"})
    assert r.status_code == 200, r.text
    assert world["sent"]()["subject"] == "" and world["sent"]()["thread_id"] == "t1"


def test_connection_actions_refuses_an_action_it_does_not_list(world):
    world["policy"](connection_actions="gmail.search, calendar.events")
    before = len(world["state"]["calls"])
    _refused(world["client"].post("/api/connections/gmail/draft", headers=HUMAN, json=DRAFT), "connection_actions")
    assert len(world["state"]["calls"]) == before, "refused before the broker"
    assert world["client"].post("/api/connections/gmail/search", headers=HUMAN, json={"query": "x"}).status_code == 200


def test_connection_actions_unattended_lets_a_routine_read_exactly_what_it_lists(world):
    before = len(world["state"]["calls"])
    _refused(world["client"].post("/api/connections/gmail/search", headers=WORKER, json={"query": "x"}),
             "connection_actions_unattended")
    assert len(world["state"]["calls"]) == before
    world["policy"](connection_actions_unattended="gmail.search")
    assert world["client"].post("/api/connections/gmail/search", headers=WORKER, json={"query": "x"}).status_code == 200
    assert world["sent"]()["policy"]["unattended"] is True
    _refused(world["client"].post("/api/connections/calendar/events", headers=WORKER,
                                  json=BODIES["/api/connections/calendar/events"]), "connection_actions_unattended")


@pytest.mark.parametrize("value", ["gmail.draft", "gmail.search, calendar.event.create", "all"])
def test_connection_actions_unattended_never_lists_a_write(world, value):
    """A write needs a person in the loop whatever the file says: a value that names one is
    unreadable, loudly, rather than quietly doing less than it reads (ADR-0040)."""
    world["policy"](connection_actions_unattended=value)
    r = world["client"].post("/api/connections/gmail/search", headers=HUMAN, json={"query": "x"})
    assert r.status_code == 409
    assert r.json()["detail"]["policy_key"] == "connection_actions_unattended"


@pytest.mark.parametrize("key,value,field,want", [
    ("calendar_notify_attendees", "off", "notify_attendees", False),
    ("calendar_attendee_domains", "example.test, @inside.test", "attendee_domains", ["example.test", "inside.test"]),
    ("calendar_ids", "primary, team@group.calendar.google.com", "calendars", ["primary", "team@group.calendar.google.com"]),
    ("calendar_ids", "any", "calendars", None),
])
def test_each_calendar_key_reaches_the_broker(world, key, value, field, want):
    world["policy"](**{key: value})
    assert world["client"].post("/api/connections/calendar/event/create", headers=HUMAN, json=EVENT).status_code == 200
    assert world["sent"]()["policy"][field] == want


def test_draft_recipient_domains_reaches_the_broker(world):
    world["policy"](draft_recipient_domains="example.test")
    world["client"].post("/api/connections/gmail/draft", headers=HUMAN, json=DRAFT)
    assert world["sent"]()["policy"]["recipient_domains"] == ["example.test"]


def test_draft_attachments_off_refuses_and_on_reads_the_workspace_file_within_the_cap(world):
    (world["desk"] / "notes.md").write_bytes(b"# Notes\n" * 10)
    (world["desk"] / "big.pdf").write_bytes(b"x" * 3000)
    with_file = {**DRAFT, "attachments": [{"path": "notes.md"}]}
    detail = _refused(world["client"].post("/api/connections/gmail/draft", headers=HUMAN, json=with_file),
                      "draft_attachments")
    assert "Refused by policy draft_attachments" in detail["instruction"]
    world["policy"](draft_attachments="on", draft_attachment_max_kb="2")
    assert world["client"].post("/api/connections/gmail/draft", headers=HUMAN, json=with_file).status_code == 200
    sent = world["sent"]()
    assert sent["attachments"] == [{"filename": "notes.md", "content_type": "text/markdown",
                                    "data": base64.b64encode(b"# Notes\n" * 10).decode()}]
    assert sent["policy"]["attachments"] is True and sent["policy"]["attachment_max_bytes"] == 2048
    _refused(world["client"].post("/api/connections/gmail/draft", headers=HUMAN,
                                  json={**DRAFT, "attachments": [{"path": "big.pdf"}]}), "draft_attachment_max_kb")
    assert world["client"].post("/api/connections/gmail/draft", headers=HUMAN,
                                json={**DRAFT, "attachments": [{"path": "run.sh"}]}).status_code == 422
    assert world["client"].post("/api/connections/gmail/draft", headers=HUMAN,
                                json={**DRAFT, "attachments": [{"path": "missing.md"}]}).status_code == 404


# ── C3 · loud ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("keys,named", [
    ({"calendar_notify_atendees": "on"}, "calendar_notify_atendees"),
    ({"calendar_notify_attendees": "sometimes"}, "calendar_notify_attendees"),
    ({"connection_actions": "gmail.send"}, "connection_actions"),
    ({"draft_attachment_max_kb": "lots"}, "draft_attachment_max_kb"),
    ({"draft_recipient_domains": "everyone"}, "draft_recipient_domains"),
])
def test_an_unreadable_policy_refuses_every_action_by_key(world, keys, named):
    world["policy"](**keys)
    r = world["client"].post("/api/connections/gmail/search", headers=HUMAN, json={"query": "x"})
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "policy_unreadable" and r.json()["detail"]["policy_key"] == named


def test_the_meeting_keys_do_not_trip_the_connection_reader():
    assert cp.resolve(SEED.read_text())["connection_actions"] == frozenset(cp.ACTIONS)


# ── C4 · the person narrows, never widens ────────────────────────────────────────────────────────

def test_the_persons_setting_narrows(world):
    world["state"]["prefs"] = {"connection_actions": ["gmail.search", "gmail.read"]}
    _refused(world["client"].post("/api/connections/gmail/draft", headers=HUMAN, json=DRAFT),
             "connection_actions", setting="user")
    world["state"]["prefs"] = {"draft_recipient_domains": ["example.test"]}
    world["client"].post("/api/connections/gmail/draft", headers=HUMAN, json=DRAFT)
    assert world["sent"]()["policy"]["recipient_domains"] == ["example.test"]


def test_a_setting_wider_than_the_admins_is_clipped_when_the_admin_narrows_later(world):
    world["state"]["prefs"] = {"draft_attachment_max_kb": 4000, "calendar_ids": "any"}
    world["policy"](draft_attachment_max_kb="100")
    world["client"].post("/api/connections/calendar/event/create", headers=HUMAN, json=EVENT)
    policy = world["sent"]()["policy"]
    assert policy["attachment_max_bytes"] == 100 * 1024 and policy["calendars"] == ["primary"]


@pytest.mark.parametrize("setting,key", [
    ({"draft_attachments": True}, "draft_attachments"),
    ({"connection_actions_unattended": ["gmail.search"]}, "connection_actions_unattended"),
    ({"draft_attachment_max_kb": 9999}, "draft_attachment_max_kb"),
])
def test_widening_is_refused_and_nothing_is_saved(world, setting, key):
    r = world["client"].put("/api/connections/policy", headers=HUMAN, json=setting)
    assert r.status_code == 422 and r.json()["detail"]["policy_key"] == key
    assert world["state"]["prefs"] == {}


def test_notifying_attendees_is_off_by_default_and_the_company_can_turn_it_on(world):
    """An invitation is mail to other people in the person's name: at the default policy an agent's
    calendar create asking Google to email attendees is refused before the broker is asked, and a
    person cannot switch it on for themselves. The company's `_global` turns it on."""
    before = len(world["state"]["calls"])
    _refused(world["client"].post("/api/connections/calendar/event/create", headers=HUMAN,
                                  json={**EVENT, "notify": True}), "calendar_notify_attendees")
    assert not [c for c in world["state"]["calls"][before:] if c[1].endswith("/calendar-event")]
    assert world["client"].post("/api/connections/calendar/event/create", headers=HUMAN,
                                json=EVENT).status_code == 200
    assert world["sent"]()["notify"] is False and world["sent"]()["policy"]["notify_attendees"] is False
    r = world["client"].put("/api/connections/policy", headers=HUMAN, json={"calendar_notify_attendees": True})
    assert r.status_code == 422 and r.json()["detail"]["policy_key"] == "calendar_notify_attendees"
    world["policy"](calendar_notify_attendees="on")
    assert world["client"].post("/api/connections/calendar/event/create", headers=HUMAN,
                                json={**EVENT, "notify": True}).status_code == 200
    assert world["sent"]()["notify"] is True and world["sent"]()["policy"]["notify_attendees"] is True


def test_widening_a_domain_list_the_admin_restricted_is_refused(world):
    world["policy"](calendar_attendee_domains="example.test")
    r = world["client"].put("/api/connections/policy", headers=HUMAN, json={"calendar_attendee_domains": "any"})
    assert r.status_code == 422 and r.json()["detail"]["policy_key"] == "calendar_attendee_domains"
    r = world["client"].put("/api/connections/policy", headers=HUMAN,
                            json={"calendar_attendee_domains": ["other.test"]})
    assert r.status_code == 422


def test_narrowing_is_saved_and_read_back(world):
    r = world["client"].put("/api/connections/policy", headers=HUMAN,
                            json={"connection_actions": ["gmail.search"], "calendar_ids": ["primary"]})
    assert r.status_code == 200 and r.json()["status"] == "saved"
    assert world["state"]["prefs"] == {"connection_actions": ["gmail.search"], "calendar_ids": ["primary"]}
    got = world["client"].get("/api/connections/policy", headers=HUMAN).json()
    assert got["effective"]["connection_actions"] == ["gmail.search"]
    assert got["deployment"]["connection_actions"] == list(cp.ACTIONS)


WATCHED = {"X-User-Id": "u1", "X-User-Regime": "human", "X-Vexa-Identity": SIGNED}


def test_a_watched_worker_reads_the_setting_but_cannot_change_it(world):
    """The setting is the person's own word. A worker in a watched chat may still be steered by text
    it read, so no delegated caller saves it, whatever its regime (ADR-0040 amendment 2026-10-13)."""
    before = len(world["state"]["calls"])
    r = world["client"].put("/api/connections/policy", headers=WATCHED, json={"connection_actions": ["gmail.search"]})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "person_setting", r.text
    assert len(world["state"]["calls"]) == before and world["state"]["prefs"] == {}
    assert world["client"].get("/api/connections/policy", headers=WATCHED).status_code == 200


def test_a_routine_cannot_change_the_persons_setting(world):
    r = world["client"].put("/api/connections/policy", headers=WORKER, json={"connection_actions": []})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "human_session_required"


# ── C5 · the governed routes ─────────────────────────────────────────────────────────────────────

def test_the_governed_routes_are_declared_reads_for_the_policy_writes_for_the_gate():
    doc = json.loads(route_policy.MANIFEST_PATH.read_text())
    rows = {("POST", "/api/" + v["path"][len("/agent/"):]): v["person"] for v in doc["verbs"]}
    for path, action in connections.POLICY_ROUTES.items():
        assert rows.get(("POST", path)) is (action in cp.WRITES), path
    assert {p for _, p in route_policy.PERSON_VERBS} >= {
        p for p, a in connections.POLICY_ROUTES.items() if a in cp.WRITES}


BODIES = {
    "/api/connections/gmail/search": {"query": "x"}, "/api/connections/gmail/inbox": {},
    "/api/connections/gmail/read": {"message_id": "m1"}, "/api/connections/gmail/thread": {"thread_id": "t1"},
    "/api/connections/calendar/events": {"time_min": "2026-10-01T00:00:00Z", "time_max": "2026-10-02T00:00:00Z"},
    "/api/connections/gmail/draft": DRAFT,
    "/api/connections/gmail/draft/update": {**DRAFT, "draft_id": "r-1"},
    "/api/connections/calendar/event/create": EVENT,
    "/api/connections/calendar/event/update": {"request_id": "req-00000003", "event_id": "0a1b2c3d4e", "title": "x"},
    "/api/connections/calendar/event/cancel": {"request_id": "req-00000004", "event_id": "0a1b2c3d4e"},
}


@pytest.mark.parametrize("path", sorted(connections.POLICY_ROUTES))
def test_every_governed_route_refuses_a_worker_at_the_default_before_the_broker(world, path):
    assert set(BODIES) == set(connections.POLICY_ROUTES)
    before = len(world["state"]["calls"])
    r = world["client"].post(path, headers=WORKER, json=BODIES[path])
    if connections.POLICY_ROUTES[path] in cp.WRITES:
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "human_session_required", r.text
    else:
        _refused(r, "connection_actions_unattended")
    assert len(world["state"]["calls"]) == before
    assert world["client"].post(path, headers=HUMAN, json=BODIES[path]).status_code == 200


@pytest.mark.parametrize("path", sorted(p for p, a in connections.POLICY_ROUTES.items() if a in cp.WRITES))
def test_a_write_refuses_a_worker_even_when_the_file_lists_every_read_unattended(world, path):
    world["policy"](connection_actions_unattended=", ".join(cp.READS))
    before = len(world["state"]["calls"])
    r = world["client"].post(path, headers=WORKER, json=BODIES[path])
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "human_session_required", r.text
    assert len(world["state"]["calls"]) == before


def test_the_founders_unattended_gmail_reads_are_honoured(tmp_path):
    """The front matter app.dev's `_global/POLICIES.md` carries (2026-10-13): a background routine
    may search and read Gmail; it still may not read the calendar or write anything."""
    from control_plane import connection_policy as cp
    g = tmp_path / "_global"
    g.mkdir()
    (g / "POLICIES.md").write_text(
        "---\nconnection_actions_unattended: gmail.search, gmail.read, gmail.thread\n---\n# Policies\n")
    eff = cp.read(g)
    for action in ("gmail.search", "gmail.read", "gmail.thread"):
        assert cp.allowed(eff, action, unattended=True) is None, action
    assert cp.allowed(eff, "calendar.events", unattended=True) == "connection_actions_unattended"
    assert cp.allowed(eff, "gmail.draft", unattended=True) is not None
