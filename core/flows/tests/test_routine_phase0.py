"""Flows phase 0 (founder 2026-10-14: "we want them to work as we discussed").

The blocker it answers: a one-shot routine made in chat to send a bot at 12:13 fired, its
`request_meeting_bot` and its self-retire were refused `human_session_required`, the run reported
`done` in two seconds, nothing was posted to the chat, and the person saw no bot.

  a) a routine carries an explicit, user-visible grant of what it may do unattended, recorded on its
     version, capped by `_global/POLICIES.md`, revocable — and a routine may retire itself only with it;
  b) a run whose acts were refused, or that did not do what it was granted to do, ends `failed` with
     the reason and one calm line in its chat — never `done`;
  c) a one-shot schedule (a time, not a cron) retires after it fires;
  d) `request_meeting_bot` is a step of its own — a bot at a time with no agent run at all.

Offline: flows-api on the sqlite double with identity stubbed at its one hop; the engine with a fake
clock; agent-api and meeting-api replaced at the one HTTP seam each step uses.
"""
from __future__ import annotations

import calendar
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from flows import FakeClock, Registry, schedule, tick  # noqa: E402
from flows.registry import SCHEDULE_EVENT  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+pg8000://phase0:unreachable@127.0.0.1:1/flows"}
_saved = {k: os.environ.get(k) for k in _ENV}
os.environ.update(_ENV)
try:
    from flows_integrations import flows_api as fa  # noqa: E402
    from flows_integrations import subject_auth  # noqa: E402
finally:
    for _k, _v in _saved.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

if not isinstance(fa.db, SqliteDB):
    fa.db = SqliteDB()

from flows_steps import grants as grants_mod  # noqa: E402


def _pol():
    """`flows_steps.policies` as the code under test will import it NOW — a sibling suite drops the
    `flows_steps` modules from sys.modules, so a module object bound at import time can be stale."""
    import importlib
    return importlib.import_module("flows_steps.policies")

T = float(calendar.timegm((2027, 3, 1, 12, 10, 0)))       # 12:10:00 UTC
AT = T + 180                                             # 12:13:00 UTC
BOT = {"name": "bot-at-1213", "trigger": {"kind": "at", "at": AT}, "steps": ["agent_run"],
       "params": {"instruction": "send a bot to https://meet.google.com/abc-defg-hij",
                  "grants": ["bot.request"]}}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for t in ("flow_version", "effect_receipt", "reaction"):
        fa.db.execute(f"DELETE FROM {t}")
    pol = _pol()
    monkeypatch.setattr(pol, "read", lambda uid: pol.resolve(None))
    monkeypatch.setattr(fa.clock, "now", lambda: T)
    routine = {"regime": "autonomous", "workspaces": ["126"], "grants": ["flow.retire_self"],
               "flow": "user:126/bot-at-1213"}
    table = {
        "anna-key": (200, {"user_id": 126, "email": "anna@vexa.test"}),
        "root-key": (200, {"user_id": 1, "email": "root@vexa.test", "admin_account": True}),
        "vxd_anna_chat": (200, {"user_id": 126, "email": "anna@vexa.test",
                                "delegation": {"regime": "human", "workspaces": "*"}}),
        "vxd_auto": (200, {"user_id": 126, "email": "anna@vexa.test",
                           "delegation": {"regime": "autonomous", "workspaces": ["126"]}}),
        "vxd_routine": (200, {"user_id": 126, "email": "anna@vexa.test", "delegation": routine}),
    }
    monkeypatch.setattr(subject_auth, "_validate", lambda base, token, secret: table.get(token, (401, {})))
    yield


@pytest.fixture
def client():
    return TestClient(fa.app, raise_server_exceptions=False)


def as_(token):
    return {"Authorization": f"Bearer {token}"}


# ── a) the grant: explicit, shown, capped, revocable ─────────────────────────────────────────────

def test_the_vocabulary_is_delegation_v1s():
    root = Path(__file__).resolve().parents[3]
    src = (root / "core/identity/contracts/delegation.v1/delegation.py").read_text()
    line = next(x for x in src.splitlines() if x.startswith("UNATTENDED_GRANTS = "))
    assert line == "UNATTENDED_GRANTS = (" + ", ".join(f'"{g}"' for g in grants_mod.VOCAB) + ")"


def test_a_routine_made_in_chat_records_its_grant_and_says_it_in_words(client):
    r = client.post("/flows", json=BOT, headers=as_("vxd_anna_chat"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["grants"] == ["bot.request"]
    assert body["may"] == "This routine may: send bots to meetings."
    card = next(f for f in client.get("/flows", headers=as_("anna-key")).json()["flows"]
                if f["name"] == "bot-at-1213")
    assert card["grants"] == ["bot.request"] and card["may"].startswith("This routine may")


def test_a_grant_outside_the_vocabulary_is_refused_by_name(client):
    bad = {**BOT, "params": {**BOT["params"], "grants": ["bot.request", "mail.send"]}}
    r = client.post("/flows", json=bad, headers=as_("anna-key"))
    assert r.status_code == 400 and r.json()["detail"]["refused"] == "grant_unknown"
    assert r.json()["detail"]["unknown"] == ["mail.send"]


def test_the_company_policy_caps_what_a_routine_may_be_granted(client, monkeypatch):
    raw = "---\nroutine_unattended_actions: none\n---\n"
    pol = _pol()
    monkeypatch.setattr(pol, "read", lambda uid: pol.resolve(raw))
    r = client.post("/flows", json=BOT, headers=as_("anna-key"))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "grant_not_allowed"


def test_a_routine_running_unattended_cannot_author_a_grant(client):
    r = client.post("/flows", json=BOT, headers=as_("vxd_auto"))
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "human_session_required"


def test_a_routine_granted_retire_self_may_retire_itself_and_nothing_else(client):
    client.post("/flows", json=BOT, headers=as_("anna-key"))
    client.post("/flows", json={**BOT, "name": "other"}, headers=as_("anna-key"))
    r = client.post("/flows/other/1/retire", headers=as_("vxd_routine"))
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "human_session_required"
    r = client.post("/flows/bot-at-1213/1/activate", headers=as_("vxd_routine"))
    assert r.status_code == 403
    r = client.post("/flows/bot-at-1213/1/retire", headers=as_("vxd_routine"))
    assert r.status_code == 200, r.text
    r = client.post("/flows/bot-at-1213/1/retire", headers=as_("vxd_auto"))
    assert r.status_code == 403, "without the grant an unwatched run still cannot retire it"


def test_the_person_revokes_the_grant(client):
    client.post("/flows", json=BOT, headers=as_("anna-key"))
    r = client.post("/flows/bot-at-1213/1/revoke_grants", headers=as_("vxd_routine"))
    assert r.status_code == 403, "a routine cannot touch its own grant"
    r = client.post("/flows/bot-at-1213/1/revoke_grants", headers=as_("anna-key"))
    assert r.status_code == 200 and r.json()["grants"] == []
    params = json.loads(fa.db.execute("SELECT params FROM flow_version")[0][0])
    assert "grants" not in params


# ── c) a time, not a cron ─────────────────────────────────────────────────────────────────────────

def test_an_at_trigger_is_a_one_shot_schedule(client):
    r = client.post("/flows", json=BOT, headers=as_("anna-key"))
    assert r.status_code == 201, r.text
    assert r.json()["trigger"] == {"kind": "at", "at": AT}
    cron, params = fa.db.execute("SELECT cron, params FROM flow_version")[0]
    assert cron == "13 12 1 3 *" and json.loads(params)["once"] is True
    iso = {**BOT, "name": "iso", "trigger": {"kind": "at", "at": "2027-03-01T13:13:00+01:00"}}
    assert client.post("/flows", json=iso, headers=as_("anna-key")).json()["trigger"]["at"] == AT


def test_an_at_in_the_past_is_refused(client):
    r = client.post("/flows", json={**BOT, "trigger": {"kind": "at", "at": T - 120}},
                    headers=as_("anna-key"))
    assert r.status_code == 400 and r.json()["detail"]["refused"] == "at_in_the_past"


def _engine_row(db, *, name="bot-at-1213", steps=("note",), params=None, cron="13 12 1 3 *",
                created_at=T):
    db.execute("INSERT INTO flow_version (scope, name, version, on_event, cron, steps, params, "
               "status, created_by, created_at) VALUES ('user:126', :n, 1, :e, :c, :s, :p, "
               "'active', 't', :t)", {"n": name, "e": SCHEDULE_EVENT, "c": cron,
                                      "s": json.dumps(list(steps)), "p": json.dumps(params or {}),
                                      "t": created_at})


def test_a_one_shot_fires_once_and_retires():
    from flows import Done
    db, clock, reg = SqliteDB(), FakeClock(AT + 5), Registry()
    ran = []

    @reg.step
    def note(ctx):
        ran.append(ctx.flow.ident)
        return Done({})
    _engine_row(db, params={"once": True, "at": AT})
    reg.refresh_from_db(db)
    assert schedule.fire_due(db, reg, clock) == 1
    while tick(db, reg, clock):
        pass
    assert ran == ["user:126/bot-at-1213"]
    assert db.execute("SELECT status FROM flow_version")[0][0] == "retired"
    reg.refresh_from_db(db)
    clock.advance(366 * 86400)
    assert schedule.fire_due(db, reg, clock) == 0


def test_a_one_shot_made_inside_its_own_minute_still_fires():
    db, clock, reg = SqliteDB(), FakeClock(AT + 20), Registry()

    @reg.step
    def note(ctx):
        from flows import Done
        return Done({})
    _engine_row(db, params={"once": True, "at": AT}, created_at=AT + 10)
    reg.refresh_from_db(db)
    assert schedule.fire_due(db, reg, clock) == 1


# ── b) an honest outcome ──────────────────────────────────────────────────────────────────────────

class _AgentApi:
    def __init__(self, outcome):
        self.outcome = outcome
        self.posts, self.notices = [], []

    def __call__(self, method, url, headers, body=None, timeout=20):
        if method == "GET" and "/api/chat/pending" in url:
            return 200, {"pending": [], "busy": False, "routine_cursor": "17-0"}
        if method == "GET" and "/api/chat/turn-outcome" in url:
            # the worker names its own turns (`t0`): the run is found by the cursor read before posting
            assert "after=17-0" in url, url
            return 200, self.outcome
        if method == "POST" and url.endswith("/api/chat/submit"):
            if headers.get("X-Vexa-Flow-Step") == "notice":
                self.notices.append(body["prompt"])
            else:
                self.posts.append({"headers": dict(headers), "body": dict(body)})
            return 200, {"ok": True, "unit": "u"}
        raise AssertionError(f"unexpected call {method} {url}")


def _run_routine(monkeypatch, outcome, params=None, rules=None):
    from flows_steps import common, routine
    api = _AgentApi(outcome)
    monkeypatch.setattr(routine, "http", api)
    monkeypatch.setattr(common, "AGENT_API", "http://agent-api.test")
    if rules is not None:
        pol = _pol()
        monkeypatch.setattr(pol, "read", lambda uid: pol.resolve(rules))
    db, clock, reg = SqliteDB(), FakeClock(AT + 5), Registry()
    routine.register(reg)
    _engine_row(db, steps=("agent_run",), params=params or {
        "instruction": "send the bot", "grants": ["bot.request"], "once": True, "at": AT,
        "thread": "pchat-abc"})
    reg.refresh_from_db(db)
    schedule.fire_due(db, reg, clock)
    for _ in range(6):
        while tick(db, reg, clock):
            pass
        clock.advance(20)
    status, reason = db.execute("SELECT status, reason FROM reaction WHERE event_type = :e",
                                {"e": SCHEDULE_EVENT})[0]
    return api, status, reason


REFUSED = {"state": "done", "ok": True, "acted": [], "failed": ["request_meeting_bot"],
           "refused": {"tool": "request_meeting_bot", "reason": "human_session_required", "count": 1}}


def test_a_routine_turn_carries_its_grant_capped_by_policy(monkeypatch):
    ok = {"state": "done", "ok": True, "refused": None, "acted": ["request_meeting_bot"], "failed": []}
    api, status, _ = _run_routine(monkeypatch, ok)
    assert api.posts[0]["headers"]["X-Vexa-Flow-Grants"] == "bot.request"
    assert status == "done"
    api, _, _ = _run_routine(monkeypatch, ok, rules="---\nroutine_unattended_actions: none\n---\n")
    assert "X-Vexa-Flow-Grants" not in api.posts[0]["headers"]


def test_a_refused_run_fails_with_the_reason_and_says_so_in_its_chat(monkeypatch):
    api, status, reason = _run_routine(monkeypatch, REFUSED)
    assert status == "failed"
    assert "human_session_required" in reason
    assert len(api.notices) == 1 and api.notices[0].startswith("Couldn't send the bot")


def test_a_one_shot_that_did_not_do_what_it_was_granted_fails(monkeypatch):
    quiet = {"state": "done", "ok": True, "refused": None, "acted": ["current_time"], "failed": []}
    api, status, reason = _run_routine(monkeypatch, quiet)
    assert status == "failed" and "no_action" in reason
    assert len(api.notices) == 1


def test_a_run_waits_for_its_turn_and_an_older_agent_api_keeps_the_old_answer(monkeypatch):
    api, status, _ = _run_routine(monkeypatch, {"state": "running"})
    assert status == "retrying", "a run in progress is not done"
    from flows_steps import routine
    real = _AgentApi({})

    def old(method, url, headers, body=None, timeout=20):
        if "/api/chat/turn-outcome" in url:
            return 404, {"detail": "Not Found"}
        return real(method, url, headers, body, timeout)
    monkeypatch.setattr(routine, "http", old)
    db, clock, reg = SqliteDB(), FakeClock(AT + 5), Registry()
    routine.register(reg)
    _engine_row(db, steps=("agent_run",), params={"instruction": "x", "thread": "pchat-abc"})
    reg.refresh_from_db(db)
    schedule.fire_due(db, reg, clock)
    for _ in range(4):
        while tick(db, reg, clock):
            pass
        clock.advance(20)
    assert db.execute("SELECT status FROM reaction")[0][0] == "done"


# ── d) a bot at a time with no agent run ──────────────────────────────────────────────────────────

class _Meetings:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, body=None, timeout=20):
        self.calls.append((method, url, dict(body or {})))
        return 201, {"id": 97, "native_meeting_id": "abc-defg-hij", "platform": "google_meet",
                     "status": "requested"}


def _bot_flow(monkeypatch, params):
    from flows_steps import common, meeting, routine
    meetings, agent = _Meetings(), _AgentApi({})
    monkeypatch.setattr(meeting, "http", meetings)
    monkeypatch.setattr(meeting, "user_api_key", lambda uid: "k")
    monkeypatch.setattr(routine, "http", agent)
    monkeypatch.setattr(common, "AGENT_API", "http://agent-api.test")
    db, clock, reg = SqliteDB(), FakeClock(AT + 5), Registry()
    meeting.register_user_steps(reg)
    _engine_row(db, steps=("request_meeting_bot",), params=params)
    reg.refresh_from_db(db)
    schedule.fire_due(db, reg, clock)
    while tick(db, reg, clock):
        pass
    status, reason = db.execute("SELECT status, reason FROM reaction")[0]
    return meetings, agent, status, reason


def test_request_meeting_bot_is_a_step_a_person_may_use():
    from flows_defs import production
    reg = Registry()
    production.build(reg, SqliteDB())
    assert "request_meeting_bot" in reg.user_allowed


def test_a_bot_at_a_time_needs_no_agent_and_says_so_in_the_chat(monkeypatch):
    meetings, agent, status, _ = _bot_flow(monkeypatch, {
        "meeting_url": "https://meet.google.com/abc-defg-hij", "grants": ["bot.request"],
        "once": True, "at": AT, "thread": "pchat-abc"})
    assert status == "done"
    assert [c[0] for c in meetings.calls] == ["POST"] and meetings.calls[0][2]["meeting_url"].endswith(
        "abc-defg-hij")
    assert agent.posts == [], "no agent turn"
    assert len(agent.notices) == 1 and "bot" in agent.notices[0].lower()


def test_a_bot_step_without_the_grant_is_refused_and_says_so(monkeypatch):
    meetings, agent, status, reason = _bot_flow(monkeypatch, {
        "meeting_url": "https://meet.google.com/abc-defg-hij", "once": True, "at": AT,
        "thread": "pchat-abc"})
    assert meetings.calls == [] and status == "failed" and "grant" in reason
    assert len(agent.notices) == 1 and agent.notices[0].startswith("Couldn't send the bot")


def test_a_bot_flow_is_saved_only_with_the_grant_and_a_meeting_link(client):
    body = {"name": "bot-only", "trigger": {"kind": "at", "at": AT}, "steps": ["request_meeting_bot"],
            "params": {"meeting_url": "https://meet.google.com/abc-defg-hij"}}
    r = client.post("/flows", json=body, headers=as_("anna-key"))
    assert r.status_code == 400 and r.json()["detail"]["refused"] == "grant_required"
    body["params"]["grants"] = ["bot.request"]
    r = client.post("/flows", json=body, headers=as_("anna-key"))
    assert r.status_code == 201, r.text
    del body["params"]["meeting_url"]
    r = client.post("/flows", json={**body, "name": "no-link"}, headers=as_("anna-key"))
    assert r.status_code == 400 and r.json()["detail"]["refused"] == "meeting_url_required"


# ── ADR-0050: the global account changes a company flow's keyed trigger from chat ─────────────────

def test_the_global_account_submits_a_keyed_company_flow_and_reads_it_back(client):
    body = {"name": "invite_join", "scope": "instance", "steps": ["dispatch_bot"],
            "trigger": {"kind": "relative", "event": "invite.*", "filter": "payload.url exists",
                        "at": "payload.start - 120s", "key": "payload.event_key"}}
    r = client.post("/flows", json=body, headers=as_("root-key"))
    assert r.status_code == 201, r.text
    assert r.json()["trigger"]["at"] == "payload.start - 120s"
    assert client.post("/flows", json=body, headers=as_("anna-key")).status_code == 403
    bad = {**body, "trigger": {**body["trigger"], "at": "payload.start - soon"}}
    assert client.post("/flows", json=bad, headers=as_("root-key")).json()["detail"]["refused"] == "bad_trigger"


# ── ADR-0050: a person designs, tests, enables and connects a sensor from chat ────────────────────

def test_sensor_design_test_enable_connect_over_the_api(client, monkeypatch):
    import importlib
    cal = importlib.import_module("flows_sensors.calendar")
    items = [{"event_id": "a", "etag": "1", "status": "confirmed", "title": "Sync", "self_response": "accepted",
              "start": "2027-03-01T13:15:00+00:00", "video_url": "https://meet.google.com/aaa"},
             {"event_id": "d", "etag": "1", "status": "confirmed", "title": "No", "self_response": "declined",
              "start": "2027-03-01T14:00:00+00:00", "video_url": "https://meet.google.com/ddd"}]
    monkeypatch.setitem(cal.KINDS, "calendar", cal.CalendarSensor(
        call=lambda owner, path, body, session: {"items": items, "cursor": "c1", "resync_required": False}))
    f = "payload.video_url exists and payload.self_response != 'declined'"
    r = client.post("/sensors", json={"name": "video-meetings", "kind": "calendar", "filter": f},
                    headers=as_("vxd_anna_chat"))
    assert r.status_code == 201 and r.json()["status"] == "draft", r.text
    assert client.post("/sensors/video-meetings/enable", headers=as_("anna-key")).json()["detail"][
        "refused"] == "test_first"
    t = client.post("/sensors/video-meetings/test", headers=as_("anna-key")).json()
    assert [w["title"] for w in t["would_emit"]] == ["Sync"] and t["dropped"][0]["title"] == "No"
    assert client.post("/sensors/video-meetings/enable", headers=as_("anna-key")).json()["status"] == "active"
    c = client.post("/sensors/video-meetings/connect", json={"thread": "pchat-abc", "wake": "per_event"},
                    headers=as_("anna-key"))
    assert c.status_code == 200, c.text
    listed = client.get("/sensors", headers=as_("anna-key")).json()
    assert listed["sensors"][0]["tested"] and listed["kinds"][0]["kind"] == "calendar"
    assert client.post("/sensors", json={"name": "x", "kind": "calendar"},
                       headers=as_("vxd_auto")).status_code == 403, "an unattended run makes no sensor"


def test_a_person_cannot_make_or_test_a_shared_mailbox_sensor(client, monkeypatch):
    import importlib
    cal = importlib.import_module("flows_sensors.calendar")
    mb = importlib.import_module("flows_sensors.mailbox")
    opened = []
    monkeypatch.setitem(cal.KINDS, "mailbox", mb.MailboxSensor(
        inbox_factory=lambda: opened.append(1) or (_ for _ in ()).throw(AssertionError("opened")),
        db_factory=lambda: None, known_uid=lambda e: None, rules=lambda: {}))
    fa.db.execute("DELETE FROM sensor")
    r = client.post("/sensors", json={"name": "inbox", "kind": "mailbox"}, headers=as_("anna-key"))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "kind_not_allowed_in_scope", r.text
    fa.db.execute("INSERT INTO sensor (scope, name, kind, interval_s, version, status, mode, "
                  "next_due_at, failures, created_at) VALUES ('user:126', 'inbox', 'mailbox', 60, 1, "
                  "'draft', 'poll', 0, 0, 0)")
    r = client.post("/sensors/inbox/test", headers=as_("anna-key"))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "kind_not_allowed_in_scope", r.text
    r = client.post("/sensors/inbox/update", json={"interval_s": 120}, headers=as_("anna-key"))
    assert r.status_code == 403, r.text
    assert opened == [], "the shared mailbox is never opened for a person's sensor"
    r = client.post("/sensors", json={"name": "info-invites", "kind": "mailbox", "scope": "instance"},
                    headers=as_("root-key"))
    assert r.status_code == 201 and r.json()["kind"] == "mailbox", r.text
    fa.db.execute("DELETE FROM sensor")
