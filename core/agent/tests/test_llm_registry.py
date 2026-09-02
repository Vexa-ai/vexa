"""L2: env-driven adapter selection (mirrors runtime's test_backend_select) — env→class mapping,
defaults, and the fail-loud contract on unknown keys.

ONE dial since PRD decision 34. The completion half of this file tested ``VEXA_LLM_PROVIDER`` and
its three adapters; that pipeline and its port are gone."""
import pytest

from llm import LLMConfigError, harness_from_env
from llm.claude_code import ClaudeCodeHarness
from llm.codex import CodexHarness


def test_harness_defaults_to_claude_code(monkeypatch):
    monkeypatch.delenv("VEXA_RUNNER", raising=False)
    assert isinstance(harness_from_env(), ClaudeCodeHarness)


def test_harness_env_selects_codex(monkeypatch):
    monkeypatch.setenv("VEXA_RUNNER", "codex")
    assert isinstance(harness_from_env(), CodexHarness)


def test_harness_unknown_runner_fails_loud(monkeypatch):
    monkeypatch.setenv("VEXA_RUNNER", "hal9000")
    with pytest.raises(LLMConfigError) as exc:
        harness_from_env()
    assert "hal9000" in str(exc.value) and "claude-code" in str(exc.value) and "codex" in str(exc.value)


def test_blank_env_value_means_default(monkeypatch):
    monkeypatch.setenv("VEXA_RUNNER", "  ")
    assert isinstance(harness_from_env(), ClaudeCodeHarness)


def test_no_completion_port_is_reachable():
    """PRD decision 34 fence: the product must expose no second, in-product model call shape."""
    import llm

    for name in ("completion_from_env", "COMPLETION_PROVIDERS", "CompletionPort", "CompletionResult"):
        assert not hasattr(llm, name), f"llm.{name} is back — the inference pipeline must stay removed"
