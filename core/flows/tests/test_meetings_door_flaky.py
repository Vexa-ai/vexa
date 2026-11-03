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

from flows import StepError
from flows_defs import production
from flows_steps import common
from flows_steps import meeting as mt
from flows_timeline import service as timeline

URL, NATIVE = "https://meet.google.com/abc-defg-hij", "abc-defg-hij"
ROOM = ["a@bank.test", "b@bank.test"]


def _503():
    return 503, {"detail": "meeting-api is restarting"}


def _timeout():
    return StepError("http GET http://gateway.test/meetings: URLError: timed out")


def _404():
    return 404, {"detail": "Not Found"}


FLAKY = [pytest.param(_503, id="503"), pytest.param(_timeout, id="timeout")]


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
    assert e.value.retryable is True, "flaky is retried on the engine's backoff, not given up on"


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
