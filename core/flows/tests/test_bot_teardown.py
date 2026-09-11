"""THE BOT LEAVES, OR SOMEBODY IS TOLD — P22, and the review's E2.

`run_meeting` asked the bot to stop and never checked whether it had been asked:

    http("DELETE", f"{meetings_door()}/bots/{platform}/{native}", {"X-API-Key": key})
    return Wait(seconds=5)

The return value was discarded on its own line, so a 403, a 404 or a 500 was indistinguishable
from a teardown that worked — and the step then polled for a `stopping` it would never see. The
`stopping` branch had no deadline either, so a bot that will not leave a call sits on `Wait(4)`
for the life of the worker: still in the room, still recording, the reaction parked in `retrying`,
and nothing anywhere carrying the word "stuck". *"Teardown is requested, not guaranteed."*

`_status` was the third half of the same defect. Every non-200 came back as the string
`f"http-{st}"`, which matches no branch, so a 404 on a meeting this key cannot address fell
through to the bottom `Wait(6)` and was retried forever as though it were a meeting state nobody
had heard of.

Three properties here, and none of them is "the stop always works":

  1. a non-2xx DELETE is COUNTED, and 404/409 are success (a stop is idempotent — the bot already
     being gone is the outcome, not a failure);
  2. repeated refusal, and an unbounded `stopping`, both escalate to a **`Block` with a deadline**
     — a row a person reads, naming the meeting and what the platform said;
  3. an unreadable status is its own named branch and is bounded, because a 404 does not become
     readable by asking again.

No network: `flows_steps.meeting.http` is replaced per test, which is the same seam
`test_link_loop`'s mint tests use.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows import Block, Done, Reaction, StepCtx, StepError, Wait  # noqa: E402
from flows_steps import meeting as mt  # noqa: E402

PRIOR = {"ensure_user": {"uid": "7"},
         "dispatch_bot": {"meeting_id": 41, "native": "abc-defg-hij", "platform": "google_meet"}}


def _ctx(*, now: float = 1_000_000.0, start: float = 0.0, scratch=None,
         transcribe_s: float = 45.0) -> StepCtx:
    refs = {"uid": "7", "start": start, "transcribe_s": transcribe_s}
    r = Reaction("rid", "sid", "meeting.started", refs, "invite_intake", 3, "run_meeting",
                 "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:run_meeting", prior=PRIOR, clock_now=now,
                   scratch={} if scratch is None else scratch)


@pytest.fixture(autouse=True)
def _no_key(monkeypatch):
    monkeypatch.setattr(mt, "user_api_key", lambda uid: "k")


def _wire(monkeypatch, answers):
    """`answers` is (method, ...) -> (status, body); every call is recorded."""
    calls: list = []

    def fake_http(method, url, headers, body=None, timeout=20):
        calls.append((method, url))
        out = answers(method, url)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(mt, "http", fake_http)
    return calls


def _statuses(status: str, **extra):
    def answer(method, url):
        if method == "GET":
            return 200, {"status": status, **extra}
        return 200, {"ok": True}
    return answer


# ── the read: a non-200 is a named branch, not an unrecognised state ─────────────────────────────
def test_a_non_200_status_read_is_named_unreadable_not_an_unknown_state(monkeypatch):
    """THE REGRESSION on `_status`. It used to answer `{"status": "http-404"}`, which no branch
    matches — so the read failure and a meeting state nobody had heard of took the same path."""
    _wire(monkeypatch, lambda m, u: (404, {"detail": "Meeting not found"}))
    out = mt._status(_ctx())
    assert out["status"] == mt.STATUS_UNREADABLE
    assert out["http"] == 404
    assert "not found" in out["detail"].lower()


def test_a_transport_failure_is_the_same_named_branch(monkeypatch):
    """A gateway that refuses the connection raises `StepError` out of `http`; that is still a read
    that did not happen, and it must not fail the step on its own."""
    _wire(monkeypatch, lambda m, u: StepError("http GET …: ConnectionRefusedError"))
    out = mt._status(_ctx())
    assert out["status"] == mt.STATUS_UNREADABLE and out["http"] is None


def test_an_unreadable_status_waits_then_gives_up_with_the_reason(monkeypatch):
    """Bounded, because a 404 on a meeting this key cannot address never becomes readable by
    asking again — and unbounded retries against it are how a reaction disappears into `retrying`
    with a reason that names no cause."""
    _wire(monkeypatch, lambda m, u: (404, {"detail": "Meeting not found"}))
    scratch: dict = {}
    for _ in range(mt.STATUS_READ_ATTEMPTS_MAX):
        assert isinstance(mt.run_meeting(_ctx(scratch=scratch)), Wait)
    with pytest.raises(StepError) as e:
        mt.run_meeting(_ctx(scratch=scratch))
    assert "unreadable" in str(e.value) and "404" in str(e.value)
    assert "abc-defg-hij" in str(e.value), "the reason names the meeting"
    assert e.value.retryable is False


def test_one_good_read_clears_the_unreadable_count(monkeypatch):
    """A gateway restart mid-meeting must not spend the budget for a later real failure."""
    _wire(monkeypatch, _statuses("active"))
    scratch = {"status_unreadable": 5}
    mt.run_meeting(_ctx(scratch=scratch, start=0.0, now=1.0))
    assert scratch["status_unreadable"] == 0


# ── the stop: it is checked ──────────────────────────────────────────────────────────────────────
def _past_the_window(scratch=None):
    return _ctx(now=1_000_000.0, start=0.0, transcribe_s=45.0, scratch=scratch)


def test_a_2xx_delete_is_a_stop_that_was_asked_for(monkeypatch):
    calls = _wire(monkeypatch, lambda m, u: (200, {"status": "active"}) if m == "GET"
                  else (200, {"stopped": True}))
    scratch: dict = {}
    out = mt.run_meeting(_past_the_window(scratch))
    assert isinstance(out, Wait) and out.seconds == 5
    assert ("DELETE", f"{mt.meetings_door()}/bots/google_meet/abc-defg-hij") in calls
    assert scratch["stop_failures"] == 0


@pytest.mark.parametrize("code", [404, 409])
def test_the_bot_already_being_gone_is_success_not_a_failure(monkeypatch, code):
    """A stop is idempotent: 404 and 409 both mean the bot is not in the call, which is the
    outcome. Counting them would escalate the one case that is already what we wanted."""
    _wire(monkeypatch, lambda m, u: (200, {"status": "active"}) if m == "GET" else (code, {}))
    scratch: dict = {}
    assert isinstance(mt.run_meeting(_past_the_window(scratch)), Wait)
    assert scratch["stop_failures"] == 0


def test_a_refused_delete_is_counted_and_the_count_is_durable(monkeypatch):
    """Once is not an answer — the platform may be restarting. The count lives in the reaction's
    own scratch so it survives the worker, which is what makes "three in a row" mean anything."""
    _wire(monkeypatch, lambda m, u: (200, {"status": "active"}) if m == "GET"
          else (500, {"detail": "upstream unreachable"}))
    scratch: dict = {}
    for n in (1, 2):
        out = mt.run_meeting(_past_the_window(scratch))
        assert isinstance(out, Wait), "one refusal is not a stuck bot"
        assert scratch["stop_failures"] == n


def test_repeated_refusal_blocks_with_a_deadline_and_says_what_the_platform_said(monkeypatch):
    """THE ESCALATION. A bot that will not stop is a bot that is still recording, and the only
    honest surface for that is a blocked row with a deadline — never a `Wait` nobody reads."""
    _wire(monkeypatch, lambda m, u: (200, {"status": "active"}) if m == "GET"
          else (403, {"detail": "Invalid API key"}))
    scratch: dict = {}
    for _ in range(mt.STOP_ATTEMPTS_MAX - 1):
        assert isinstance(mt.run_meeting(_past_the_window(scratch)), Wait)
    out = mt.run_meeting(_past_the_window(scratch))
    assert isinstance(out, Block)
    assert out.deadline_s == mt.STOP_DEADLINE_S
    assert "would not stop" in out.reason
    assert "abc-defg-hij" in out.reason                 # which meeting
    assert "403" in out.reason and "Invalid API key" in out.reason   # what the platform said
    assert "recording" in out.reason                   # why anybody should care


def test_a_delete_that_cannot_be_sent_at_all_counts_the_same(monkeypatch):
    """`http` raises `StepError` when the connection fails. A teardown that never left the process
    is a teardown that did not happen."""
    def answer(m, u):
        return (200, {"status": "active"}) if m == "GET" else StepError("http DELETE …: timeout")
    _wire(monkeypatch, answer)
    scratch: dict = {}
    mt.run_meeting(_past_the_window(scratch))
    assert scratch["stop_failures"] == 1


# ── the stop poll has a ceiling ──────────────────────────────────────────────────────────────────
def test_stopping_waits_but_not_forever(monkeypatch):
    """It used to be `Wait(4)` with no deadline: the meeting never completes, the reaction never
    fails, and the bot that will not leave the call is visible to nobody."""
    _wire(monkeypatch, _statuses("stopping"))
    scratch: dict = {}
    first = mt.run_meeting(_ctx(now=1_000.0, scratch=scratch))
    assert isinstance(first, Wait) and first.seconds == 4
    assert scratch["stopping_since"] == 1_000.0

    inside = mt.run_meeting(_ctx(now=1_000.0 + mt.STOP_DEADLINE_S - 1, scratch=scratch))
    assert isinstance(inside, Wait), "a slow stop is not a stuck one"

    out = mt.run_meeting(_ctx(now=1_000.0 + mt.STOP_DEADLINE_S + 1, scratch=scratch))
    assert isinstance(out, Block)
    assert out.deadline_s == mt.STOP_DEADLINE_S
    assert "has not left the call" in out.reason and "abc-defg-hij" in out.reason


# ── everything that already worked, still works ──────────────────────────────────────────────────
def test_the_ordinary_lifecycle_is_unchanged(monkeypatch):
    for state in ("requested", "joining", "awaiting_admission"):
        _wire(monkeypatch, _statuses(state))
        assert isinstance(mt.run_meeting(_ctx()), Wait)

    _wire(monkeypatch, _statuses("active"))
    out = mt.run_meeting(_ctx(now=1.0, start=0.0, transcribe_s=45.0))
    assert isinstance(out, Wait) and out.seconds == 8, "inside the window, nothing is stopped"

    _wire(monkeypatch, _statuses("completed", segments=[{"text": "hi"}, {"text": "there"}]))
    done = mt.run_meeting(_ctx())
    assert isinstance(done, Done) and done.result == {"segments": 2}

    _wire(monkeypatch, _statuses("failed", completion_reason="bot_evicted"))
    with pytest.raises(StepError) as e:
        mt.run_meeting(_ctx())
    assert "bot_evicted" in str(e.value) and e.value.retryable is False


def test_an_unrecognised_state_still_waits_and_is_not_confused_with_a_failed_read(monkeypatch):
    """The distinction the old `http-404` string destroyed: a status we do not know is a genuine
    unknown worth waiting on; a read that did not happen is not."""
    _wire(monkeypatch, _statuses("some_new_state"))
    scratch: dict = {}
    for _ in range(mt.STATUS_READ_ATTEMPTS_MAX + 3):
        assert isinstance(mt.run_meeting(_ctx(scratch=scratch)), Wait)
    assert scratch["status_unreadable"] == 0
