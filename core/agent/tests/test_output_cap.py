"""THE OUTPUT CAP — one dial, VEXA_AGENT_MAX_OUTPUT_TOKENS, read by every harness.

The claude CLI asks for 32000 output tokens unless told otherwise, and a provider that prices that
allowance before it answers (OpenRouter) refuses a small balance with a 402 for a one-word turn.
Pinned here: claude-code receives the cap as CLAUDE_CODE_MAX_OUTPUT_TOKENS (the CLI's own variable,
checked against CLI 2.1.293: the request then carries `max_tokens` equal to it); openai-agent sends
it as the request's `max_tokens` on the wire; the runtime forwards it into every worker; and a value
that is not a positive whole number is ignored rather than sent.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from llm.claude_code import _cli_env
from llm.openai_agent import OpenAIAgentHarness
from llm.ports import MAX_OUTPUT_TOKENS_ENV, max_output_tokens


def test_unset_means_each_harness_keeps_its_own_default(monkeypatch):
    monkeypatch.delenv(MAX_OUTPUT_TOKENS_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", raising=False)
    assert max_output_tokens() is None
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in _cli_env()


def test_claude_code_receives_the_cap_as_the_clis_own_variable(monkeypatch):
    monkeypatch.setenv(MAX_OUTPUT_TOKENS_ENV, "4096")
    assert _cli_env()["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4096"


@pytest.mark.parametrize("raw", ["0", "-5", "lots", "4k"])
def test_a_value_that_is_not_a_positive_whole_number_is_not_sent(monkeypatch, raw):
    monkeypatch.setenv(MAX_OUTPUT_TOKENS_ENV, raw)
    monkeypatch.delenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", raising=False)
    assert max_output_tokens() is None
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in _cli_env()


@pytest.mark.parametrize("stream", ["1", "0"])
@pytest.mark.parametrize("cap, extra, sent", [(None, None, None), ("2048", None, 2048),
                                              ("2048", {"max_tokens": 99999}, 2048)])
def test_openai_agent_sends_the_cap_as_max_tokens_on_the_wire(tmp_path, monkeypatch, stream, cap, extra, sent):
    monkeypatch.setenv("VEXA_AGENT_STREAM", stream)
    if cap is None:
        monkeypatch.delenv(MAX_OUTPUT_TOKENS_ENV, raising=False)
    else:
        monkeypatch.setenv(MAX_OUTPUT_TOKENS_ENV, cap)
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(402, json={"error": {"message": "Insufficient credits", "code": 402}})

    h = OpenAIAgentHarness(transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1",
                           model="qwen/qwen3", extra_body=extra)
    h.prepare(tmp_path)
    list(h.run_turn(Path(tmp_path), "hi"))
    assert bodies, "the harness never called the model"
    if sent is None:
        assert "max_tokens" not in bodies[0]
    else:
        assert bodies[0]["max_tokens"] == sent


def test_the_runtime_forwards_the_cap_into_every_worker():
    import re
    src = (Path(__file__).resolve().parents[2] / "runtime" / "src" / "runtime_kernel"
           / "workload_env.py").read_text()
    forward = src[src.index("WORKER_FORWARD_ENV = ("):]
    forward = forward[: forward.index("\n)\n")]
    assert re.search(r'"VEXA_AGENT_MAX_OUTPUT_TOKENS"', forward)
