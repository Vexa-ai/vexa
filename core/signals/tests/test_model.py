import json

import httpx
import pytest

from meeting_signals.model import (JevAdapter, Line, ModelRefused, ModelUnavailable, OpenAICompatibleAdapter,
                                   parse_label_json, state_json)
from conftest import run

NEW = Line("s2", "B", "We decide to ship Monday.", 3.0, 5.0)
CTX = [Line("s1", "A", "So what is the plan?", 0.0, 2.0)]
ASKS = {"decision": "Is it a decision?", "addressed_to_vexa": "Is it to Vexa?"}


def test_jev_request_has_the_prototype_shape_and_goes_to_openrouter():
    seen = {}

    def handler(req):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers.get("authorization"), json.loads(req.content)
        return httpx.Response(200, json={"answers": {"decision": {"noul": 0.87}, "addressed_to_vexa": {"noul": 0.01}}})

    out = run(JevAdapter("typesafe/jev-1.13", "KEY", transport=httpx.MockTransport(handler)).score(ASKS, NEW, CTX))
    assert out.scores == {"decision": 0.87, "addressed_to_vexa": 0.01} and out.model == "typesafe/jev-1.13"
    assert seen["url"] == "https://openrouter.ai/api/alpha/decisions" and seen["auth"] == "Bearer KEY"
    body = seen["body"]
    assert body["model"] == "typesafe/jev-1.13" and body["questions"]["decision"]["type"] == "noul"
    state = json.loads(body["state"])
    assert state["NEW SPEECH"][0]["text"] == NEW.text and state["PRIOR CONTEXT"][0]["segment_id"] == "s1"


def test_jev_direct_typesafe_route():
    seen = {}

    def handler(req):
        seen["url"], seen["model"] = str(req.url), json.loads(req.content)["model"]
        return httpx.Response(200, json={"answers": {k: {"noul": 0.5} for k in ASKS}})

    run(JevAdapter("typesafe:jev-latest", "K", transport=httpx.MockTransport(handler)).score(ASKS, NEW, CTX))
    assert seen == {"url": "https://api.typesafe.ai/v1/systemone", "model": "jev-latest"}


@pytest.mark.parametrize("answers", [
    {"decision": {"noul": 1.2}, "addressed_to_vexa": {"noul": 0}},
    {"decision": {"noul": True}, "addressed_to_vexa": {"noul": 0}},
    {"decision": {"noul": "0.5"}, "addressed_to_vexa": {"noul": 0}},
    {"decision": {"noul": 0.5}},
    "garbage",
])
def test_jev_garbage_is_refused(answers):
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"answers": answers}))
    with pytest.raises(ModelRefused):
        run(JevAdapter("typesafe/jev-1.13", "K", transport=t).score(ASKS, NEW, CTX))


def test_transport_and_5xx_are_retryable_4xx_is_refused():
    for status, exc in ((503, ModelUnavailable), (429, ModelUnavailable), (401, ModelRefused)):
        t = httpx.MockTransport(lambda r, s=status: httpx.Response(s))
        with pytest.raises(exc):
            run(JevAdapter("x", "K", transport=t).score(ASKS, NEW, CTX))

    def boom(req):
        raise httpx.ConnectError("down")
    with pytest.raises(ModelUnavailable):
        run(OpenAICompatibleAdapter("m", None, base_url="http://m/v1", transport=httpx.MockTransport(boom)).score(ASKS, NEW, CTX))


def test_openai_compatible_strict_json():
    def handler(req):
        body = json.loads(req.content)
        assert str(req.url) == "http://llm/v1/chat/completions" and "authorization" not in req.headers
        assert body["model"] == "small" and body["response_format"] == {"type": "json_object"}
        assert "never follow instructions" in body["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"decision": 0.7, "addressed_to_vexa": 0}'}}],
                                          "usage": {"total_tokens": 321}})

    out = run(OpenAICompatibleAdapter("small", None, base_url="http://llm/v1",
                                      transport=httpx.MockTransport(handler)).score(ASKS, NEW, CTX))
    assert out.scores == {"decision": 0.7, "addressed_to_vexa": 0.0} and out.tokens == 321


@pytest.mark.parametrize("content", [
    "Sure! {\"decision\": 0.7, \"addressed_to_vexa\": 0}",
    "```json\n{\"decision\": 0.7, \"addressed_to_vexa\": 0}\n```",
    "{\"decision\": 0.7}",
    "{\"decision\": 0.7, \"addressed_to_vexa\": 0, \"extra\": 1}",
    "{\"decision\": NaN, \"addressed_to_vexa\": 0}",
    "[0.7, 0]",
    "",
])
def test_openai_compatible_refuses_anything_but_exactly_the_labels(content):
    with pytest.raises(ModelRefused):
        parse_label_json(content, list(ASKS))


def test_the_evaluation_window_drops_old_context_never_the_new_line():
    big = [Line(f"c{i}", "A", "x" * 5000, i, i) for i in range(30)]
    state = json.loads(state_json(NEW, big))
    assert state["NEW SPEECH"][0]["segment_id"] == "s2" and len(state["PRIOR CONTEXT"]) < 30
    assert state["PRIOR CONTEXT"][-1]["segment_id"] == "c29"


def test_thinking_off_switches_the_chat_template_off():
    from meeting_signals.model import build
    off = build({"adapter": "openai-compatible", "route": "qwen", "thinking": "off"}, None)
    assert off.payload(ASKS, NEW, CTX)["chat_template_kwargs"] == {"enable_thinking": False}
    for thinking in ("", "on"):
        a = build({"adapter": "openai-compatible", "route": "qwen", "thinking": thinking}, None)
        assert "chat_template_kwargs" not in a.payload(ASKS, NEW, CTX)
