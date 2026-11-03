"""gate:access deny vectors — what the model-driven harness subprocess may NOT inherit.

The worker process holds credentials it uses itself (its redis grant, the per-dispatch identity
token, the toolbelt's delegation token, the read-only timeline key, the web-search key). A harness
CLI exposes a Bash tool to the model, and whatever is in that CLI's environment is in the model's
shell. Each of these is DENIED there — and so is every model-provider credential (H5/W1): no worker
is given one, and should one reach the worker's environment anyway, no harness inherits it. A
harness is handed its own run's model-proxy token instead, under the one name its CLI reads.
"""

import pytest

from llm import ports
from llm.ports import harness_subprocess_env

WITHHELD = [
    "REDIS_URL",                  # the shared data plane
    "VEXA_AGENT_IDENTITY_TOKEN",  # the per-dispatch signed identity
    "VEXA_UNIT_IN_KEY",           # writes onto the unit's own input stream
    "VEXA_FLOWS_TIMELINE_KEY",    # flows-api GET /timeline names its own subject
    "VEXA_MCP_DELEGATION_TOKEN",  # the toolbelt bearer; the proxy presents it (H5/W1)
    "VEXA_SEARCH_API_KEY",        # the operator's search endpoint key; used in-process only
    "VEXA_LLM_API_KEY",           # the openai-agent harness's key; that harness runs in-process
    # every model-provider credential (H5/W1), and the spawn's own model-proxy token
    "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY", "CODEX_API_KEY", "VEXA_MODEL_PROXY_TOKEN",
]

#: What a harness CLI still needs from the worker's environment: where its model requests go (the
#: model proxy, stamped by the dispatch). Never a credential.
KEPT = ["ANTHROPIC_BASE_URL", "VEXA_LLM_BASE_URL", "VEXA_MODEL_PROXY_URL"]


@pytest.mark.parametrize("name", WITHHELD)
def test_harness_subprocess_env_withholds(monkeypatch, name):
    monkeypatch.setenv(name, "value-held-by-the-worker")
    assert name not in harness_subprocess_env(), f"{name} must not reach the model's shell"


def test_every_withheld_name_is_on_the_deny_list():
    assert set(WITHHELD) <= set(ports._HARNESS_SUBPROCESS_DENY_VARS)


@pytest.mark.parametrize("name", KEPT)
def test_harness_subprocess_env_keeps_the_model_route(monkeypatch, name):
    monkeypatch.setenv(name, "http://model-proxy:8102/llm")
    assert harness_subprocess_env()[name] == "http://model-proxy:8102/llm"


def test_claude_cli_env_withholds(monkeypatch):
    from llm import claude_code
    for name in WITHHELD:
        monkeypatch.setenv(name, "value-held-by-the-worker")
    env = claude_code._cli_env()
    assert not set(WITHHELD) & set(env)
