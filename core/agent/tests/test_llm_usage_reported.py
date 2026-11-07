"""Every model call's usage reaches the spend cap (`control_plane/llm_budget.py`).

The harnesses put the turn's usage on its `done` (`usage`: tokens, the provider's own cost when it
reported one, how many calls). The write-back phase drops its `done`, so it hands the usage on as an
`llm-usage` event instead. agent-api's meter reads both off the unit's output stream."""
from __future__ import annotations

import json
from pathlib import Path

import fakeredis
import httpx
import pytest

from control_plane import llm_budget as lb
from llm.claude_code import parse_stream_json
from llm.openai_agent import OpenAIAgentHarness
from worker import engine


def _sse(seen, *frames):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})
    return handler


def _harness(handler):
    return OpenAIAgentHarness(transport=httpx.MockTransport(handler), base_url="http://ccc.local/v1",
                              model="some-model")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("VEXA_AGENT_STREAM", raising=False)
    monkeypatch.setenv("VEXA_AGENT_AUTO_CONTINUE_CHAT", "0")
    monkeypatch.delenv("VEXA_MOUNTS", raising=False)


def test_a_streamed_turn_asks_for_usage_and_carries_the_providers_cost_on_done(tmp_path):
    seen = []
    h = _harness(_sse(seen, {"choices": [{"delta": {"content": "Hello"}}]},
                      {"choices": [], "usage": {"prompt_tokens": 1200, "completion_tokens": 30,
                                                "cost": 0.0042}}))
    h.prepare(tmp_path)
    evs = list(h.run_turn(Path(tmp_path), "hi"))
    assert seen[0]["stream_options"] == {"include_usage": True}
    done = evs[-1]
    assert done["type"] == "done" and done["ok"] is True
    assert done["usage"] == {"prompt_tokens": 1200, "completion_tokens": 30, "cost": 0.0042,
                             "calls": 1, "model": "some-model"}
    assert [e["type"] for e in evs] == ["message-delta", "done"]       # no new event kinds


def test_a_call_that_reports_no_usage_is_estimated_never_free(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_STREAM", "0")

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant",
                                                                  "content": "answer " * 40}}]})
    h = _harness(handler)
    h.prepare(tmp_path)
    done = list(h.run_turn(Path(tmp_path), "a question"))[-1]
    u = done["usage"]
    assert u["estimated"] is True and u["calls"] == 1
    assert u["prompt_tokens"] > 0 and u["completion_tokens"] >= 60
    assert "cost" not in u


def test_an_operator_can_take_stream_options_off_through_the_extra_body(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_LLM_EXTRA_BODY", json.dumps({"stream_options": None}))
    seen = []
    h = _harness(_sse(seen, {"choices": [{"delta": {"content": "x"}}]}))
    h.prepare(tmp_path)
    list(h.run_turn(Path(tmp_path), "hi"))
    assert "stream_options" not in seen[0]


def test_the_claude_cli_result_carries_its_total_cost():
    lines = [json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}),
             json.dumps({"type": "result", "subtype": "success", "result": "hi", "session_id": "s1",
                         "total_cost_usd": 0.0731,
                         "usage": {"input_tokens": 10, "cache_creation_input_tokens": 2000,
                                   "cache_read_input_tokens": 5000, "output_tokens": 300}})]
    evs = list(parse_stream_json(lines))
    assert [e["type"] for e in evs] == ["message-delta", "done"]
    assert evs[-1]["usage"] == {"cost": 0.0731, "prompt_tokens": 7010, "completion_tokens": 300,
                                "calls": 1}


def test_the_writeback_phase_hands_its_usage_on_as_an_event():
    raw = [{"type": "message-delta", "text": "x"},
           {"type": "done", "ok": True, "reply": "x", "usage": {"cost": 0.01, "calls": 2}}]
    out = list(engine.writeback_events(iter(raw)))
    assert out == [{"type": "llm-usage", "cost": 0.01, "calls": 2, "phase": "writeback"}]


def test_the_meter_counts_a_dones_usage_and_a_writeback_usage_event():
    r = fakeredis.FakeRedis(decode_responses=True)
    b = lb.Budget(r, dict, env={})
    m = lb.UsageMeter(b, r, output_topic=lambda u: f"unit:{u}:out")
    m.watch("u1", "alice")
    for ev in ({"type": "done", "ok": True, "usage": {"cost": 0.25, "calls": 3}},
               {"type": "llm-usage", "cost": 0.05, "phase": "writeback"},
               {"type": "done", "ok": True},                                   # no usage: nothing
               {"type": "turn-complete"}):
        r.xadd("unit:u1:out", {"event": json.dumps(ev)})
    m.poll_once()
    assert b.spent("alice")[("user", "day")][0] == pytest.approx(0.30)
