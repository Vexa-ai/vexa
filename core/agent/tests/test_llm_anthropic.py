"""L2: the anthropic completion adapter against a fake transport — Messages-API request shape
(x-api-key, anthropic-version, max_tokens, system as top-level), text-block parsing, 401 taxonomy."""
import json

import httpx
import pytest

from llm import LLMAuthError, LLMConfigError
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
