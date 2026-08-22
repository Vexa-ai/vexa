"""L2: the orcarouter completion adapter against a fake transport — default-host wiring, request
shape (OpenAI-compatible URL, Bearer auth, messages), response parsing, error taxonomy, and the
env/override fallback chain. No network."""
import json

import httpx
import pytest

from llm import LLMAuthError, LLMConfigError, LLMError
from llm.orca import OrcaRouterCompletion, _DEFAULT_BASE


def _adapter(handler, **kw):
    kw.setdefault("api_key", "sk-orca-test")
    kw.setdefault("model", "orcarouter/auto")
    return OrcaRouterCompletion(transport=httpx.MockTransport(handler), **kw)


def test_default_base_and_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "polished"}}]})

    result = _adapter(handler).complete("clean these lines", system="you are a copilot")
    assert result.text == "polished"
    assert result.model == "orcarouter/auto"
    assert seen["url"] == f"{_DEFAULT_BASE}/chat/completions"  # default host, nothing to type
    assert seen["auth"] == "Bearer sk-orca-test"
    assert seen["body"]["model"] == "orcarouter/auto"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "you are a copilot"}
    assert seen["body"]["messages"][1] == {"role": "user", "content": "clean these lines"}


def test_explicit_base_url_overrides_default():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://self-hosted.example/v1/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    adapter = OrcaRouterCompletion(base_url="https://self-hosted.example/v1", api_key="k",
                                   model="m", transport=httpx.MockTransport(handler))
    assert adapter.complete("p").text == "ok"


def test_per_call_model_overrides_default():
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["model"] == "beat-model"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    assert _adapter(handler).complete("p", model="beat-model").model == "beat-model"


def test_401_raises_auth_error():
    handler = lambda request: httpx.Response(401, text="User not found.")  # noqa: E731
    with pytest.raises(LLMAuthError) as exc:
        _adapter(handler).complete("p")
    assert "401" in str(exc.value)


def test_5xx_raises_llm_error():
    handler = lambda request: httpx.Response(503, text="overloaded")  # noqa: E731
    with pytest.raises(LLMError):
        _adapter(handler).complete("p")


def test_missing_model_fails_loud(monkeypatch):
    monkeypatch.delenv("VEXA_LLM_MODEL", raising=False)
    with pytest.raises(LLMConfigError):
        OrcaRouterCompletion(model="").complete("p")


def test_constructor_args_win_over_env(monkeypatch):
    monkeypatch.setenv("VEXA_LLM_BASE_URL", "https://env.example/v1")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    adapter = OrcaRouterCompletion(base_url="https://ctor.example/v1", api_key="k", model="m",
                                   transport=httpx.MockTransport(handler))
    assert adapter.complete("p").text == "ok"
    assert seen["url"] == "https://ctor.example/v1/chat/completions"
