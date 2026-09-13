"""A TURN THAT WAS NOT DISPATCHED IS NOT A TURN THAT IS RUNNING — P21(b), and P18 beside it.

`dispatch_turn` ended:

    try:
        http("POST", f"{agent_door()}/api/chat", headers, body, timeout=3)
    except Exception:  # stream-open timeout: the turn IS running
        pass
    return base

The comment is right about ONE exception. `/api/chat` is an SSE stream that stays open for the
whole turn, so a client read-timeout genuinely means the turn started — that is the 2026-08-23
double-dispatch lesson, and it is preserved exactly here. It is wrong about every other one: a
connection refused, a DNS failure, a 401, a 404, a 500 all mean nothing is running, and the
function returned a history baseline as though something were. `collect_reply` then waited for a
reply that was never coming, for as long as its caller allowed, and the reaction reported the
pending state of a turn that had never been dispatched.

The status was not checked at all, either: `http` RETURNS `(code, body)` for any HTTP answer and
only raises for transport failures, so a 500 from agent-api never even reached the `except`.

P18's half: the one swallow that stays is LOGGED, with `source` and `kind`, so a degradation
nobody can see is not indistinguishable from the healthy path it degrades to.
"""
from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows import StepError  # noqa: E402
from flows_steps import agent as ag  # noqa: E402
from flows_steps import common  # noqa: E402


@pytest.fixture(autouse=True)
def _door(monkeypatch):
    monkeypatch.setattr(ag, "agent_door", lambda: "http://agent-api.test")


def _wire(monkeypatch, *, post):
    """History answers two turns; the POST answers whatever the test says."""
    calls: list = []

    def fake_http(method, url, headers, body=None, timeout=20):
        calls.append((method, url))
        if url.endswith("/history"):
            return 200, {"turns": [{"role": "user"}, {"role": "agent"}]}
        out = post(method, url)
        if isinstance(out, BaseException):
            raise out
        return out

    monkeypatch.setattr(ag, "http", fake_http)
    return calls


def _timeout_stepe() -> StepError:
    """What `common.http` actually raises on a read timeout: a StepError whose `__context__` is the
    underlying `TimeoutError`. Built by running the real wrapper over a raiser, so this fixture
    cannot drift from the shape the product produces."""
    try:
        raise TimeoutError("timed out")
    except TimeoutError:
        try:
            raise StepError("http POST http://agent-api.test/api/chat: TimeoutError: timed out")
        except StepError as e:
            return e


# ── the one exception that means success ─────────────────────────────────────────────────────────
def test_a_stream_open_timeout_is_still_a_dispatched_turn(monkeypatch, capsys):
    """THE 2026-08-23 LESSON, kept. `/api/chat` holds the connection for the whole turn, so the
    client timing out is what a working dispatch looks like from here — and re-dispatching on it
    is how the same turn ran twice."""
    _wire(monkeypatch, post=lambda m, u: _timeout_stepe())
    assert ag.dispatch_turn("7", "main", "go") == 2, "the baseline is still returned"
    out = capsys.readouterr().out
    assert "swallowed" in out and "flows_steps.agent.dispatch_turn" in out, (
        "the one swallow that stays must be visible (P18) — a degradation nobody can see cannot "
        "be told apart from the healthy path when this heuristic is one day wrong")


def test_a_wrapped_timeout_is_recognised_too(monkeypatch):
    """`urllib` may deliver the same timeout inside a `URLError`, and the recogniser reads the
    CAUSE chain rather than the formatted message — a message is not a contract."""
    try:
        raise urllib.error.URLError(TimeoutError("timed out"))
    except urllib.error.URLError:
        try:
            raise StepError("http POST …: URLError: <urlopen error timed out>")
        except StepError as wrapped:
            err = wrapped
    _wire(monkeypatch, post=lambda m, u: err)
    assert ag.dispatch_turn("7", "main", "go") == 2


# ── everything else is a turn that did not happen ────────────────────────────────────────────────
def test_a_refused_connection_is_not_a_running_turn(monkeypatch):
    """THE REGRESSION. This returned a baseline, and `collect_reply` then waited for a reply from
    a turn that was never dispatched — a dispatch reported as a run."""
    try:
        raise ConnectionRefusedError(61, "Connection refused")
    except ConnectionRefusedError:
        try:
            raise StepError("http POST …: ConnectionRefusedError: [Errno 61] Connection refused")
        except StepError as e:
            err = e
    _wire(monkeypatch, post=lambda m, u: err)
    with pytest.raises(StepError) as caught:
        ag.dispatch_turn("7", "main", "go")
    assert "was not dispatched" in str(caught.value)
    assert "7/main" in str(caught.value), "the reason names the person and the session"
    assert caught.value.retryable is True


@pytest.mark.parametrize("code,retryable", [(401, False), (403, False), (404, False),
                                            (422, False), (429, True), (500, True), (503, True)])
def test_a_non_2xx_answer_raises_and_says_whether_it_is_worth_retrying(monkeypatch, code, retryable):
    """The status was never looked at: `http` RETURNS a code for any HTTP answer and only raises
    for transport failures, so a 500 from agent-api did not even reach the old `except`.

    5xx and 429 are the platform having a moment; a 4xx is a fact about this call, and retrying it
    delays the reaction without changing the answer."""
    _wire(monkeypatch, post=lambda m, u: (code, {"detail": "no"}))
    with pytest.raises(StepError) as e:
        ag.dispatch_turn("7", "main", "go")
    assert f"answered {code}" in str(e.value)
    assert e.value.retryable is retryable


@pytest.mark.parametrize("code", [200, 201, 202, 204])
def test_a_2xx_answer_is_the_ordinary_path(monkeypatch, code):
    calls = _wire(monkeypatch, post=lambda m, u: (code, {"ok": True}))
    assert ag.dispatch_turn("7", "main", "go") == 2
    assert ("POST", "http://agent-api.test/api/chat") in calls


def test_the_room_still_rides_the_same_post(monkeypatch):
    """The half a stricter dispatch would break: the meeting-room fields and the internal-tier
    header are unchanged."""
    sent: list = []

    def fake_http(method, url, headers, body=None, timeout=20):
        if url.endswith("/history"):
            return 200, {"turns": []}
        sent.append((headers, body))
        return 200, {"ok": True}

    monkeypatch.setattr(ag, "http", fake_http)
    monkeypatch.setattr(ag, "require_internal_secret", lambda: "internal-tier-secret")
    ag.dispatch_turn("7", "main", "go",
                     room={"meeting_id": 41, "read": ["a@b.test"],
                           "names": {"a@b.test": "A B"}, "read_max": 12})
    headers, body = sent[0]
    assert headers["X-Internal-Secret"] == "internal-tier-secret"
    assert body["room_meeting_id"] == "41"
    assert body["room_participants"] == ["a@b.test"]
    assert body["room_participant_names"] == {"a@b.test": "A B"}
    assert body["room_read_max"] == 12


# ── the swallow log itself (P18) ─────────────────────────────────────────────────────────────────
def test_a_swallow_names_who_swallowed_it_and_what_it_swallowed(capsys):
    """Two fields rather than one sentence, so a reader can count occurrences of a kind without
    parsing prose. The instance that made this worth having: a Postgres outage surfaced to an
    operator as "flow retired by deploy" — a different event, a different owner, a different fix."""
    common.swallowed("flows_steps.agent.head_sha", "desk history unreadable",
                     RuntimeError("boom"), uid="7")
    line = capsys.readouterr().out.strip()
    assert line.startswith("warning: swallowed ")
    assert "source=flows_steps.agent.head_sha" in line
    assert "kind='desk history unreadable'" in line
    assert "uid='7'" in line and "RuntimeError: boom" in line


def test_a_swallow_with_no_exception_still_logs(capsys):
    common.swallowed("flows_steps.agent.head_subjects", "desk history unreadable", None, http=503)
    line = capsys.readouterr().out.strip()
    assert "http=503" in line and "source=flows_steps.agent.head_subjects" in line


def test_the_desk_probes_say_when_they_could_not_read(monkeypatch, capsys):
    """`head_sha`/`head_subjects` degrade to "" and [] on purpose — a probe that cannot read must
    not be able to manufacture a difference — but a silent degrade means the detector reports
    "nothing changed" for "I could not look", which are different facts about a meeting."""
    monkeypatch.setattr(ag, "http", lambda *a, **k: (503, {"detail": "restarting"}))
    assert ag.head_sha("7") == ""
    assert ag.head_subjects("7") == []
    out = capsys.readouterr().out
    assert out.count("warning: swallowed") == 2
    assert "flows_steps.agent.head_sha" in out and "flows_steps.agent.head_subjects" in out
