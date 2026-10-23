"""A turn that hears the same refusal twice stops asking (`llm/refusal_guard.py`).

A worker with nobody in the loop is refused every mail, calendar and connection verb, and nothing
inside the turn can change that. The second identical {tool, reason} refusal gets a stop note; the
third ends the turn and is reported ONCE (`worker/friction.py` `refusal_stop`), not per call.
"""
from __future__ import annotations

import json

import pytest

from llm import jobs as llm_jobs
from llm.refusal_guard import RefusalGuard, refusal_reason, tell_your_person
from tests.test_llm_openai_agent import _events, _harness, _msg, _server
from worker.friction import scan_turn, turn_refusal

TELL = "This ran without you present; ask in chat."
REFUSAL = json.dumps({"status": "refused", "reason": "human_session_required",
                      "instruction": "stop", "remedy": "ask_in_chat", "tell_your_person": TELL})
RENDERED = "HTTP 403 human_session_required\n" + REFUSAL      # the gateway MCP's rendering


def test_only_a_body_that_says_it_is_a_refusal_counts():
    assert refusal_reason(RENDERED) == "human_session_required"
    assert refusal_reason('{"refused": "out_of_scope", "workspace": "x"}') == "out_of_scope"
    assert refusal_reason('{"status": "refused", "reason": "quota_exceeded"}') == "quota_exceeded"
    for not_one in ("HTTP 404 Not Found", "no such file", '{"reason": "timeout"}', "", None):
        assert refusal_reason(not_one) == "", not_one
    assert tell_your_person(RENDERED) == TELL


def test_the_second_identical_refusal_gets_a_stop_note_and_the_third_ends_the_turn():
    g = RefusalGuard()
    out, act = g.observe("mail_inbox", False, RENDERED)
    assert act == "" and out == RENDERED
    out, act = g.observe("calendar_events", False, RENDERED)   # another tool: its own count
    assert act == ""
    out, act = g.observe("mail_inbox", False, RENDERED)
    assert act == "stop" and "Do not call it again" in out and "tell_your_person" in out
    _, act = g.observe("mail_inbox", False, RENDERED)
    assert act == "end" and g.ended == {"tool": "mail_inbox", "reason": "human_session_required",
                                        "count": 3}
    assert g.ended_reply() == TELL


def test_a_success_or_an_ordinary_error_is_never_counted():
    g = RefusalGuard()
    for _ in range(5):
        assert g.observe("Read", False, "no such file")[1] == ""
        assert g.observe("mail_inbox", True, REFUSAL)[1] == ""
    assert g.counts == {}


@pytest.fixture(autouse=True)
def _blocking(monkeypatch):
    """The scripted model plays whole messages (`test_llm_openai_agent._blocking`)."""
    monkeypatch.setenv("VEXA_AGENT_STREAM", "0")
    monkeypatch.setenv("VEXA_AGENT_AUTO_CONTINUE_CHAT", "0")
    monkeypatch.delenv("VEXA_MOUNTS", raising=False)


def _refusing_harness(monkeypatch, script):
    llm_jobs.mark_turn_kind("")
    h = _harness(_server(script))
    monkeypatch.setattr(type(h), "_exec_tool", lambda self, call, *a, **k: (False, RENDERED))
    return h


def test_the_harness_injects_the_stop_on_the_second_and_ends_on_the_third(tmp_path, monkeypatch):
    """The model keeps asking for the inbox; the harness stops it at three, answers every call it
    made, says the refusal's own sentence, and reports `refusal-repeated` once."""
    keep_asking = _msg("checking", [("c1", "mail_inbox", {"limit": 3})])
    h = _refusing_harness(monkeypatch, [keep_asking])
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "check my email")
    results = [e for e in evs if e["type"] == "tool-result"]
    assert len(results) == 3, "the turn ended on the third identical refusal"
    repeated = [e for e in evs if e["type"] == "refusal-repeated"]
    assert repeated == [{"type": "refusal-repeated", "tool": "mail_inbox",
                         "reason": "human_session_required", "count": 3}]
    done = evs[-1]
    assert done["type"] == "done" and done["ok"] is False
    assert "refused 3 times" in done["reason"] and "act" not in done
    assert done["reply"] == "checking"          # the model's own words stand when it had some
    # one friction record for the whole thing, not one per refused call
    records = scan_turn([e for e in evs if e["type"] in ("tool-call", "tool-result", "refusal-repeated")])
    assert len(records) == 1 and "refused 3 times" in records[0]["happened"]


def test_a_turn_that_stops_after_one_refusal_is_untouched(tmp_path, monkeypatch):
    h = _refusing_harness(monkeypatch, [_msg("", [("c1", "mail_inbox", {})]),
                                        _msg("I could not read your inbox from here.")])
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "check my email")
    assert not [e for e in evs if e["type"] == "refusal-repeated"]
    assert evs[-1]["ok"] is True and evs[-1]["reply"].startswith("I could not")


def test_the_turn_refusal_rides_on_any_harness_s_results():
    """`worker.engine` stamps `done.refused` from the summaries, which every harness emits."""
    events = [{"type": "tool-call", "callId": "a", "tool": "mcp__vexa__mail_inbox"},
              {"type": "tool-result", "callId": "a", "ok": False,
               "summary": "HTTP 403 human_session_required {\"status\":\"refused\""},
              {"type": "tool-result", "callId": "b", "ok": False, "summary": "no such file"}]
    assert turn_refusal(events) == {"tool": "mcp__vexa__mail_inbox",
                                    "reason": "human_session_required", "count": 1}
    assert turn_refusal([{"type": "tool-result", "ok": True, "summary": "fine"}]) is None
