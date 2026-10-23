"""registry.py — env-driven adapter selection (the ``RUNTIME_BACKEND`` factory pattern).

ONE dial: ``VEXA_RUNNER`` picks the HarnessPort adapter (workspace turns). Default ``claude-code``
— the ONLY place that vendor default string lives; worker/ code never names a runner.

There was a second dial, ``VEXA_LLM_PROVIDER``, selecting a ``CompletionPort`` for the live meeting
copilot's card beats. PRD decision 34 removed that pipeline and its three adapters with it: the
product runs no model calls of its own beside the agent, so there is one call shape left.

An unknown key fails LOUD with the known set — a typo'd runner must never limp into a confusing
downstream error. To add a runner: implement the port, add one line to the table, done.
"""
from __future__ import annotations

import os

from llm.claude_code import ClaudeCodeHarness
from llm.codex import CodexHarness
from llm.errors import LLMConfigError
from llm.openai_agent import OpenAIAgentHarness
from llm.ports import HarnessPort

HARNESS_RUNNERS: dict[str, type] = {
    "claude-code": ClaudeCodeHarness,
    "codex": CodexHarness,
    # PRD decision 37 — OURS, not a vendor CLI: an agent loop over any OpenAI-compatible endpoint,
    # so a deployment can run the service on a model it hosts itself (the CCC box serving Qwen).
    "openai-agent": OpenAIAgentHarness,
}


def harness_from_env() -> HarnessPort:
    key = (os.environ.get("VEXA_RUNNER") or "").strip() or "claude-code"
    cls = HARNESS_RUNNERS.get(key)
    if cls is None:
        raise LLMConfigError(
            f"unknown VEXA_RUNNER {key!r} — known runners: {sorted(HARNESS_RUNNERS)}"
        )
    return cls()
