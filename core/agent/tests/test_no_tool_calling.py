"""L2: an endpoint that does not do function calling ends the turn with a typed fault.

Every turn is a tool loop. Before this, a model served without tool calling (vLLM without
``--enable-auto-tool-choice``, LiteLLM with ``drop_params`` stripping ``tools``, a server that does not
parse the model's call markup) either failed as ``refused`` — "check the model name", the wrong dial —
or, worse, ANSWERED: never shown its tools, the model told the person it had none. Each test below
fails on the code before ``model-provider`` / ``no_tool_calling`` existed.

The stub server is ``httpx.MockTransport``; the check request is the one whose only tool is
``vexa_tool_check``.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from llm import faults
from llm import openai_agent
from llm.openai_agent import OpenAIAgentHarness

pytestmark = pytest.mark.real_tool_check

NO_TOOLS_REPLY = "I don't have any tools available, so I can't join the meeting."


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_STREAM", "0")
    monkeypatch.setenv("VEXA_AGENT_AUTO_CONTINUE_CHAT", "0")
    monkeypatch.delenv("VEXA_MOUNTS", raising=False)
    # every test starts with nothing proven about any endpoint
    monkeypatch.setattr(openai_agent, "_TOOL_CALLING_OK", set())


def _is_check(body: dict) -> bool:
    return [t["function"]["name"] for t in body.get("tools") or []] == ["vexa_tool_check"]


def _ok(msg: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": msg, "finish_reason": "stop"}]})


def _harness(handler) -> OpenAIAgentHarness:
    return OpenAIAgentHarness(base_url="http://llm.internal/v1", model="glm-4",
                              transport=httpx.MockTransport(handler))


def _turn(h: OpenAIAgentHarness, work: Path, prompt: str = "send the bot to my meeting") -> list[dict]:
    h.prepare(work)
    return list(h.run_turn(work, prompt, allowed_tools=["Read"]))


def _fault(evs: list[dict]) -> dict:
    done = evs[-1]
    assert done["type"] == "done" and done["ok"] is False, done
    return done["fault"]


def test_tools_dropped_by_a_proxy_is_a_fault_not_a_claim_of_no_tools(tmp_path):
    """LiteLLM `drop_params`: the tools never reach the model, which answers that it has none."""
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        return _ok({"role": "assistant", "content": NO_TOOLS_REPLY})

    evs = _turn(_harness(handler), tmp_path)
    fault = _fault(evs)
    assert fault["source"] == "model-provider" and fault["kind"] == "no_tool_calling"
    assert fault["provider"] == "llm.internal" and fault["model"] == "glm-4"
    assert "tool calling" in fault["remedy"]
    # the person hears the fault, not the model's claim
    assert NO_TOOLS_REPLY not in evs[-1]["reply"]
    assert "does not do tool calling" in evs[-1]["reply"]
    # the check forced a call
    check = next(b for b in seen if _is_check(b))
    assert check["tool_choice"] == "required"


def test_vllm_without_auto_tool_choice_is_no_tool_calling_not_refused(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"object": "error", "message":
                                         '"auto" tool choice requires --enable-auto-tool-choice and '
                                         "--tool-call-parser to be set", "code": 400})

    fault = _fault(_turn(_harness(handler), tmp_path))
    assert fault["kind"] == "no_tool_calling" and fault["status"] == 400


def test_litellm_unsupported_params_is_no_tool_calling(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message":
                              "litellm.UnsupportedParamsError: openai does not support parameters: "
                              "['tools', 'tool_choice'], for model=glm-4. To drop these, set "
                              "`litellm.drop_params=True`", "code": "400"}})

    fault = _fault(_turn(_harness(handler), tmp_path))
    assert fault["kind"] == "no_tool_calling"


def test_a_tool_call_written_as_text_is_no_tool_calling(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        return _ok({"role": "assistant", "content":
                    '<tool_call>\n{"name": "request_meeting_bot", "arguments": {}}\n</tool_call>'})

    fault = _fault(_turn(_harness(handler), tmp_path))
    assert fault["kind"] == "no_tool_calling" and "as text" in fault["detail"]


def test_a_streamed_error_frame_refusing_tools_is_no_tool_calling(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_STREAM", "1")

    def handler(req: httpx.Request) -> httpx.Response:
        frame = {"error": {"message": "Function calling is not supported by this model", "code": 400}}
        return httpx.Response(200, text=f"data: {json.dumps(frame)}\n\ndata: [DONE]\n\n",
                              headers={"content-type": "text/event-stream"})

    fault = _fault(_turn(_harness(handler), tmp_path))
    assert fault["kind"] == "no_tool_calling"


def test_an_endpoint_that_calls_tools_keeps_the_reply_and_is_checked_once(tmp_path):
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        if _is_check(body):
            return _ok({"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "vexa_tool_check", "arguments": "{}"}}]})
        return _ok({"role": "assistant", "content": "Hello! What meeting should I join?"})

    h = _harness(handler)
    first = _turn(h, tmp_path, "hi")
    assert first[-1]["ok"] is True and first[-1]["reply"] == "Hello! What meeting should I join?"
    second = list(h.run_turn(tmp_path, "and again", allowed_tools=["Read"]))
    assert second[-1]["ok"] is True
    assert sum(1 for b in seen if _is_check(b)) == 1          # proven once per endpoint and model


def test_a_check_that_proves_nothing_never_faults_the_turn(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if _is_check(json.loads(req.content)):
            return httpx.Response(503, text="upstream overloaded")
        return _ok({"role": "assistant", "content": "Hello."})

    evs = _turn(_harness(handler), tmp_path, "hi")
    assert evs[-1]["ok"] is True and evs[-1]["reply"] == "Hello."
    assert "fault" not in evs[-1]


def test_a_server_without_required_is_asked_again_with_auto(tmp_path):
    choices: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if not _is_check(body):
            return _ok({"role": "assistant", "content": "Hello."})
        choices.append(body["tool_choice"])
        if body["tool_choice"] == "required":
            return httpx.Response(400, json={"error": {"message": "invalid tool_choice value"}})
        return _ok({"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "vexa_tool_check", "arguments": "{}"}}]})

    evs = _turn(_harness(handler), tmp_path, "hi")
    assert evs[-1]["ok"] is True and choices == ["required", "auto"]


def test_a_turn_without_tools_is_never_checked(tmp_path):
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return _ok({"role": "assistant", "content": "Hello."})

    h = _harness(handler)
    h.prepare(tmp_path)
    # an allow-set naming nothing this harness attaches: no tools are offered at all
    evs = list(h.run_turn(tmp_path, "hi", allowed_tools=["NoSuchTool"]))
    assert evs[-1]["ok"] is True and len(seen) == 1 and "tools" not in seen[0]


@pytest.mark.parametrize("text", [
    '"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set',
    "litellm.UnsupportedParamsError: openai does not support parameters: ['tools'], for model=x",
    "registry.ollama.ai/library/gemma:2b does not support tools",
    "Function calling is not supported by this model",
])
def test_the_servers_own_words_for_unsupported_tools(text):
    assert faults.tools_refused(text)


@pytest.mark.parametrize("text", ["invalid model id", "rate limit exceeded", "context length exceeded",
                                  "Your credit balance is too low"])
def test_other_refusals_are_not_read_as_no_tool_calling(text):
    assert not faults.tools_refused(text)


def test_the_fault_is_the_contracts_shape():
    jsonschema = pytest.importorskip("jsonschema")
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "contracts/unit.v1/unit.schema.json").read_text())
    f = faults.no_tool_calling(provider="llm.internal", model="glm-4", detail="no tools").as_dict()
    jsonschema.validate(f, {**schema, "$ref": "#/$defs/Fault"})
    assert f["kind"] in faults.KINDS


def test_an_empty_stream_to_a_request_with_tools_is_checked_for_tool_calling(tmp_path, monkeypatch):
    """A server whose tool parser swallowed a call it was never told to expect streams nothing."""
    monkeypatch.setenv("VEXA_AGENT_STREAM", "1")

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if _is_check(body):
            return _ok({"role": "assistant", "content": NO_TOOLS_REPLY})
        empty = {"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]}
        return httpx.Response(200, text=f"data: {json.dumps(empty)}\n\ndata: [DONE]\n\n",
                              headers={"content-type": "text/event-stream"})

    fault = _fault(_turn(_harness(handler), tmp_path))
    assert fault["kind"] == "no_tool_calling"


def test_an_empty_stream_from_an_endpoint_that_calls_tools_stays_the_empty_completion(tmp_path,
                                                                                    monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_STREAM", "1")

    def handler(req: httpx.Request) -> httpx.Response:
        if _is_check(json.loads(req.content)):
            return _ok({"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "vexa_tool_check", "arguments": "{}"}}]})
        return httpx.Response(200, text="data: [DONE]\n\n", headers={"content-type": "text/event-stream"})

    evs = _turn(_harness(handler), tmp_path)
    assert evs[-1]["ok"] is False and "fault" not in evs[-1]
    assert "streamed no content" in evs[-1]["reply"]
