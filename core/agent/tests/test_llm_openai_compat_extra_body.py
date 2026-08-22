"""VEXA_LLM_EXTRA_BODY — server-specific parameters the OpenAI dialect cannot express.

The load-bearing case is a self-hosted vLLM serving Qwen: in thinking mode it returns 0% valid
JSON (it spends the whole budget reasoning), and 100% with
``{"chat_template_kwargs": {"enable_thinking": false}}``. Without this passthrough that deployment
cannot serve structured output at all, so these tests pin the behaviour rather than the plumbing.
"""
from __future__ import annotations

import json

import httpx
import pytest

from llm.errors import LLMConfigError
from llm.openai_compat import OpenAICompatCompletion


def _capture() -> tuple[list[dict], httpx.MockTransport]:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    return seen, httpx.MockTransport(handler)


def test_extra_body_is_merged_into_the_request():
    seen, transport = _capture()
    c = OpenAICompatCompletion(base_url="http://vllm:8001/v1", model="qwen3.8-27b",
                               extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                               transport=transport)
    c.complete("summarise")
    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen[0]["model"] == "qwen3.8-27b"


def test_extra_body_cannot_override_model_or_messages():
    seen, transport = _capture()
    c = OpenAICompatCompletion(base_url="http://vllm:8001/v1", model="real-model",
                               extra_body={"model": "hijacked", "messages": [{"role": "user", "content": "x"}]},
                               transport=transport)
    c.complete("the real prompt")
    assert seen[0]["model"] == "real-model"
    assert seen[0]["messages"][-1]["content"] == "the real prompt"


def test_absent_extra_body_changes_nothing():
    seen, transport = _capture()
    c = OpenAICompatCompletion(base_url="http://vllm:8001/v1", model="m", transport=transport)
    c.complete("hello")
    assert set(seen[0]) == {"model", "messages"}


def test_env_supplies_extra_body(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VEXA_LLM_EXTRA_BODY", '{"chat_template_kwargs": {"enable_thinking": false}}')
    seen, transport = _capture()
    c = OpenAICompatCompletion(base_url="http://vllm:8001/v1", model="m", transport=transport)
    c.complete("hello")
    assert seen[0]["chat_template_kwargs"]["enable_thinking"] is False


def test_malformed_extra_body_fails_loudly(monkeypatch: pytest.MonkeyPatch):
    """A deployment that believes it disabled thinking and did not must not discover it as bad output."""
    monkeypatch.setenv("VEXA_LLM_EXTRA_BODY", "{not json")
    with pytest.raises(LLMConfigError):
        OpenAICompatCompletion(base_url="http://vllm:8001/v1", model="m")


def test_non_object_extra_body_is_refused(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VEXA_LLM_EXTRA_BODY", '["a", "list"]')
    with pytest.raises(LLMConfigError):
        OpenAICompatCompletion(base_url="http://vllm:8001/v1", model="m")
