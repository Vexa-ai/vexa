"""registry.py — env-driven adapter selection (the ``RUNTIME_BACKEND`` factory pattern).

Two independent dials:
- ``VEXA_LLM_PROVIDER`` picks the CompletionPort adapter (card beats). Default ``openai-compat``.
- ``VEXA_RUNNER`` picks the HarnessPort adapter (workspace turns). Default ``claude-code`` — the
  ONLY place that vendor default string lives; worker/ code never names a runner.

Unknown keys fail LOUD with the known set — a typo'd provider must never limp into a confusing
downstream error. To add a provider: implement the port, add one line to the table, done.
"""
from __future__ import annotations

import os

from llm.anthropic_api import AnthropicCompletion
from llm.claude_cli import ClaudeCliCompletion
from llm.claude_code import ClaudeCodeHarness
from llm.errors import LLMConfigError
from llm.openai_compat import OpenAICompatCompletion
from llm.ports import CompletionPort, HarnessPort

COMPLETION_PROVIDERS: dict[str, type] = {
    "openai-compat": OpenAICompatCompletion,
    "anthropic": AnthropicCompletion,
    # Subscription-credential deployments: beats ride the claude CLI (no API key needed).
    "claude-cli": ClaudeCliCompletion,
}

HARNESS_RUNNERS: dict[str, type] = {
    "claude-code": ClaudeCodeHarness,
}


def completion_from_env() -> CompletionPort:
    key = (os.environ.get("VEXA_LLM_PROVIDER") or "").strip() or "openai-compat"
    cls = COMPLETION_PROVIDERS.get(key)
    if cls is None:
        raise LLMConfigError(
            f"unknown VEXA_LLM_PROVIDER {key!r} — known providers: {sorted(COMPLETION_PROVIDERS)}"
        )
    return cls()


def harness_from_env() -> HarnessPort:
    key = (os.environ.get("VEXA_RUNNER") or "").strip() or "claude-code"
    cls = HARNESS_RUNNERS.get(key)
    if cls is None:
        raise LLMConfigError(
            f"unknown VEXA_RUNNER {key!r} — known runners: {sorted(HARNESS_RUNNERS)}"
        )
    return cls()
