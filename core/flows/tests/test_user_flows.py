"""ADR-0046 — a person's own flows: schedules, scoped admission, the loop guard, quotas and the
`agent_run` step that posts a routine's turn into its chat thread.

Offline: the engine on the sqlite double, the clock faked, agent-api replaced at the one HTTP seam
the step uses (`routine.http`)."""
from __future__ import annotations

import calendar
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flows import Done, EventType, FakeClock, Registry, admit, tick  # noqa: E402
from flows import admission, schedule  # noqa: E402
from flows.registry import SCHEDULE_EVENT, flow_ident  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

T = float(calendar.timegm((2027, 3, 1, 6, 59, 30)))      # a Monday, 06:59:30 UTC


def _row(db, *, scope="user:42", name="brief", version=1, cron="0 7 * * *", event=None,
         steps=("note",), params=None, status="active", created_at=0.0):
    db.execute(
        "INSERT INTO flow_version (scope, name, version, on_event, cron, steps, params, status, "
        "created_by, created_at) VALUES (:sc,:n,:v,:e,:c,:s,:p,:st,'t',:t)",
        {"sc": scope, "n": name, "v": version, "e": event or SCHEDULE_EVENT,
         "c": None if event else cron, "s": json.dumps(list(steps)),
         "p": json.dumps(params or {}), "st": status, "t": created_at})


def _rig():
    db, clock, reg = SqliteDB(), FakeClock(T), Registry()
    ran: list = []

    @reg.step
    def note(ctx):
        ran.append((ctx.flow.ident, ctx.refs.get("slot"), ctx.refs.get("uid")))
        return Done({})

    @reg.step
    def echo(ctx):
        ran.append((ctx.flow.ident, ctx.refs.get("uid")))
        ctx.emit("thing.happened", f"echo-{ctx.reaction_id}", {"uid": ctx.refs.get("uid")})
        return Done({})

    return db, clock, reg, ran


def _drain(db, reg, clock, emit=None):
    while tick(db, reg, clock, emit=emit):
        pass


# ── the cron ─────────────────────────────────────────────────────────────────────────────────────

def test_cron_reads_five_fields_and_refuses_anything_else():
    c = schedule.parse("*/15 7-9 * * 1-5")
    assert 30 in c.minutes and 8 in c.hours and 6 not in c.weekdays
    for bad in ("", "* * * *", "61 * * * *", "* * * * 8", "a * * * *", "*/0 * * * *"):
        with pytest.raises(schedule.CronError):
            schedule.parse(bad)


def test_the_previous_and_next_slot():
    c = schedule.parse("0 7 * * *")
    assert schedule.previous_slot(c, T) == T + 30 - 86400
    assert schedule.next_slot(c, T) == T + 30


def test_min_interval_is_the_shortest_gap():
    assert schedule.min_interval("*/5 * * * *") == 300
    assert schedule.min_interval("0 7 * * 1-5") == 86400


# ── schedule firing, the catch-up rule ───────────────────────────────────────────────────────────

def test_two_slots_fire_two_runs_in_order_and_a_slot_fires_once():
    db, clock, reg, ran = _rig()
    _row(db)
    reg.refresh_from_db(db)
    clock.advance(31)                                      # 07:00:01
    assert schedule.fire_due(db, reg, clock) == 1
    assert schedule.fire_due(db, reg, clock) == 0          # same slot: the constraint holds
    _drain(db, reg, clock)
    clock.advance(86400)
    assert schedule.fire_due(db, reg, clock) == 1
    _drain(db, reg, clock)
    assert [r[1] for r in ran] == [T + 30, T + 30 + 86400]
    assert all(r[2] == "42" for r in ran), "a person's schedule runs as that person"


def test_after_downtime_only_the_most_recent_slot_fires_and_the_rest_are_counted():
    db, clock, reg, ran = _rig()
    _row(db, cron="0 * * * *")                             # hourly
    reg.refresh_from_db(db)
    clock.advance(31)
    schedule.fire_due(db, reg, clock)
    _drain(db, reg, clock)
    before = schedule.SKIPPED[flow_ident("user:42", "brief")]
    clock.advance(5 * 3600 + 600)                          # down for five hours
    assert schedule.fire_due(db, reg, clock, catchup_s=3600) == 1
    _drain(db, reg, clock)
    assert len(ran) == 2
    assert schedule.SKIPPED[flow_ident("user:42", "brief")] - before == 4


def test_a_missed_slot_older_than_the_window_does_not_fire():
    db, clock, reg, ran = _rig()
    _row(db, cron="0 7 * * *")
    reg.refresh_from_db(db)
    clock.advance(31 + 7200)                               # 09:00 — two hours after the slot
    assert schedule.fire_due(db, reg, clock, catchup_s=3600) == 0


def test_no_slot_before_the_version_existed_fires():
    db, clock, reg, ran = _rig()
    _row(db, cron="0 7 * * *", created_at=T + 60)          # created at 07:00:30, after the slot
    reg.refresh_from_db(db)
    clock.advance(90)
    assert schedule.fire_due(db, reg, clock) == 0


def test_a_retired_schedule_stops_firing_without_a_restart():
    db, clock, reg, ran = _rig()
    _row(db)
    reg.refresh_from_db(db)
    db.execute("UPDATE flow_version SET status='retired'")
    reg.refresh_from_db(db)
    clock.advance(31)
    assert schedule.fire_due(db, reg, clock) == 0


def test_schedule_due_is_never_an_event_anyone_can_publish():
    db, clock, reg, ran = _rig()
    _row(db)
    reg.refresh_from_db(db)
    assert reg.match(SCHEDULE_EVENT) == []


# ── scoped admission ─────────────────────────────────────────────────────────────────────────────

def test_a_persons_event_flow_is_admitted_only_for_their_own_events():
    db, clock, reg, ran = _rig()
    _row(db, scope="user:42", name="on-done", event="meeting.completed")
    _row(db, scope="user:7", name="on-done", event="meeting.completed")
    reg.refresh_from_db(db)
    assert admit(db, reg, clock, source_event_id="m1", event_type="meeting.completed",
                 subject_refs={"uid": "42"}) == 1
    assert admit(db, reg, clock, source_event_id="m2", event_type="meeting.completed",
                 subject_refs={}) == 0
    _drain(db, reg, clock)
    assert ran == [("user:42/on-done", None, "42")]


def test_two_people_and_the_instance_each_run_their_own_flow_of_one_name():
    db, clock, reg, ran = _rig()
    for scope in ("instance", "user:42", "user:7"):
        _row(db, scope=scope, name="same", event="meeting.completed")
    reg.refresh_from_db(db)
    assert admit(db, reg, clock, source_event_id="m1", event_type="meeting.completed",
                 subject_refs={"uid": "7"}) == 2
    _drain(db, reg, clock)
    assert sorted(r[0] for r in ran) == ["same", "user:7/same"]


# ── the loop guard ───────────────────────────────────────────────────────────────────────────────

def test_a_flow_whose_step_emits_its_own_trigger_runs_once():
    db, clock, reg, ran = _rig()
    _row(db, scope="user:42", name="loop", event="thing.happened", steps=("echo",))
    reg.refresh_from_db(db)

    def emit(et, sid, refs):
        return admit(db, reg, clock, source_event_id=sid, event_type=et, subject_refs=refs)

    before = admission.REFUSED["loop_limit"]
    admit(db, reg, clock, source_event_id="t0", event_type="thing.happened",
          subject_refs={"uid": "42"})
    _drain(db, reg, clock, emit=emit)
    assert len(ran) == 1
    assert admission.REFUSED["loop_limit"] - before == 1


def test_a_chain_at_the_hop_limit_starts_nothing():
    db, clock, reg, ran = _rig()
    _row(db, scope="user:42", name="x", event="thing.happened")
    reg.refresh_from_db(db)
    chain = [f"f{i}" for i in range(admission.MAX_HOPS)]
    assert admit(db, reg, clock, source_event_id="deep", event_type="thing.happened",
                 subject_refs={"uid": "42", "_origin": chain}) == 0


# ── quotas at admission ──────────────────────────────────────────────────────────────────────────

def test_runs_over_the_daily_cap_are_not_admitted():
    db, clock, reg, ran = _rig()
    _row(db, scope="user:42", name="x", event="thing.happened")
    reg.refresh_from_db(db)
    made = [admit(db, reg, clock, source_event_id=f"e{i}", event_type="thing.happened",
                  subject_refs={"uid": "42"}, runs_per_day=3) for i in range(5)]
    assert made == [1, 1, 1, 0, 0]
    clock.advance(86401)
    assert admit(db, reg, clock, source_event_id="later", event_type="thing.happened",
                 subject_refs={"uid": "42"}, runs_per_day=3) == 1


# ── agent_run: the routine's thread ──────────────────────────────────────────────────────────────

class _AgentApi:
    """agent-api at the one seam `agent_run` uses: the pending check and the submit."""

    def __init__(self):
        self.posts: list[dict] = []
        self.busy = False
        self.answer = 200

    def __call__(self, method, url, headers, body=None, timeout=20):
        if method == "GET" and "/api/chat/pending" in url:
            return 200, {"pending": [], "busy": self.busy}
        if method == "GET" and "/api/chat/turn-outcome" in url:     # flows phase 0: the run's end
            return 200, {"state": "done", "ok": True, "refused": None, "acted": [], "failed": []}
        if method == "POST" and url.endswith("/api/chat/submit"):
            if self.answer != 200:
                return self.answer, {"detail": {"refused": "thread_busy"}}
            self.posts.append({"headers": dict(headers), "body": dict(body)})
            return 200, {"ok": True, "unit": f"agent-42-chat-{body['session']}-auto"}
        raise AssertionError(f"unexpected call {method} {url}")


@pytest.fixture
def agent_api(monkeypatch):
    from flows_steps import common, routine
    api = _AgentApi()
    monkeypatch.setattr(routine, "http", api)
    monkeypatch.setattr(common, "AGENT_API", "http://agent-api.test")
    return api


def _routine_rig(params=None):
    from flows_steps import routine
    db, clock, reg = SqliteDB(), FakeClock(T), Registry()
    routine.register(reg)
    _row(db, steps=("agent_run",), params=params or {"instruction": "brief me"})
    reg.refresh_from_db(db)
    return db, clock, reg


def test_two_scheduled_runs_land_in_one_thread_in_order(agent_api):
    from flows_steps import routine
    db, clock, reg = _routine_rig()
    clock.advance(31)
    schedule.fire_due(db, reg, clock)
    _drain(db, reg, clock)
    clock.advance(86400)
    schedule.fire_due(db, reg, clock)
    _drain(db, reg, clock)
    sessions = [p["body"]["session"] for p in agent_api.posts]
    assert len(sessions) == 2 and sessions[0] == sessions[1] == routine.thread_session(
        "user:42", "brief")
    for p in agent_api.posts:
        h = p["headers"]
        assert h["X-User-Id"] == "42" and h["X-Vexa-Flow-Regime"] == "autonomous"
        assert h["X-Vexa-Flow-Name"] == "brief" and h["X-Vexa-Flow-Scope"] == "user:42"
        assert h["X-Vexa-Flow-Step"] == "agent_run", "the turn is marked as the flow's"
        assert p["body"]["prompt"].startswith("brief me")


def test_a_busy_thread_holds_the_run_until_it_is_free(agent_api):
    db, clock, reg = _routine_rig()
    clock.advance(31)
    schedule.fire_due(db, reg, clock)
    agent_api.busy = True
    tick(db, reg, clock)
    assert agent_api.posts == []
    status, attempt = db.execute("SELECT status, attempt FROM reaction")[0]
    assert status == "retrying" and attempt == 0, "waiting burns no attempt"
    agent_api.busy = False
    clock.advance(31)
    _drain(db, reg, clock)
    assert len(agent_api.posts) == 1


def test_a_race_at_the_door_is_a_wait_too(agent_api):
    db, clock, reg = _routine_rig()
    clock.advance(31)
    schedule.fire_due(db, reg, clock)
    agent_api.answer = 409
    tick(db, reg, clock)
    assert db.execute("SELECT status FROM reaction")[0][0] == "retrying"


def test_a_routine_reports_into_its_own_chat_by_default(agent_api):
    db, clock, reg = _routine_rig()
    clock.advance(31)
    schedule.fire_due(db, reg, clock)
    _drain(db, reg, clock)
    prompt = agent_api.posts[0]["body"]["prompt"]
    assert "this chat" in prompt and "Do not send" in prompt


def test_only_a_named_connection_is_used_outward(agent_api):
    db, clock, reg = _routine_rig({"instruction": "brief me", "notify": "conn-123"})
    clock.advance(31)
    schedule.fire_due(db, reg, clock)
    _drain(db, reg, clock)
    prompt = agent_api.posts[0]["body"]["prompt"]
    assert "conn-123" in prompt and "only" in prompt


def test_a_paused_routine_ends_the_run(agent_api):
    db, clock, reg = _routine_rig()
    clock.advance(31)
    schedule.fire_due(db, reg, clock)
    agent_api.answer = 423
    _drain(db, reg, clock)
    assert db.execute("SELECT status FROM reaction")[0][0] not in ("retrying", "pending", "done")


def test_thread_new_gives_each_run_its_own_thread(agent_api):
    db, clock, reg = _routine_rig({"instruction": "x", "thread": "new"})
    clock.advance(31)
    schedule.fire_due(db, reg, clock)
    _drain(db, reg, clock)
    clock.advance(86400)
    schedule.fire_due(db, reg, clock)
    _drain(db, reg, clock)
    a, b = (p["body"]["session"] for p in agent_api.posts)
    assert a != b


def test_agent_run_and_the_sink_card_are_the_steps_the_image_marks_for_people():
    from flows_defs import production
    reg = Registry()
    production.build(reg, SqliteDB())
    assert reg.user_allowed == {"agent_run", "post_event_card", "request_meeting_bot", "post_message"}


def test_a_routine_runs_in_the_chat_it_was_created_in(agent_api):
    """Founder, 2026-10-13: a routine runs IN THE CHAT WHERE IT IS CREATED — `params.thread`."""
    db, clock, reg = _routine_rig({"instruction": "brief me", "thread": "pchat-mv6j378b"})
    clock.advance(31)
    schedule.fire_due(db, reg, clock)
    _drain(db, reg, clock)
    clock.advance(86400)
    schedule.fire_due(db, reg, clock)
    _drain(db, reg, clock)
    assert [p["body"]["session"] for p in agent_api.posts] == ["pchat-mv6j378b"] * 2


def test_a_thread_that_is_no_chat_id_falls_back_to_the_routines_own(agent_api):
    from flows_steps import routine
    db, clock, reg = _routine_rig({"instruction": "x", "thread": "../etc/passwd"})
    clock.advance(31)
    schedule.fire_due(db, reg, clock)
    _drain(db, reg, clock)
    assert agent_api.posts[0]["body"]["session"] == routine.thread_session("user:42", "brief")


# ── a refused fire is recorded once a day and said where the person reads it ───────────────────

def _capped(n_slots, *, cap=2):
    db, clock, reg, ran = _rig()
    _row(db, scope="user:42", name="sweep", cron="*/15 * * * *", steps=("note",))
    reg.refresh_from_db(db)
    for _ in range(n_slots):
        clock.advance(15 * 60)
        schedule.fire_due(db, reg, clock, runs_per_day=cap)
    return db, clock, reg


def test_a_fire_over_the_daily_cap_is_recorded_once_per_day_not_per_slot():
    db, clock, reg = _capped(6, cap=2)
    rows = db.execute("SELECT status, reason, subject_refs FROM reaction "
                      "WHERE event_type = 'schedule.refused'")
    assert len(rows) == 1, "four refused slots, one record"
    status, reason, refs = rows[0]
    refs = json.loads(refs)
    assert status == "failed" and reason.startswith("quota_runs_per_day")
    assert "daily limit of 2 runs" in refs["text"] and "UTC" in refs["text"]
    assert refs["next_run_at"] and refs["uid"] == "42"


def test_a_refusal_record_never_uses_up_the_cap_itself():
    db, clock, reg = _capped(4, cap=2)
    now = clock.now()
    assert admission.runs_today(db, "42", now, scheduled=True) == 2
    assert admission.runs_today(db, "42", now, scheduled=False) == 0


def test_the_refusal_is_posted_into_the_routines_chat_once(monkeypatch):
    from flows_steps import common, routine
    db, clock, reg = _capped(4, cap=2)
    posts = []
    monkeypatch.setattr(routine, "http", lambda m, u, h, b=None, timeout=20: (
        posts.append((h["X-Vexa-Flow-Step"], b["session"], b["prompt"])) or (200, {"ok": True})))
    monkeypatch.setattr(common, "AGENT_API", "http://agent-api.test")
    assert routine.announce_refusals(db) == 1
    assert routine.announce_refusals(db) == 0, "once"
    step, session, text = posts[0]
    assert step == "notice" and session == routine.thread_session("user:42", "sweep")
    assert "daily limit" in text


def test_a_wake_with_nothing_new_is_asked_for_one_checked_line(agent_api):
    db, clock, reg = _routine_rig()
    clock.advance(31)
    schedule.fire_due(db, reg, clock)
    _drain(db, reg, clock)
    assert "`Checked — `" in agent_api.posts[0]["body"]["prompt"]


# ── an event agent: one concern chat per occurrence ─────────────────────────────────────────────

def test_meeting_ended_opens_one_follow_up_chat_per_meeting(agent_api):
    """The example event agent: meeting.completed → a follow-up concern chat per meeting (dedupe by
    meeting id); a second event for the same meeting lands in the same chat."""
    from flows_steps import routine
    db, clock, reg = SqliteDB(), FakeClock(T), Registry()
    routine.register(reg)
    _row(db, name="meeting-follow-up", event="meeting.completed", steps=("agent_run",),
         params={"instruction": "Draft the recap and follow-ups for this meeting.",
                 "thread": "per:meeting_id"})
    reg.refresh_from_db(db)
    for sid, mid in (("done-1", "m-1"), ("done-2", "m-2"), ("again-1", "m-1")):
        admit(db, reg, clock, source_event_id=sid, event_type="meeting.completed",
              subject_refs={"uid": "42", "meeting_id": mid, "native": "x"})
        _drain(db, reg, clock)
    sessions = [p["body"]["session"] for p in agent_api.posts]
    assert sessions[0] == sessions[2] != sessions[1] and sessions[0].startswith("concern-")
    assert [p["headers"]["X-Vexa-Concern"] for p in agent_api.posts] == \
        ["meeting_id:m-1", "meeting_id:m-2", "meeting_id:m-1"]


def test_an_event_agent_turn_is_marked_and_told_to_propose(agent_api):
    from flows_steps import routine
    db, clock, reg = SqliteDB(), FakeClock(T), Registry()
    routine.register(reg)
    _row(db, name="meeting-follow-up", event="meeting.completed", steps=("agent_run",),
         params={"instruction": "Draft the recap.", "thread": "per:meeting_id"})
    reg.refresh_from_db(db)
    admit(db, reg, clock, source_event_id="done-1", event_type="meeting.completed",
          subject_refs={"uid": "42", "meeting_id": "m-1", "native": "x"})
    _drain(db, reg, clock)
    post = agent_api.posts[0]
    assert post["headers"]["X-Vexa-Flow-Kind"] == "event_agent"
    assert "read and propose" in post["body"]["prompt"] and '"needs_go":true' in post["body"]["prompt"]


def test_a_routine_turn_is_not_an_event_agent(agent_api):
    db, clock, reg = _routine_rig()
    clock.advance(31)
    schedule.fire_due(db, reg, clock)
    _drain(db, reg, clock)
    assert "X-Vexa-Flow-Kind" not in agent_api.posts[0]["headers"]


# ── a sink: one chat subscribed to an event stream ──────────────────────────────────────────────

class _Api(_AgentApi):
    def __init__(self):
        super().__init__()
        self.notes = []

    def __call__(self, method, url, headers, body=None, timeout=20):
        if method == "POST" and url.endswith("/api/chat/submit") and headers.get("X-Vexa-Flow-Step") == "notice":
            self.notes.append(body["prompt"])
            return 200, {"ok": True}
        return super().__call__(method, url, headers, body, timeout)


def _sink_rig(monkeypatch, **params):
    from flows_steps import common, routine
    api = _Api()
    monkeypatch.setattr(routine, "http", api)
    monkeypatch.setattr(common, "AGENT_API", "http://agent-api.test")
    db, clock, reg = SqliteDB(), FakeClock(T), Registry()
    routine.register_sinks(reg, db)
    _row(db, name="acme-mail", event="mail.received", steps=("post_event_card",),
         params={"thread": "pchat-acme", **params})
    reg.refresh_from_db(db)
    return api, db, clock, reg


def _mail(db, reg, clock, i, sender="ann@acme.example", **kw):
    admit(db, reg, clock, source_event_id=f"m{i}", event_type="mail.received",
          subject_refs={"uid": "42", "from": sender, "subject": f"note {i}"}, **kw)
    _drain(db, reg, clock)


def test_a_sink_posts_cards_and_runs_no_agent(monkeypatch):
    api, db, clock, reg = _sink_rig(monkeypatch, filter={"from": "acme"})
    _mail(db, reg, clock, 1)
    _mail(db, reg, clock, 2, sender="spam@else.example")
    assert len(api.notes) == 1 and "mail.received" in api.notes[0] and "acme" in api.notes[0]
    assert api.posts == [], "wake: never — a card, no agent turn"


def test_cards_never_count_as_runs_and_are_capped_per_chat(monkeypatch):
    api, db, clock, reg = _sink_rig(monkeypatch, cards_per_hour=3)
    for i in range(6):
        _mail(db, reg, clock, i, runs_per_day=2)
    assert len(api.notes) == 3, "three cards an hour, then the cap"
    assert admission.runs_today(db, "42", clock.now()) == 0, "cards are not runs"


def test_a_sink_wakes_its_chat_as_its_policy_says(monkeypatch):
    api, db, clock, reg = _sink_rig(monkeypatch, wake="match:urgent")
    _mail(db, reg, clock, 1)
    admit(db, reg, clock, source_event_id="m9", event_type="mail.received",
          subject_refs={"uid": "42", "from": "x", "subject": "URGENT: prod down"})
    _drain(db, reg, clock)
    assert len(api.notes) == 2 and len(api.posts) == 1
    assert api.posts[0]["body"]["session"] == "pchat-acme"
    assert admission.runs_today(db, "42", clock.now()) == 1, "only the wake counts"


def test_wake_policies():
    from flows_steps.routine import wake_due
    assert wake_due("per_event", "x", None, 0) and not wake_due("never", "x", None, 0)
    assert wake_due("batch:15", "x", None, 1000) and not wake_due("batch:15", "x", 1000, 1500)
    assert wake_due("batch:15", "x", 1000, 1000 + 901)
