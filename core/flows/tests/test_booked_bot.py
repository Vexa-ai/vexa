"""ADR-0052 s2d — `booked_bot`: Flows owns the one clock for booked meetings (§3, §4, §6).

meeting-api's outbox hands every planned-row change to Flows as `meeting.planned.changed`
(`meeting_id`, `uid`, `status`, `auto_join`, `pod_at`, `join_at`, `deadline`, `revision`). The
code-registered flow `booked_bot` keeps ONE keyed row per meeting:

* a scheduled booking with auto-join on arms it at `pod_at`, missed only past `deadline`;
* a move re-arms it; a cancel, a delete, auto-join off or a claimed row disarms it;
* an OLDER revision delivered again after a newer one changes nothing;
* its fire runs `start_booked_bot`: the start route for THAT row id, through the service door as
  the row's owner; 201 → done; 409 not_scheduled/past_deadline/already_live, 404 → done;
  429/503/too_early → wait Retry-After (default 15 s), until the deadline;
* instance-scoped: a person's daily run cap does not stop it.
Offline: SQLite double, FakeClock, `flows_steps.meeting.http` replaced per test."""
from __future__ import annotations

import calendar
import json
from datetime import datetime, timezone

import pytest

from flows import Done, FakeClock, Reaction, Registry, StepCtx, StepError, Wait, admit, keyed, triggers
from flows_defs import production
from flows_steps import meeting as mt
from sqlite_double import SqliteDB

import flows_worker.__main__ as worker

T = float(calendar.timegm((2030, 3, 1, 9, 0, 0)))          # the meeting's start
POD, JOIN, GRACE = 600, 120, 600
LIMITS = {"loop_limit": 3, "max_hops": 8, "runs_per_day": 0, "messages_per_day": 0}


def _z(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _fact(mid=41, rev=1, *, start=T, status="scheduled", auto_join=True, uid="7") -> dict:
    on = status == "scheduled"
    return {"meeting_id": mid, "uid": uid, "status": status, "auto_join": auto_join and on,
            "pod_at": _z(start - POD) if on else None, "join_at": _z(start - JOIN) if on else None,
            "deadline": _z(start + GRACE) if on else None, "revision": rev}


def _rig():
    db, clock, reg = SqliteDB(), FakeClock(T - 3600), Registry()
    production.build(reg, db)
    return db, clock, reg


def _deliver(db, reg, clock, fact):
    return admit(db, reg, clock, source_event_id=f"planned-{fact['meeting_id']}-r{fact['revision']}",
                 event_type="meeting.planned.changed", subject_refs=dict(fact))


def _row(db, mid=41):
    rows = db.execute("SELECT status, fire_at, deadline, payload FROM keyed_schedule "
                      "WHERE flow = 'booked_bot' AND key = :k", {"k": str(mid)})
    return rows[0] if rows else None


# ── the flow is code, and it reacts to the fact ──────────────────────────────────────────────────

def test_booked_bot_is_registered_in_code_on_the_planned_fact():
    db, clock, reg = _rig()
    flow = reg.newest("instance", "booked_bot")
    assert flow is not None and not flow.owner, "an instance flow, registered in code"
    assert "booked_bot" not in [d["name"] for d in production.SEEDS]
    spec = triggers.spec_of(flow)
    assert spec["kind"] == "relative" and spec["at"] == "payload.pod_at"
    assert spec["deadline"] == "payload.deadline" and spec["key"] == "payload.meeting_id"
    assert spec["share"] == "account" and spec["revision"] == "payload.revision"
    assert flow.steps[0] == "start_booked_bot"
    # flows-api's intake answers 400 no_flow_reacts_to for a fact nothing reacts to; now something does
    assert [f.name for f in reg.match("meeting.planned.changed")] == ["booked_bot"]


def test_a_booking_arms_at_pod_at_with_the_meetings_deadline():
    db, clock, reg = _rig()
    _deliver(db, reg, clock, _fact())
    status, fire_at, deadline, _payload = _row(db)
    assert status == "armed" and fire_at == T - POD and deadline == T + GRACE


def test_a_move_rearms_and_a_cancel_delete_toggle_or_claim_disarms():
    for change in ({"start": T + 3600}, ):
        db, clock, reg = _rig()
        _deliver(db, reg, clock, _fact(rev=1))
        _deliver(db, reg, clock, _fact(rev=2, **change))
        assert _row(db)[0] == "armed" and _row(db)[1] == T + 3600 - POD
    for gone in ({"status": "deleted"}, {"auto_join": False}, {"status": "requested"},
                 {"status": "idle"}):
        db, clock, reg = _rig()
        _deliver(db, reg, clock, _fact(rev=1))
        _deliver(db, reg, clock, _fact(rev=2, **gone))
        assert _row(db)[0] == "cancelled", gone


def test_an_older_revision_never_overwrites_a_newer_one():
    db, clock, reg = _rig()
    _deliver(db, reg, clock, _fact(rev=1))
    _deliver(db, reg, clock, _fact(rev=2, start=T + 3600))               # moved
    _deliver(db, reg, clock, _fact(rev=1))                               # the old one, again
    assert _row(db)[0] == "armed" and _row(db)[1] == T + 3600 - POD, "the move stands"
    _deliver(db, reg, clock, _fact(rev=3, status="deleted"))             # deleted
    _deliver(db, reg, clock, _fact(rev=2, start=T + 3600))               # the move, again
    assert _row(db)[0] == "cancelled", "the delete stands"
    assert json.loads(_row(db)[3])["revision"] == 3
    _deliver(db, reg, clock, _fact(rev=4, start=T + 7200))               # booked again, later
    assert _row(db)[0] == "armed" and _row(db)[1] == T + 7200 - POD


def test_the_worker_fires_it_at_pod_at_whatever_the_persons_daily_cap():
    db, clock, reg = _rig()
    _deliver(db, reg, clock, _fact())
    assert worker.keyed_pass(db, reg, clock, LIMITS) == 0, "not before pod_at"
    clock._t = T - POD + 1
    assert worker.keyed_pass(db, reg, clock, LIMITS) == 1, "runs_per_day=0 does not stop it"
    assert worker.keyed_pass(db, reg, clock, LIMITS) == 0, "and only once"
    flow, refs = db.execute("SELECT flow, subject_refs FROM reaction "
                            "WHERE event_type = 'keyed.due'")[0]
    refs = json.loads(refs)
    assert flow == "booked_bot" and refs["meeting_id"] == 41 and refs["uid"] == "7"
    assert "meeting_url" not in refs and "url" not in refs, "the fact carries no link"


def test_a_flows_outage_until_just_before_the_deadline_still_fires():
    db, clock, reg = _rig()
    _deliver(db, reg, clock, _fact())
    clock._t = T + GRACE - 1                       # down from pod_at to 10 min after the start
    assert worker.keyed_pass(db, reg, clock, LIMITS) == 1


def test_each_account_has_its_own_share_and_no_company_total(monkeypatch):
    db, clock, reg = _rig()
    for uid in range(1, 4):
        for n in range(3):
            refusals: list = []
            admit(db, reg, clock, source_event_id=f"p-{uid}-{n}",
                  event_type="meeting.planned.changed",
                  subject_refs=_fact(mid=uid * 100 + n, uid=str(uid)), refusals=refusals,
                  keyed_per_user=3)
            assert not refusals
    refusals = []
    admit(db, reg, clock, source_event_id="p-1-x", event_type="meeting.planned.changed",
          subject_refs=_fact(mid=199, uid="1"), refusals=refusals, keyed_per_user=3)
    assert refusals, "the account's own share is full: answered, not only logged"
    monkeypatch.setattr(keyed, "COMPANY_MAX", 2)
    refusals = []
    admit(db, reg, clock, source_event_id="p-9-0", event_type="meeting.planned.changed",
          subject_refs=_fact(mid=900, uid="9"), refusals=refusals, keyed_per_user=3)
    assert not refusals, "no company total on the account share"


# ── the fire: the start route for THIS row ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _flows_key(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", "booked-flows-key")
    monkeypatch.setattr(mt, "meetings_door", lambda: "http://door/meetings")


def _ctx(now: float, fact=None, scratch=None) -> StepCtx:
    refs = {**(fact or _fact()), "_key": "41", "fire_at": T - POD}
    r = Reaction("rid", "sid", "keyed.due", refs, "booked_bot", 1, "start_booked_bot",
                 "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:start_booked_bot", prior={}, clock_now=now,
                   scratch={} if scratch is None else scratch)


def _wire(monkeypatch, status, body, headers=None):
    calls: list = []

    def fake_http(method, url, hdr, body_=None, timeout=20, response_headers=None):
        calls.append((method, url, dict(hdr)))
        if isinstance(status, Exception):
            raise status
        if response_headers is not None:
            response_headers.update(headers or {})
        return status, body

    monkeypatch.setattr(mt, "http", fake_http)
    return calls


def test_the_fire_starts_the_bot_for_this_row_as_its_owner(monkeypatch):
    calls = _wire(monkeypatch, 201, {"code": "started", "meeting": {"id": 41, "status": "requested"},
                                     "join_at": _z(T - JOIN)})
    out = mt.start_booked_bot(_ctx(T - POD))
    assert isinstance(out, Done) and out.result["code"] == "started"
    assert out.result["join_at"] == _z(T - JOIN)
    (method, url, hdr), = calls
    assert (method, url) == ("POST", "http://door/meetings/internal/meetings/41/start")
    assert hdr["X-User-Id"] == "7" and hdr["X-Vexa-Service-Key"] == "booked-flows-key"


@pytest.mark.parametrize("status,code", [(409, "not_scheduled"), (409, "past_deadline"),
                                         (409, "already_live"), (404, "not_found"),
                                         (403, "blocked"), (422, "meeting_host_refused")])
def test_nothing_to_do_ends_the_fire(monkeypatch, status, code):
    _wire(monkeypatch, status, {"code": code, "detail": "why"})
    out = mt.start_booked_bot(_ctx(T - POD))
    assert isinstance(out, Done) and out.result["code"] == code


@pytest.mark.parametrize("status,code,headers,wait", [
    (429, "max_bots", {"retry-after": "30"}, 30), (503, "spawn_failed", {}, 15),
    (409, "too_early", {"retry-after": "40"}, 40), (402, "service_denied", {"retry-after": "15"}, 15),
    (429, "backoff", {"retry-after": "120"}, 120)])
def test_come_back_when_meeting_api_says(monkeypatch, status, code, headers, wait):
    _wire(monkeypatch, status, {"code": code, "detail": "full"}, headers)
    out = mt.start_booked_bot(_ctx(T - POD))
    assert isinstance(out, Wait) and out.until == T - POD + wait


def test_a_transport_error_waits_the_default(monkeypatch):
    _wire(monkeypatch, StepError("http POST …: URLError"), None)
    out = mt.start_booked_bot(_ctx(T - POD))
    assert isinstance(out, Wait) and out.until == T - POD + mt.BOOKED_RETRY_S


def test_the_wait_never_runs_past_the_deadline_and_past_it_the_fire_ends(monkeypatch):
    _wire(monkeypatch, 429, {"code": "max_bots", "detail": "bot concurrency limit reached"},
          {"retry-after": "900"})
    scratch: dict = {}
    out = mt.start_booked_bot(_ctx(T + GRACE - 10, scratch=scratch))
    assert isinstance(out, Wait) and out.until == T + GRACE + 1
    calls = _wire(monkeypatch, 201, {"code": "started"})
    out = mt.start_booked_bot(_ctx(T + GRACE + 1, scratch=scratch))
    assert isinstance(out, Done) and out.result["code"] == "past_deadline"
    assert "concurrency" in out.result["reason"] and calls == [], "no start past the deadline"


@pytest.mark.parametrize("status,reason", [(403, "service_key_required"),
                                           (403, "not_on_the_allow_list"),
                                           (403, "subject_unknown"), (404, "domain_not_deployed"),
                                           (400, "subject_required")])
def test_a_door_refusal_fails_the_fire_loudly(monkeypatch, status, reason):
    """The gateway door refusing the call itself (`service_door._refuse`) is a configuration fault:
    meeting-api never saw it and stamped nothing. It must fail the step, never end it as done."""
    _wire(monkeypatch, status, {"detail": f"service door: {reason}", "reason": reason,
                                "method": "POST", "path": "/internal/meetings/41/start"})
    with pytest.raises(StepError) as e:
        mt.start_booked_bot(_ctx(T - POD))
    assert e.value.retryable is False and reason in str(e.value)


def test_meeting_api_answers_with_the_same_codes_still_end_the_fire(monkeypatch):
    for status, code in ((403, "blocked"), (404, "not_found")):
        _wire(monkeypatch, status, {"code": code, "detail": "why"})
        assert isinstance(mt.start_booked_bot(_ctx(T - POD)), Done)


def test_a_fact_without_its_owner_is_refused_for_good(monkeypatch):
    _wire(monkeypatch, 201, {"code": "started"})
    with pytest.raises(StepError) as e:
        mt.start_booked_bot(_ctx(T - POD, fact={**_fact(), "uid": ""}))
    assert e.value.retryable is False


def test_booked_less_than_ten_minutes_ahead_fires_on_the_next_pass():
    """ADR-0052 §2: pod_at already past → the pod starts at once (and waits for join_at)."""
    db, clock, reg = _rig()
    clock._t = T - 300                                   # booked 5 minutes before the start
    _deliver(db, reg, clock, _fact())
    assert _row(db)[0] == "armed" and _row(db)[1] == T - POD < clock.now()
    assert worker.keyed_pass(db, reg, clock, LIMITS) == 1


def test_a_full_ceiling_starts_the_bot_when_a_slot_frees(monkeypatch):
    """ADR-0052 §2/§6: at the ceiling the start is refused 429; Flows comes back after Retry-After
    and the bot starts once a slot is free — arrival order, no priority."""
    scratch: dict = {}
    _wire(monkeypatch, 429, {"code": "max_bots", "detail": "bot concurrency limit reached"},
          {"retry-after": "15"})
    first = mt.start_booked_bot(_ctx(T - POD, scratch=scratch))
    assert isinstance(first, Wait) and first.until == T - POD + 15
    _wire(monkeypatch, 201, {"code": "started", "join_at": _z(T - JOIN)})
    second = mt.start_booked_bot(_ctx(first.until, scratch=scratch))
    assert isinstance(second, Done) and second.result["code"] == "started"
