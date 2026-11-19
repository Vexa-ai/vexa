"""H5/W1, the worker's half: a harness holds only its own run's model-proxy token.

The token arrives with the message (the spawn env for the entrypoint, the signed entry for every
later one); each harness hands it to its CLI under the one name that CLI reads, nothing inherited from
the worker's environment carries a provider key, and the token is revoked when the last run holding
it — the turn, its write-back, the jobs it started — lets go of it.
"""
from __future__ import annotations

import json

import httpx
import pytest

from shared import model_grant as mg
from tests.conftest import signed_turn
from tests.test_worker import FakeStream
from worker.worker import serve

PROXY = "http://model-proxy:8102"


@pytest.fixture
def revoked(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(mg, "_revoke", calls.append)
    return calls


def test_a_token_is_revoked_when_its_last_run_lets_go(revoked):
    with mg.use("vxm_turn"):
        assert mg.current() == "vxm_turn"
        held = mg.capture()                     # a job the turn started
        assert revoked == []
    assert mg.current() == ""
    assert revoked == []                        # the job still holds it
    with mg.adopt(held):
        assert mg.current() == "vxm_turn"
    assert revoked == ["vxm_turn"]


def test_no_token_means_no_credential(revoked):
    with mg.use(None):
        assert mg.current() == ""
    assert revoked == []


def test_the_boot_token_leaves_the_environment(monkeypatch):
    monkeypatch.setenv(mg.TOKEN_ENV, "vxm_boot")
    assert mg.take_boot_token() == "vxm_boot"
    import os
    assert mg.TOKEN_ENV not in os.environ and mg.boot_token() == "vxm_boot"


def test_each_message_runs_on_its_own_token_and_each_is_revoked_after(revoked, monkeypatch):
    seen: list[tuple[str, str]] = []

    def turn(prompt):
        seen.append((prompt, mg.current()))
        yield {"type": "message-delta", "text": f"re:{prompt}"}

    monkeypatch.setattr(mg, "_boot_token", "vxm_t0")
    s = FakeStream(inbox=[("1-0", signed_turn({"prompt": "second", mg.ENTRY_FIELD: "vxm_t1"})),
                          ("2-0", signed_turn({"prompt": "third", mg.ENTRY_FIELD: "vxm_t2"}))])
    serve(s, out_topic="o", in_topic="i", turn=turn, start={"entrypoint": {"inline": "first"}}, idle_ms=10)
    assert seen == [("first", "vxm_t0"), ("second", "vxm_t1"), ("third", "vxm_t2")]
    assert revoked == ["vxm_t0", "vxm_t1", "vxm_t2"]


# ── what each harness hands its CLI ────────────────────────────────────────────────────────────────

PROVIDER_KEYS = {"ANTHROPIC_API_KEY": "sk-ant-LEAK", "ANTHROPIC_AUTH_TOKEN": "sk-or-LEAK",
                 "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-LEAK", "OPENAI_API_KEY": "sk-proj-LEAK",
                 "CODEX_API_KEY": "sk-codex-LEAK", "VEXA_LLM_API_KEY": "sk-llm-LEAK",
                 mg.TOKEN_ENV: "vxm_someone_elses_boot_token"}


def test_the_claude_cli_gets_its_runs_token_and_no_inherited_key(monkeypatch, revoked):
    from llm.claude_code import _cli_env
    for k, v in PROVIDER_KEYS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", PROXY + mg.LLM_PATH)
    with mg.use("vxm_this_turn"):
        env = _cli_env()
    assert env["ANTHROPIC_AUTH_TOKEN"] == "vxm_this_turn"
    assert env["ANTHROPIC_BASE_URL"] == PROXY + mg.LLM_PATH
    leaked = [k for k, v in PROVIDER_KEYS.items() if v in json.dumps(env)]
    assert leaked == []


def test_codex_is_pointed_at_the_proxy_with_its_runs_token(monkeypatch, revoked):
    from llm import codex
    for k, v in PROVIDER_KEYS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv(mg.URL_ENV, PROXY)
    captured = {}

    class _Proc:
        def __init__(self, argv, **kw):
            captured["argv"], captured["env"] = argv, kw["env"]

    h = codex.CodexHarness(process_factory=_Proc)
    with mg.use("vxm_codex_turn"):
        h._spawn(__import__("pathlib").Path("/tmp"))
    argv, env = captured["argv"], captured["env"]
    assert argv[0] == "codex" and argv[-2:] == ["app-server", "--stdio"]
    assert f'model_providers.{codex.PROXY_PROVIDER}.base_url="{PROXY}{mg.LLM_PATH}"' in argv
    assert f'model_providers.{codex.PROXY_PROVIDER}.env_key="{mg.TOKEN_ENV}"' in argv
    assert env[mg.TOKEN_ENV] == "vxm_codex_turn"
    leaked = [k for k, v in PROVIDER_KEYS.items() if v in json.dumps(env) or v in " ".join(argv)]
    assert leaked == []
    assert h.preflight() is None


def test_the_openai_agent_loop_sends_its_runs_token(monkeypatch, revoked):
    from llm.openai_agent import OpenAIAgentHarness
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    h = OpenAIAgentHarness(base_url=PROXY + mg.LLM_PATH, model="m",
                           transport=httpx.MockTransport(handler))
    with mg.use("vxm_oa_turn"):
        assert h._credential() == "vxm_oa_turn"
    assert h._credential() == ""
