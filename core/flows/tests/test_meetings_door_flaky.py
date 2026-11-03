"""A DOOR THAT HAD A MOMENT IS NOT "NOTHING THERE" — brain:card/4310908cf3b3 (Factory's review of
#1998).

A 503 from meeting-api through the gateway's service door, a timeout or a refused connection came
back from these reads as no row, no start, nobody speaking, no meetings, and the step carried on
with the wrong answer: a prepare link on a Zoom number and no planned row, a note stamped today, a
room in invite order, a timeline with no meetings in it. Founder rule: fallbacks only for flaky
things, and this is the flaky thing. So each read raises a retryable `StepError` (the engine's
backoff, then a failed reaction with the reason) and the timeline route answers 503
(`test_operator_tier`). A 404 is an answer and keeps its meaning.

No network: `http` is replaced where each read looks it up, the seam `test_bot_teardown` uses.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flows import Done, EventType, FakeClock, Registry, StepError, Unavailable, admit, status, tick
from flows_defs import production
from flows_steps import common
from flows_steps import meeting as mt
from flows_timeline import service as timeline

URL, NATIVE = "https://meet.google.com/abc-defg-hij", "abc-defg-hij"
ROOM = ["a@bank.test", "b@bank.test"]


def _http(code):
    return lambda: (code, {"detail": "upstream body, not ours to log"})


_503 = _http(503)


def _timeout():
    return StepError("http GET http://gateway.test/meetings: URLError: timed out")


def _404():
    return 404, {"detail": "Not Found"}


FLAKY = [pytest.param(_http(c), id=str(c)) for c in (429, 502, 503, 504)] + [
    pytest.param(_timeout, id="timeout")]


@pytest.fixture(autouse=True)
def _door(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", "flaky-door-flows-key")
    monkeypatch.setattr(common, "MEETINGS_API", "http://gateway.test", raising=False)
    monkeypatch.setattr(production, "setting", lambda uid, key: "")   # identity is not the door


def _wire(monkeypatch, answer, where=mt, get=None):
    """Every call answers `answer()`; with `get`, a GET answers `get()` instead."""
    calls: list = []

    def fake_http(method, url, headers, body=None, timeout=20):
        calls.append(method)
        out = (get if get and method == "GET" else answer)()
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(where, "http", fake_http)
    return calls


def _ctx():
    return SimpleNamespace(scratch={}, refs={"uid": "7", "meeting_id": 41, "native": NATIVE,
                                             "platform": "vexa_call"})


#: (the read, the module whose `http` it calls, what a 404 answers — today's answer, kept)
READS = [
    pytest.param(lambda: mt.meeting_ref("7", URL), mt, NATIVE, id="meeting_ref"),
    pytest.param(lambda: mt.ensure_meeting_row("7", URL, "Sync", 1.9e9), mt, NATIVE,
                 id="ensure_meeting_row"),
    pytest.param(lambda: mt.meeting_row("7", 41, NATIVE), mt, None, id="meeting_row"),
    pytest.param(lambda: mt.meeting_start("7", 41, NATIVE), mt, None, id="meeting_start"),
    pytest.param(lambda: mt.speaking_seconds("7", 41), mt, {}, id="speaking_seconds"),
    pytest.param(lambda: mt.room_order("7", 41, ROOM, {}), mt, ROOM, id="room_order"),
    pytest.param(lambda: timeline.fetch_meetings("7"), common, [], id="fetch_meetings"),
    # the three wrappers that caught whatever `meeting_row` raised and cached the empty answer
    pytest.param(lambda: production._recorded_note_path(_ctx(), "7"), mt, "", id="note_path"),
    pytest.param(lambda: production._call_session(_ctx()), mt, "", id="call_session"),
    pytest.param(lambda: production._mail_title(_ctx()), mt, "Vexa Call meeting", id="mail_title"),
]


@pytest.mark.parametrize("flaky", FLAKY)
@pytest.mark.parametrize("read,where,_today", READS)
def test_a_door_that_had_a_moment_is_retried_not_read_as_nothing(monkeypatch, read, where,
                                                                 _today, flaky):
    _wire(monkeypatch, flaky, where)
    with pytest.raises(StepError) as e:
        read()
    assert e.value.retryable is True, "flaky is retried, not given up on"
    # a 5xx/429 waits without spending an attempt; a timeout keeps the normal budget
    assert isinstance(e.value, Unavailable) is (flaky is not _timeout)
    assert "upstream body" not in str(e.value), "the upstream's body is not carried"


@pytest.mark.parametrize("read,where,today", READS)
def test_a_404_answers_what_it_always_answered(monkeypatch, read, where, today):
    _wire(monkeypatch, _404, where)
    assert read() == today


@pytest.mark.parametrize("flaky", FLAKY)
def test_ensure_meeting_row_raises_when_the_plan_could_not_be_written(monkeypatch, flaky):
    """The read finds no row; the POST that would plan one meets the outage."""
    calls = _wire(monkeypatch, flaky, get=lambda: (200, {"meetings": []}))
    with pytest.raises(StepError):
        mt.ensure_meeting_row("7", URL, "Sync", 1.9e9)
    assert calls == ["GET", "POST"]


def test_the_retry_finds_the_row_an_earlier_attempt_planned(monkeypatch):
    calls = _wire(monkeypatch, lambda: (200, {"meetings": [{"id": 41, "native_meeting_id": NATIVE}]}))
    assert mt.ensure_meeting_row("7", URL, "Sync", 1.9e9) == "41" and calls == ["GET"]


def test_the_note_stamp_is_not_cached_as_today_off_a_503(monkeypatch):
    _wire(monkeypatch, _503)
    ctx = _ctx()
    with pytest.raises(StepError):
        production._meeting_stamp(ctx, "7")
    assert "_meeting_stamp:7" not in ctx.scratch


def test_no_meetings_domain_is_still_no_meetings_and_is_not_knocked_on(monkeypatch):
    calls = _wire(monkeypatch, _503, common)
    monkeypatch.setattr(common, "MEETINGS_API", "")
    assert timeline.fetch_meetings("7") == [] and calls == []


def test_a_scaffold_is_not_minted_without_its_path_when_the_door_had_a_moment(monkeypatch):
    """`_scaffold_refs` caught everything around the note path, this error included."""
    _wire(monkeypatch, _503)
    ctx = _ctx()
    ctx.refs["title"] = "Sync"
    with pytest.raises(Unavailable):
        production._scaffold_refs(ctx, "7")


def test_a_scaffold_with_no_meetings_domain_is_still_minted_without_a_path(monkeypatch):
    """What it caught before for absent data, it still catches: no domain, no path, no raise."""
    calls = _wire(monkeypatch, _503)
    monkeypatch.setattr(common, "MEETINGS_API", "")
    ctx = _ctx()
    ctx.refs["title"] = "Sync"
    assert production._scaffold_refs(ctx, "7") == {"title": "Sync"} and calls == []


# ── through the engine: how long a flaky door is waited on ───────────────────────────────────────
def _engine(monkeypatch, read, answer):
    """One step doing `read()` under the real loop; `answer(elapsed)` is the door at that moment.
    Returns the reaction's status and how long the loop ran."""
    from sqlite_double import SqliteDB
    db, clock, reg = SqliteDB(), FakeClock(1_000_000.0), Registry()
    t0 = clock.now()

    @reg.step
    def door_read(ctx):
        return Done({"read": read()})
    reg.flow(name="door", version=1, on=EventType("t.door"), steps=[door_read])
    _wire(monkeypatch, lambda: answer(clock.now() - t0))
    admit(db, reg, clock, source_event_id="s1", event_type="t.door", subject_refs={})
    for _ in range(2000):
        if not tick(db, reg, clock):
            nxt = db.execute("SELECT MIN(next_run_at) FROM reaction "
                             "WHERE status IN ('admitted','retrying')")[0][0]
            if nxt is None:
                break
            clock._t = max(clock._t, nxt)
    return status(db, db.execute("SELECT reaction_id FROM reaction")[0][0]), clock.now() - t0


def _row(_elapsed=0):
    return 200, {"meetings": [{"id": 41, "native_meeting_id": NATIVE}]}


def test_a_503_that_outlasts_the_attempts_succeeds_when_the_door_comes_back(monkeypatch):
    """Six attempts over 5/30/120/600 s is about 23 minutes; an upgrade window is longer."""
    st, ran = _engine(monkeypatch, lambda: mt.meeting_row("7", 41, NATIVE),
                      lambda t: _503() if t < 40 * 60 else _row())
    assert st["status"] == "done", st["reason"]
    assert ran >= 40 * 60


def test_a_503_past_six_hours_fails_loudly_with_the_reason(monkeypatch):
    st, ran = _engine(monkeypatch, lambda: mt.meeting_row("7", 41, NATIVE), lambda t: _503())
    assert st["status"] == "failed"
    assert "GET /meetings answered HTTP 503" in st["reason"] and "gave up" in st["reason"]
    assert 6 * 3600 <= ran < 6 * 3600 + 120


def test_a_refused_ticket_keeps_the_normal_budget_then_fails_loudly(monkeypatch):
    """A refused subject ticket is not the door having a moment: it now comes through (it was
    swallowed as nobody speaking), is retried on the engine's attempts, and fails with its reason."""
    def refused(_t):
        common._ticket_refusal("GET", "http://gateway.test/transcripts/by-id/41", 403,
                               {"reason": "subject_ticket_required"})
    st, ran = _engine(monkeypatch, lambda: mt.speaking_seconds("7", 41), refused)
    assert st["status"] == "failed"
    assert "subject_ticket_required" in st["reason"]
    assert ran < 30 * 60, "the normal budget, not the six-hour wait"

