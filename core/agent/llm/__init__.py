"""llm — the detached, provider-agnostic LLM + agent-harness module (see README.md).

The locked front door: product code imports ONLY these names. Vendor specifics (claude-code argv,
Anthropic headers, OpenAI dialect) never leak past this surface.
"""
from llm.errors import (
    LLMAuthError,
    LLMConfigError,
    LLMError,
    auth_error_event,
    looks_like_auth_failure,
    model_error_event,
    preflight_provider_guard,
    provider_host,
)
from llm.ports import (
    CompletionPort,
    CompletionResult,
    HarnessExec,
    HarnessPort,
    run_harness_turn,
)
from llm.registry import (
    COMPLETION_PROVIDERS,
    HARNESS_RUNNERS,
    completion_from_env,
    harness_from_env,
)

__all__ = [
    "LLMAuthError",
    "LLMConfigError",
    "LLMError",
    "auth_error_event",
    "looks_like_auth_failure",
    "model_error_event",
    "preflight_provider_guard",
    "provider_host",
    "CompletionPort",
    "CompletionResult",
    "HarnessExec",
    "HarnessPort",
    "run_harness_turn",
    "COMPLETION_PROVIDERS",
    "HARNESS_RUNNERS",
    "completion_from_env",
    "harness_from_env",
]
