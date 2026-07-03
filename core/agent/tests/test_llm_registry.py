"""L2: env-driven adapter selection (mirrors runtime's test_backend_select) — env→class mapping,
defaults, and the fail-loud contract on unknown keys."""
import pytest

from llm import LLMConfigError, completion_from_env, harness_from_env
from llm.anthropic_api import AnthropicCompletion
from llm.claude_code import ClaudeCodeHarness
from llm.openai_compat import OpenAICompatCompletion


def test_completion_defaults_to_openai_compat(monkeypatch):
    monkeypatch.delenv("VEXA_LLM_PROVIDER", raising=False)
    assert isinstance(completion_from_env(), OpenAICompatCompletion)


def test_completion_env_selects_anthropic(monkeypatch):
    monkeypatch.setenv("VEXA_LLM_PROVIDER", "anthropic")
    assert isinstance(completion_from_env(), AnthropicCompletion)


def test_completion_unknown_provider_fails_loud(monkeypatch):
    monkeypatch.setenv("VEXA_LLM_PROVIDER", "gpt-magic")
    with pytest.raises(LLMConfigError) as exc:
        completion_from_env()
    assert "gpt-magic" in str(exc.value) and "openai-compat" in str(exc.value)


def test_harness_defaults_to_claude_code(monkeypatch):
    monkeypatch.delenv("VEXA_RUNNER", raising=False)
    assert isinstance(harness_from_env(), ClaudeCodeHarness)


def test_harness_unknown_runner_fails_loud(monkeypatch):
    monkeypatch.setenv("VEXA_RUNNER", "hal9000")
    with pytest.raises(LLMConfigError) as exc:
        harness_from_env()
    assert "hal9000" in str(exc.value) and "claude-code" in str(exc.value)


def test_blank_env_values_mean_default(monkeypatch):
    monkeypatch.setenv("VEXA_LLM_PROVIDER", "")
    monkeypatch.setenv("VEXA_RUNNER", "  ")
    assert isinstance(completion_from_env(), OpenAICompatCompletion)
    assert isinstance(harness_from_env(), ClaudeCodeHarness)
