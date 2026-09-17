"""L2: the anthropic completion adapter against a fake transport — Messages-API request shape
(x-api-key, anthropic-version, max_tokens, system as top-level), text-block parsing, 401 taxonomy."""
import json

import httpx
import pytest

from llm import LLMAuthError, LLMConfigError, LLMError
from llm.anthropic_api import AnthropicCompletion


def _adapter(handler, **kw):
    kw.setdefault("api_key", "sk-ant-test")
    kw.setdefault("model", "some-model")
    return AnthropicCompletion(transport=httpx.MockTransport(handler), **kw)


def test_request_shape_and_parse(monkeypatch):
    monkeypatch.setenv("VEXA_LLM_MAX_TOKENS", "2048")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        seen["version"] = request.headers.get("anthropic-version")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "pol"},
                                                     {"type": "text", "text": "ished"}]})

    result = _adapter(handler).complete("clean", system="copilot")
    assert result.text == "polished"
    assert seen["url"] == "https://api.anthropic.com/v1/messages"  # default base
    assert seen["key"] == "sk-ant-test"
    assert seen["version"] == "2023-06-01"
    assert seen["body"]["max_tokens"] == 2048
    assert seen["body"]["system"] == "copilot"
    assert seen["body"]["messages"] == [{"role": "user", "content": "clean"}]


def test_401_raises_auth_error():
    handler = lambda request: httpx.Response(401, json={"error": {"type": "authentication_error"}})  # noqa: E731
    with pytest.raises(LLMAuthError):
        _adapter(handler).complete("p")


def test_missing_model_fails_loud(monkeypatch):
    monkeypatch.delenv("VEXA_LLM_MODEL", raising=False)
    with pytest.raises(LLMConfigError):
        AnthropicCompletion(model="").complete("p")


# ── #1666: what the endpoint ANSWERED, not just what it returned ──────────────────────────────

def test_openai_body_at_200_fails_by_name_not_empty_text():
    """The reported cause: a gateway answering the OpenAI shape at /v1/messages. This used to
    return CompletionResult(text='') — a silent empty beat with no error anywhere."""
    handler = lambda request: httpx.Response(  # noqa: E731
        200, json={"object": "chat.completion", "choices": [{"message": {"content": "pong"}}]})
    with pytest.raises(LLMError) as exc:
        _adapter(handler).complete("p")
    assert "DIALECT MISMATCH" in str(exc.value)


def test_html_error_page_at_200_names_the_gateway():
    handler = lambda request: httpx.Response(  # noqa: E731
        200, text="<html>blocked</html>", headers={"content-type": "text/html"})
    with pytest.raises(LLMError) as exc:
        _adapter(handler).complete("p")
    assert "CDN or gateway" in str(exc.value)


# ── #1666: a rejected credential is terminal ──────────────────────────────────────────────────

def test_401_is_not_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(401, json={"error": {"message": "Missing API key"}})

    with pytest.raises(LLMAuthError) as exc:
        _adapter(handler).complete("p")
    assert len(calls) == 1                      # one request, no retry loop
    assert "x-api-key" in str(exc.value)        # names the header the key was sent as
    assert "Not retried" in str(exc.value)


# ── #1667: a provider-required extra header ───────────────────────────────────────────────────

def test_extra_headers_are_sent_and_never_override_auth():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    adapter = _adapter(handler, extra_headers="x-provider-session: abc\nx-api-key: hijack")
    assert adapter.complete("p").text == "ok"
    assert seen["x-provider-session"] == "abc"
    assert seen["x-api-key"] == "sk-ant-test"   # config extras never replace the credential


def test_extra_headers_from_env(monkeypatch):
    monkeypatch.setenv("VEXA_LLM_EXTRA_HEADERS", "x-route: eu")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-route") == "eu"
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    assert AnthropicCompletion(api_key="k", model="m",
                               transport=httpx.MockTransport(handler)).complete("p").text == "ok"
