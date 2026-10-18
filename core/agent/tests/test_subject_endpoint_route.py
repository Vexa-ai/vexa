"""A subject's own model endpoint reaches the openai-agent harness intact (Vexa-ai/vexa#1783).

The shape app.dev runs: the deployment's openai-agent lane is a local Qwen box, so the runtime
forwards ``VEXA_LLM_BASE_URL`` / ``_MODEL`` / ``_EXTRA_BODY`` into every agent worker, and no
``VEXA_LLM_API_KEY`` is set. A person points Settings → Models at an allow-listed gateway with a key
of their own and picks openai-agent. The dispatch used to stamp that endpoint only under the
ANTHROPIC_* names, the runtime filled the VEXA_LLM_* names from the deployment, and the harness reads
VEXA_LLM_* first: the turn ran on the deployment's Qwen with the person's key attached.

These tests run the real chain, offline: ``build_unit_env`` (the dispatch), then the runtime's own
merge (``runtime_kernel.workload_env``, loaded from its file, so its forward list is the live one),
then ``OpenAIAgentHarness`` on the resulting worker env, with a mock transport recording what would
go on the wire. They hold four rules:

1. a subject endpoint that passed the operator gate is used whole: its URL, its key, the model the
   overlay resolved, its extra_body, whatever the deployment's VEXA_LLM_* say;
2. the subject's key goes to the subject's endpoint only, and the deployment's credentials never go
   there (F84 on both harnesses);
3. with no subject endpoint, or a refused one, the deployment's route applies exactly as before;
4. the model comes from ``VEXA_AGENT_MODEL`` under the existing allowlist, nowhere else.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from control_plane.dispatch import build_unit_env, subject_route_env
from llm.openai_agent import OpenAIAgentHarness
from shared.config import load_settings

REPO = Path(__file__).resolve().parents[3]

QWEN = "http://192.168.1.6:8001/v1"
QWEN_BODY = '{"chat_template_kwargs":{"enable_thinking":false}}'
ROUTER = "https://openrouter.ai/api/v1"
SUBJECT_KEY = "sk-or-subject-key"
DEPLOYMENT_SECRETS = ("deployment-oauth", "deployment-llm-key", "deployment-anthropic-token")


def _runtime_workload_env():
    """The runtime's env module, read from its file (stdlib-only), so the forward list and the
    merge rule under test are the ones the runtime ships rather than a copy."""
    path = REPO / "core" / "runtime" / "src" / "runtime_kernel" / "workload_env.py"
    name = "runtime_kernel_workload_env_route_test"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RT = _runtime_workload_env()


def _deployment(*, llm_key: bool) -> tuple[dict, dict]:
    """(agent-api's environment, the runtime's environment) for the app.dev shape. ``llm_key``
    adds the deployment credentials app.dev does not have, so rule 2 is also checked against a
    deployment that has a VEXA_LLM_API_KEY and a gateway token to leak."""
    agent_api = {"VEXA_LLM_BASE_URL": QWEN, "CLAUDE_CODE_OAUTH_TOKEN": "deployment-oauth"}
    runtime = {"VEXA_RUNNER": "claude-code", "VEXA_AGENT_MODEL": "claude-sonnet-5",
               "VEXA_LLM_BASE_URL": QWEN, "VEXA_LLM_MODEL": "qwen3.8-27b",
               "VEXA_LLM_EXTRA_BODY": QWEN_BODY, "CLAUDE_CODE_OAUTH_TOKEN": "deployment-oauth"}
    if llm_key:
        for env in (agent_api, runtime):
            env["VEXA_LLM_API_KEY"] = "deployment-llm-key"
            env["ANTHROPIC_AUTH_TOKEN"] = "deployment-anthropic-token"
    return agent_api, runtime


#: Every key the dispatch backfills from agent-api, or the runtime forwards, plus the subject route.
_MODEL_KEYS = set(RT.WORKER_FORWARD_ENV) | set(subject_route_env("", "", "")) | {
    "VEXA_MODEL_BASE_URL_ALLOW"}

_INV = {"identity": {"subject": "u_2", "launcher": "user:u_2"}, "runner": "claude-code",
        "workspaces": [{"id": "u_2", "mode": "rw"}], "trigger": "message",
        "start": {"entrypoint": {"inline": "hi"}}}


def _isolate(monkeypatch, env: dict) -> None:
    for key in _MODEL_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def _worker_env(monkeypatch, tmp_path, model_config, *, llm_key=False, backend="process",
                **settings) -> dict:
    """What a worker's process environment holds, for the model keys, on ``backend``."""
    agent_api, runtime = _deployment(llm_key=llm_key)
    _isolate(monkeypatch, agent_api)
    settings.setdefault("agent_model", "claude-sonnet-5")
    spec = build_unit_env(load_settings(workspaces_dir=str(tmp_path), **settings), _INV,
                          unit_id="unit-1", token="tok", model_config=model_config)
    if backend == "process":
        merged = RT.child_environment(spec, forward=RT.WORKER_FORWARD_ENV, parent=runtime)
    elif backend == "docker":       # docker_backend.start: spawn_env, then the forward list
        merged = dict(spec)
        merged.update(RT.forwarded_env(RT.WORKER_FORWARD_ENV, runtime, merged))
    else:                           # k8s: the Pod carries the spec env verbatim
        merged = dict(spec)
    return {k: v for k, v in merged.items() if k in _MODEL_KEYS}


def _turn(monkeypatch, tmp_path, worker: dict) -> list[dict]:
    """Construct the harness the way the worker does and run one turn; return what went out."""
    _isolate(monkeypatch, worker)
    monkeypatch.setenv("VEXA_AGENT_STREAM", "0")
    monkeypatch.delenv("VEXA_MOUNTS", raising=False)
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"url": str(request.url), "auth": request.headers.get("Authorization"),
                     "body": json.loads(request.content)})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant",
                                                                  "content": "ok"},
                                                      "finish_reason": "stop"}]})

    harness = OpenAIAgentHarness(transport=httpx.MockTransport(handler))
    assert harness.preflight() is None
    # engine.main: the model every turn runs on is VEXA_AGENT_MODEL, passed in as `model`
    events = list(harness.run_turn(tmp_path, "hi", model=os.environ.get("VEXA_AGENT_MODEL") or None))
    assert events[-1]["type"] == "done" and events[-1]["ok"] is True, events[-1]
    assert sent, "the harness sent nothing"
    return sent


_SUBJECT = {"mode": "custom", "runner": "openai-agent", "base_url": ROUTER,
            "api_key": SUBJECT_KEY, "model": "z-ai/glm-5.3"}


# ── rules 1 and 2: the app.dev regression ──────────────────────────────────────────────────────

@pytest.mark.parametrize("backend", ["process", "docker", "k8s"])
@pytest.mark.parametrize("llm_key", [False, True], ids=["appdev-no-llm-key", "deployment-llm-key"])
def test_a_subject_endpoint_is_used_whole_whatever_the_deployment_forwards(
        monkeypatch, tmp_path, backend, llm_key):
    worker = _worker_env(monkeypatch, tmp_path, dict(_SUBJECT), llm_key=llm_key, backend=backend)

    assert worker["VEXA_RUNNER"] == "openai-agent"
    assert worker["VEXA_LLM_BASE_URL"] == worker["ANTHROPIC_BASE_URL"] == ROUTER
    assert worker["VEXA_LLM_API_KEY"] == worker["ANTHROPIC_AUTH_TOKEN"] == SUBJECT_KEY
    assert worker["VEXA_AGENT_MODEL"] == "z-ai/glm-5.3"
    assert worker["VEXA_LLM_MODEL"] == "" and worker["VEXA_LLM_EXTRA_BODY"] == ""
    assert worker["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert not set(DEPLOYMENT_SECRETS) & set(worker.values()), \
        "a deployment credential is in a subject's route"
    assert QWEN not in worker.values()

    for request in _turn(monkeypatch, tmp_path, worker):
        assert request["url"] == f"{ROUTER}/chat/completions"
        assert request["auth"] == f"Bearer {SUBJECT_KEY}"
        assert request["body"]["model"] == "z-ai/glm-5.3"
        assert "chat_template_kwargs" not in request["body"], \
            "the deployment's extra_body reached the subject's endpoint"


def test_the_subjects_own_extra_body_is_the_one_applied(monkeypatch, tmp_path):
    config = dict(_SUBJECT, extra_body='{"provider": {"order": ["z-ai"]}}')
    worker = _worker_env(monkeypatch, tmp_path, config)
    assert worker["VEXA_LLM_EXTRA_BODY"] == '{"provider": {"order": ["z-ai"]}}'
    for request in _turn(monkeypatch, tmp_path, worker):
        assert request["url"] == f"{ROUTER}/chat/completions"
        assert request["body"]["provider"] == {"order": ["z-ai"]}
        assert "chat_template_kwargs" not in request["body"]


def test_a_keyless_subject_endpoint_gets_no_key_at_all(monkeypatch, tmp_path):
    """Rule 2 from the other side: the subject set no key, and the deployment has three. None of
    them may be presented at the subject's endpoint."""
    worker = _worker_env(monkeypatch, tmp_path, dict(_SUBJECT, api_key=""), llm_key=True)
    assert not set(DEPLOYMENT_SECRETS) & set(worker.values())
    for request in _turn(monkeypatch, tmp_path, worker):
        assert request["url"].startswith(ROUTER)
        assert request["auth"] is None


# ── rule 3: no subject endpoint, or a refused one, leaves the deployment's route alone ─────────

@pytest.mark.parametrize("config", [
    None,
    {"mode": "subscription", "runner": "openai-agent"},
    {"mode": "custom", "runner": "openai-agent", "base_url": ""},          # inert
], ids=["no-config", "subscription", "custom-without-url"])
def test_without_a_subject_endpoint_the_deployment_route_applies(monkeypatch, tmp_path, config):
    worker = _worker_env(monkeypatch, tmp_path, config, llm_key=True)
    assert "ANTHROPIC_BASE_URL" not in worker
    assert worker["VEXA_LLM_BASE_URL"] == QWEN and worker["VEXA_LLM_MODEL"] == "qwen3.8-27b"
    for request in _turn(monkeypatch, tmp_path, worker):
        assert request["url"] == f"{QWEN}/chat/completions"
        assert request["auth"] == "Bearer deployment-llm-key"
        assert request["body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_a_refused_subject_endpoint_leaves_the_deployment_route_and_carries_no_subject_key(
        monkeypatch, tmp_path):
    reports: list[dict] = []
    agent_api, runtime = _deployment(llm_key=False)
    _isolate(monkeypatch, agent_api)
    spec = build_unit_env(load_settings(workspaces_dir=str(tmp_path), agent_model="claude-sonnet-5"),
                          _INV, unit_id="unit-1", token="tok", friction=reports.append,
                          model_config=dict(_SUBJECT, base_url="https://evil.example.com/v1"))
    assert reports and reports[0]["kind"] == "refusal"
    worker = {k: v for k, v in RT.child_environment(spec, forward=RT.WORKER_FORWARD_ENV,
                                                     parent=runtime).items() if k in _MODEL_KEYS}
    assert SUBJECT_KEY not in worker.values()
    assert worker["VEXA_LLM_BASE_URL"] == QWEN
    for request in _turn(monkeypatch, tmp_path, worker):
        assert request["url"] == f"{QWEN}/chat/completions"
        assert request["auth"] is None      # app.dev's Qwen lane has no key, and gains none
        # A refused endpoint is no endpoint: the model name follows the allowlist alone, as before.
        assert request["body"]["model"] == "z-ai/glm-5.3"


# ── rule 4: the model is VEXA_AGENT_MODEL, under the allowlist that already exists ──────────────

def test_a_non_allowlisted_subject_model_falls_back_to_the_deployment_default(monkeypatch, tmp_path):
    """Allowlist unchanged: the subject's endpoint still applies, its model name does not, and the
    openai-agent override VEXA_LLM_MODEL (the Qwen name, meant for the Qwen box) never stands in."""
    worker = _worker_env(monkeypatch, tmp_path, dict(_SUBJECT),
                         model_allowlist="claude-sonnet-5,qwen3.8-27b")
    assert worker["VEXA_AGENT_MODEL"] == "claude-sonnet-5"
    for request in _turn(monkeypatch, tmp_path, worker):
        assert request["url"].startswith(ROUTER)
        assert request["body"]["model"] == "claude-sonnet-5"


def test_a_subject_model_without_an_endpoint_goes_to_the_deployment_endpoint(monkeypatch, tmp_path):
    worker = _worker_env(monkeypatch, tmp_path, {"runner": "openai-agent", "model": "qwen3.8-27b-a"},
                         model_allowlist="qwen3.8-27b-a")
    for request in _turn(monkeypatch, tmp_path, worker):
        assert request["url"] == f"{QWEN}/chat/completions"
        assert request["body"]["model"] == "qwen3.8-27b-a"


# ── the claude-code harness sees exactly what it saw before ─────────────────────────────────────

def test_the_claude_code_route_is_unchanged(monkeypatch, tmp_path):
    """The GLM-via-OpenRouter override that worked on claude-code: the claude CLI reads the
    ANTHROPIC_* names and the subscription token, and those are what they always were."""
    worker = _worker_env(monkeypatch, tmp_path, dict(_SUBJECT, runner="claude-code",
                                                     base_url="https://openrouter.ai/api"),
                         llm_key=True)
    assert worker["VEXA_RUNNER"] == "claude-code"
    assert worker["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert worker["ANTHROPIC_AUTH_TOKEN"] == worker["ANTHROPIC_API_KEY"] == SUBJECT_KEY
    assert worker["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert worker["VEXA_AGENT_MODEL"] == "z-ai/glm-5.3"
