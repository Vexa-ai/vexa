"""gate:access deny vectors — what the model-driven harness subprocess may NOT inherit.

The worker process holds credentials it uses itself (its redis grant, the per-dispatch identity
token, the toolbelt's delegation token, the read-only timeline key, the web-search key, the
openai-agent harness's in-process model key). A harness CLI exposes a Bash tool to the model, and
whatever is in that CLI's environment is in the model's shell. Each of these is DENIED there; the
model credential the CLI itself needs to reach its provider is not.
"""

import pytest

from llm import ports
from llm.ports import harness_subprocess_env

WITHHELD = [
    "REDIS_URL",                  # the shared data plane
    "VEXA_AGENT_IDENTITY_TOKEN",  # the per-dispatch signed identity
    "VEXA_UNIT_IN_KEY",           # writes onto the unit's own input stream
    "VEXA_FLOWS_TIMELINE_KEY",    # flows-api GET /timeline names its own subject
    "VEXA_MCP_DELEGATION_TOKEN",  # the toolbelt bearer; the CLI reads it from the attachment file
    "VEXA_SEARCH_API_KEY",        # the operator's search endpoint key; used in-process only
    "VEXA_LLM_API_KEY",           # the openai-agent harness's key; that harness runs in-process
]

#: What a harness CLI genuinely needs to talk to its model provider.
KEPT = ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY", "CODEX_API_KEY"]


@pytest.mark.parametrize("name", WITHHELD)
def test_harness_subprocess_env_withholds(monkeypatch, name):
    monkeypatch.setenv(name, "value-held-by-the-worker")
    assert name not in harness_subprocess_env(), f"{name} must not reach the model's shell"


def test_every_withheld_name_is_on_the_deny_list():
    assert set(WITHHELD) <= set(ports._HARNESS_SUBPROCESS_DENY_VARS)


@pytest.mark.parametrize("name", KEPT)
def test_harness_subprocess_env_keeps_model_credentials(monkeypatch, name):
    monkeypatch.setenv(name, "provider-credential")
    assert harness_subprocess_env()[name] == "provider-credential"


def test_claude_cli_env_withholds(monkeypatch):
    from llm import claude_code
    for name in WITHHELD:
        monkeypatch.setenv(name, "value-held-by-the-worker")
    env = claude_code._cli_env()
    assert not set(WITHHELD) & set(env)
