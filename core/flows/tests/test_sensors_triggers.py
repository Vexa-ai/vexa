"""ADR-0050 phases A and B, offline: triggers (glob, filter, relative + keyed schedule), the light
budget, and the sensor pool with a fake calendar kind — the worked example end to end: a bot one
minute before every calendar meeting with a video link, following reschedules, skipping declined,
with zero model calls."""
from __future__ import annotations

import calendar
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flows import Done, FakeClock, Registry, admit, keyed, tick, triggers  # noqa: E402
from flows.registry import SCHEDULE_EVENT  # noqa: E402
from flows_sensors import Change, SensorFault, SensorKind, Tick  # noqa: E402
from flows_sensors import pool  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

T = float(calendar.timegm((2027, 3, 1, 9, 0, 0)))
FILTER = "payload.video_url exists and payload.self_response != 'declined'"
TRIGGER = {"kind": "relative", "filter": FILTER, "at": "payload.start - 60s", "key": "payload.event_id"}


# ── the filter language ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,refs,want", [
    (FILTER, {"video_url": "https://meet.google.com/x", "self_response": "accepted"}, True),
    (FILTER, {"video_url": "https://meet.google.com/x", "self_response": "declined"}, False),
    (FILTER, {"self_response": "accepted"}, False),
    ("payload.title contains 'standup'", {"title": "Daily Standup"}, True),
    ("payload.n in [1, 2] or not payload.x exists", {"n": 3}, True),
    ("(payload.a == 'x' and payload.b != 'y')", {"a": "x", "b": "y"}, False),
    ("type == 'calendar.event.created'", {}, True),
])
def test_filters(text, refs, want):
    assert triggers.passes(text, "calendar.event.created", refs) is want


@pytest.mark.parametrize("bad", ["payload.x", "payload.x == ", "import os", "payload.x ~= 1", "a" * 600])
def test_a_filter_this_engine_cannot_read_is_refused(bad):
    with pytest.raises(triggers.TriggerError):
        triggers.parse_filter(bad)


def test_relative_times_read_calendar_shapes():
    refs = {"start": {"dateTime": "2027-03-01T10:00:00+01:00"}}
    assert triggers.fire_at("payload.start - 60s", "calendar.event.created", refs) == T - 60
    assert triggers.fire_at("payload.start + 2m", "x.y", {"start": T}) == T + 120
    assert triggers.fire_at("payload.start - 60s", "x.y", {}) is None
    with pytest.raises(triggers.TriggerError):
        triggers.parse_at("payload.start - 30d")


# ── the engine: glob, keyed schedule, light budget ───────────────────────────────────────────────

def _rig(steps=("request_meeting_bot",), trigger=TRIGGER, event="calendar.event.*"):
    db, clock, reg = SqliteDB(), FakeClock(T - 3600), Registry()
    fired: list = []

    @reg.step
    def request_meeting_bot(ctx):
        fired.append((ctx.refs.get("_key"), ctx.refs.get("fire_at"), ctx.refs.get("video_url")))
        return Done({})

    @reg.step
    def note(ctx):
        fired.append(ctx.refs.get("event_id"))
        return Done({})
    db.execute("INSERT INTO flow_version (scope, name, version, on_event, cron, steps, params, status, "
               "created_by, created_at) VALUES ('user:7', 'bot-to-my-meetings', 1, :e, NULL, :s, :p, "
               "'active', 't', 0)", {"e": event, "s": json.dumps(list(steps)),
                                     "p": json.dumps({"_trigger": trigger, "grants": ["bot.request"]})})
    reg.refresh_from_db(db)
    return db, clock, reg, fired


def _event(db, reg, clock, kind, eid, start, *, video=True, response="accepted", etag="1", uid="7"):
    refs = {"uid": uid, "event_id": eid, "start": start, "title": f"meeting {eid}",
            "self_response": response, **({"video_url": f"https://meet.google.com/{eid}"} if video else {})}
    return admit(db, reg, clock, source_event_id=f"cal:{eid}:{etag}", event_type=f"calendar.event.{kind}",
                 subject_refs=refs)


def _drain(db, reg, clock):
    keyed.fire_due(db, reg, clock)
    while tick(db, reg, clock):
        pass


def test_a_bot_one_minute_before_each_video_meeting_follows_reschedules_and_skips_declined():
    db, clock, reg, fired = _rig()
    assert _event(db, reg, clock, "created", "aaa", T) == 0        # arms, starts nothing yet
    _event(db, reg, clock, "created", "nov", T, video=False)
    _event(db, reg, clock, "created", "dec", T, response="declined")
    _event(db, reg, clock, "created", "ccc", T + 600)
    _event(db, reg, clock, "updated", "aaa", T + 1800, etag="2")   # moved half an hour later
    _event(db, reg, clock, "cancelled", "ccc", T + 600, etag="2")
    assert [u["key"] for u in keyed.upcoming(db, "user:7", "bot-to-my-meetings")] == ["aaa"]
    clock.advance(3600 - 60)          # the original slot: nothing fires, the meeting moved
    _drain(db, reg, clock)
    assert fired == []
    clock.advance(1800)
    _drain(db, reg, clock)
    assert fired == [("aaa", T + 1800 - 60, "https://meet.google.com/aaa")]
    clock.advance(60)
    _drain(db, reg, clock)
    assert len(fired) == 1, "a slot fires once"
    _event(db, reg, clock, "updated", "aaa", T + 7200, etag="3")   # moved again after it fired
    clock.advance(T + 7200 - 60 + 5 - clock.now())
    _drain(db, reg, clock)
    assert len(fired) == 2, "a meeting moved after its bot went re-arms once"


def test_someone_elses_event_arms_nothing():
    db, clock, reg, fired = _rig()
    _event(db, reg, clock, "created", "aaa", T, uid="8")
    assert keyed.upcoming(db, "user:7", "bot-to-my-meetings") == []


def test_an_event_filter_starts_a_run_only_for_events_that_pass():
    db, clock, reg, fired = _rig(steps=("note",), trigger={"kind": "event", "filter": FILTER})
    assert _event(db, reg, clock, "created", "aaa", T) == 1
    assert _event(db, reg, clock, "created", "dec", T, response="declined") == 0
    while tick(db, reg, clock):
        pass
    assert fired == ["aaa"]


def test_a_flow_of_light_steps_runs_on_its_own_budget():
    from flows import admission
    db, clock, reg, fired = _rig()
    flow = reg.newest("user:7", "bot-to-my-meetings")
    assert admission.is_light(flow)
    assert admission.admissible(db, flow, clock, event_type="keyed.due", subject_refs={"uid": "7"},
                                runs_per_day=0, light_runs_per_day=5)
    assert not admission.admissible(db, flow, clock, event_type="keyed.due",
                                    subject_refs={"uid": "7"}, light_runs_per_day=0)


# ── the sensor: the person's own, designed, tested, enabled, connected ───────────────────────────
from flows_sensors import store  # noqa: E402
from flows_sensors.calendar import CalendarSensor  # noqa: E402

ITEMS = [
    {"event_id": "aaa", "etag": "1", "status": "confirmed", "title": "Sync", "start": "2027-03-01T10:00:00+01:00",
     "self_response": "accepted", "video_url": "https://meet.google.com/aaa"},
    {"event_id": "dec", "etag": "1", "status": "confirmed", "title": "Declined one",
     "start": "2027-03-01T11:00:00+01:00", "self_response": "declined", "video_url": "https://meet.google.com/dec"},
    {"event_id": "nov", "etag": "1", "status": "confirmed", "title": "Lunch", "start": "2027-03-01T12:00:00+01:00",
     "self_response": "accepted"},
]


class Broker:
    """The broker's calendar-changes answer, recorded: `pages[cursor]`."""

    def __init__(self):
        self.calls, self.fail = [], None
        self.pages = {None: {"items": ITEMS, "cursor": "c1", "resync_required": False},
                      "c1": {"items": [], "cursor": "c1", "resync_required": False}}

    def __call__(self, owner, path, body, *, session):
        self.calls.append((owner, body.get("cursor")))
        if self.fail:
            raise SensorFault(self.fail)
        return self.pages[body.get("cursor")]


def _kinds(broker):
    return {"calendar": CalendarSensor(call=broker)}


def _emit(db, reg, clock):
    return lambda et, sid, refs: admit(db, reg, clock, source_event_id=sid, event_type=et,
                                       subject_refs=refs)


def test_design_test_enable_then_it_feeds_the_keyed_schedule_with_no_model_call():
    db, clock, reg, fired = _rig(trigger={"kind": "relative", "at": "payload.start - 60s",
                                          "key": "payload.event_id"})
    broker = Broker()
    kinds = _kinds(broker)
    store.create(db, kinds, "user:7", name="video-meetings", kind="calendar", filter=FILTER, now=clock.now())
    with pytest.raises(store.SensorError) as e:
        store.enable(db, "user:7", "video-meetings")
    assert e.value.reason == "test_first"
    t = store.test(db, kinds, "user:7", "video-meetings")
    assert t["ok"] and [w["key"] for w in t["would_emit"]] == ["aaa:1"]
    assert sorted(d["key"] for d in t["dropped"]) == ["dec:1", "nov:1"] and t["dropped"][0]["reason"] == "filter"
    assert db.execute("SELECT COUNT(*) FROM reaction")[0][0] == 0, "a test admits nothing"
    assert store.get(db, "user:7", "video-meetings").cursor is None, "a test stores nothing"
    store.enable(db, "user:7", "video-meetings", now=clock.now())
    out = pool.run_due(db, kinds, _emit(db, reg, clock), clock.now())
    assert out[0]["kept"] == 1
    assert [u["key"] for u in keyed.upcoming(db, "user:7", "bot-to-my-meetings")] == ["aaa"]
    clock.advance(120)
    pool.run_due(db, kinds, _emit(db, reg, clock), clock.now())
    assert broker.calls == [("7", None), ("7", None), ("7", "c1")], "test, first read, then the cursor"
    h = pool.health(db, "user:7")[0]
    assert h["status"] == "active" and h["tested"] and h["last_events"][0]["title"] == "Sync"


def test_a_change_needs_a_new_test():
    db, clock, reg, _ = _rig()
    kinds = _kinds(Broker())
    store.create(db, kinds, "user:7", name="s", kind="calendar")
    store.test(db, kinds, "user:7", "s")
    store.enable(db, "user:7", "s")
    s = store.update(db, kinds, "user:7", "s", filter="payload.video_url exists")
    assert s.status == "draft" and s.version == 2
    with pytest.raises(store.SensorError):
        store.enable(db, "user:7", "s")


def test_bad_filters_kinds_and_intervals_are_refused_by_name():
    db, clock, reg, _ = _rig()
    kinds = _kinds(Broker())
    for kw, reason in (({"kind": "fax"}, "unknown_kind"), ({"filter": "payload.x =="}, "bad_filter"),
                       ({"interval_s": 5}, "interval_too_short")):
        with pytest.raises(store.SensorError) as e:
            store.create(db, kinds, "user:7", name="s", **{"kind": "calendar", **kw})
        assert e.value.reason == reason


def test_a_failing_sensor_degrades_with_its_reason_and_recovers():
    db, clock, reg, _ = _rig()
    broker = Broker()
    kinds = _kinds(broker)
    store.create(db, kinds, "user:7", name="s", kind="calendar")
    store.test(db, kinds, "user:7", "s")
    store.enable(db, "user:7", "s", now=clock.now())
    broker.fail = "provider rate limit reached"
    for _ in range(3):
        pool.run_due(db, kinds, _emit(db, reg, clock), clock.now())
        clock.advance(4000)
    h = pool.health(db, "user:7")[0]
    assert h["status"] == "degraded" and h["reason"] == "provider rate limit reached"
    broker.fail = None
    pool.run_due(db, kinds, _emit(db, reg, clock), clock.now())
    assert pool.health(db, "user:7")[0]["status"] == "active"


def test_a_sensor_cannot_speak_for_another_person():
    db, clock, reg, _ = _rig()
    kinds = _kinds(Broker())
    store.create(db, kinds, "user:8", name="s", kind="calendar")
    store.test(db, kinds, "user:8", "s")
    store.enable(db, "user:8", "s", now=clock.now())
    pool.run_due(db, kinds, _emit(db, reg, clock), clock.now())
    assert keyed.upcoming(db, "user:7", "bot-to-my-meetings") == [], "user 8's events start nothing of 7's"


def test_post_message_is_a_light_step_that_fills_slots(monkeypatch):
    from flows import Done, admission
    from flows_steps import meeting
    said = []
    monkeypatch.setattr(meeting, "_say", lambda ctx, uid, text: (said.append(text), ctx.scratch.update(noticed=True)))
    reg = Registry()
    meeting.register_light_steps(reg)
    assert "post_message" in reg.user_allowed and "post_message" in admission.LIGHT_STEPS
    db, clock = SqliteDB(), FakeClock(T)
    db.execute("INSERT INTO flow_version (scope, name, version, on_event, cron, steps, params, status, "
               "created_by, created_at) VALUES ('user:7', 'say', 1, 'x.y', NULL, '[\"post_message\"]', :p, "
               "'active', 't', 0)", {"p": json.dumps({"text": "{{date}}: {{payload.title}}"})})
    reg.refresh_from_db(db)
    admit(db, reg, clock, source_event_id="e1", event_type="x.y", subject_refs={"uid": "7", "title": "Sync"})
    while tick(db, reg, clock):
        pass
    assert said == ["Monday 01 March 2027: Sync"]


def test_the_keyed_cap_is_per_person_even_on_a_company_flow():
    from flows.registry import Flow, EventType
    db, clock = SqliteDB(), FakeClock(T)
    company = Flow(name="invite_join", version=1, on=EventType("invite.*"), steps=("note",))
    for i in range(3):
        assert keyed.upsert(db, clock, company, key=f"a{i}", fire_at=T + 60, event_type="invite.received",
                            payload={"organizer": "a@x.test"}, per_user=3) == "armed"
    assert keyed.upsert(db, clock, company, key="a3", fire_at=T + 60, event_type="invite.received",
                        payload={"organizer": "a@x.test"}, per_user=3) == "refused"
    assert keyed.upsert(db, clock, company, key="b0", fire_at=T + 60, event_type="invite.received",
                        payload={"organizer": "b@x.test"}, per_user=3) == "armed", "a busy person starves nobody"
