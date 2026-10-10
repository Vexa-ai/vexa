"""A CATALOG ROUTE'S CREDENTIAL GOES TO ITS PROVIDER ONLY — through the whole chain, on every backend.

The three credential invariants the per-person endpoint established (#1783, #1784) hold for the
catalog by construction, because a catalog route is stamped by the same one writer, whole:

1. a provider's secret goes only to that provider's endpoint, under the one name it expects;
2. the deployment's credentials never reach an endpoint that is not the deployment's own — and the
   person's own key never reaches a catalog provider;
3. one place decides the route: with no catalog, the worker env is byte-identical to what it was.

These run the real chain offline, as `test_subject_endpoint_route` does: `build_unit_env` (the
dispatch), the runtime's own merge (`runtime_kernel.workload_env`, loaded from its file), then the
openai-agent harness on the resulting env with a mock transport recording what goes on the wire.
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
from control_plane.model_providers import ModelChoiceFault, UNKNOWN_MODEL, Catalog, parse
from llm.openai_agent import OpenAIAgentHarness
from shared.config import load_settings
from tests.model_catalogs import ENV, EXAMPLE

REPO = Path(__file__).resolve().parents[3]

OPERATOR_KEY = ENV["VEXA_MODEL_SECRET_OPENROUTER"]
PERSON_KEY = "sk-person-own-key"
DEPLOYMENT_SECRETS = ("deployment-oauth", "deployment-llm-key", "deployment-anthropic-token")
DEPLOYMENT_QWEN = "http://192.0.2.10:8001/v1"
OWN = {"mode": "custom", "base_url": "https://openrouter.ai/api/v1", "api_key": PERSON_KEY,
       "model": "z-ai/glm-5.3", "runner": "openai-agent"}


def _runtime_workload_env():
    path = REPO / "core" / "runtime" / "src" / "runtime_kernel" / "workload_env.py"
    name = "runtime_kernel_workload_env_catalog_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RT = _runtime_workload_env()

#: Everything the dispatch backfills, the runtime forwards, or a route stamps.
_MODEL_KEYS = set(RT.WORKER_FORWARD_ENV) | set(subject_route_env("", "", "")) | {
    "VEXA_MODEL_BASE_URL_ALLOW", "VEXA_AGENT_EFFORT"}

_INV = {"identity": {"subject": "u_2", "launcher": "user:u_2"}, "runner": "claude-code",
        "workspaces": [{"id": "u_2", "mode": "rw"}], "trigger": "message",
        "start": {"entrypoint": {"inline": "hi"}}}


def _deployment() -> tuple[dict, dict]:
    """(agent-api's environment, the runtime's environment): a deployment with every credential
    there is to leak, and the catalog's own secrets in agent-api's environment."""
    agent_api = {"VEXA_LLM_BASE_URL": DEPLOYMENT_QWEN, "CLAUDE_CODE_OAUTH_TOKEN": "deployment-oauth",
                 "ANTHROPIC_AUTH_TOKEN": "deployment-anthropic-token",
                 "ANTHROPIC_BASE_URL": "https://gateway.deployment.example",
                 "VEXA_LLM_API_KEY": "deployment-llm-key", **ENV}
    runtime = {"VEXA_RUNNER": "claude-code", "VEXA_AGENT_MODEL": "claude-sonnet-5",
               "VEXA_LLM_BASE_URL": DEPLOYMENT_QWEN, "VEXA_LLM_MODEL": "qwen-deployment",
               "VEXA_LLM_API_KEY": "deployment-llm-key",
               "VEXA_LLM_EXTRA_BODY": '{"deployment_only": true}',
               "ANTHROPIC_AUTH_TOKEN": "deployment-anthropic-token",
               "ANTHROPIC_BASE_URL": "https://gateway.deployment.example",
               "ANTHROPIC_DEFAULT_HAIKU_MODEL": "gateway/haiku",
               "VEXA_AGENT_CONTEXT_TOKENS": "24000", "CLAUDE_CODE_OAUTH_TOKEN": "deployment-oauth"}
    return agent_api, runtime


def _isolate(monkeypatch, env: dict) -> None:
    for key in _MODEL_KEYS | set(ENV):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def _spec(monkeypatch, tmp_path, *, choice="", model_config=None, catalog=None, admin=True,
          effort="", **settings) -> dict:
    agent_api, _ = _deployment()
    _isolate(monkeypatch, agent_api)
    settings.setdefault("agent_model", "claude-sonnet-5")
    return build_unit_env(load_settings(workspaces_dir=str(tmp_path), **settings), _INV,
                          unit_id="unit-1", token="tok", model_config=model_config,
                          catalog=parse(json.dumps(EXAMPLE), ENV) if catalog is None else catalog,
                          model_choice=choice, admin=admin, effort_choice=effort)


def _worker(monkeypatch, tmp_path, backend="process", **kw) -> dict:
    spec = _spec(monkeypatch, tmp_path, **kw)
    _, runtime = _deployment()
    if backend == "process":
        merged = RT.child_environment(spec, forward=RT.WORKER_FORWARD_ENV, parent=runtime)
    elif backend == "docker":
        merged = dict(spec)
        merged.update(RT.forwarded_env(RT.WORKER_FORWARD_ENV, runtime, merged))
    else:                                   # k8s: the Pod carries the spec env verbatim
        merged = dict(spec)
    return {k: v for k, v in merged.items() if k in _MODEL_KEYS | set(ENV)}


def _turn(monkeypatch, tmp_path, worker: dict) -> list[dict]:
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
    events = list(harness.run_turn(tmp_path, "hi", model=os.environ.get("VEXA_AGENT_MODEL") or None))
    assert events[-1]["type"] == "done" and events[-1]["ok"] is True, events[-1]
    assert sent
    return sent


def _no_foreign_credential(worker: dict, *, allowed: tuple[str, ...] = ()) -> None:
    values = set(worker.values())
    for secret in DEPLOYMENT_SECRETS + (OPERATOR_KEY, PERSON_KEY, ENV["VEXA_MODEL_SECRET_ANTHROPIC"]):
        if secret not in allowed:
            assert secret not in values, f"{secret!r} reached a route it does not belong to"


# ── 1. a provider's secret goes to that provider only ───────────────────────────────────────────

@pytest.mark.parametrize("backend", ["process", "docker", "k8s"])
def test_a_self_hosted_qwen_entry_runs_keyless_on_its_own_endpoint(monkeypatch, tmp_path, backend):
    worker = _worker(monkeypatch, tmp_path, backend, choice="qwen3-32b", model_config=OWN)
    assert worker["VEXA_RUNNER"] == "openai-agent"
    assert worker["VEXA_LLM_BASE_URL"] == "http://10.0.0.5:8000/v1"
    assert worker["VEXA_AGENT_MODEL"] == "Qwen/Qwen3-32B" and worker["VEXA_LLM_MODEL"] == ""
    assert worker["VEXA_AGENT_CONTEXT_TOKENS"] == str(32768 - 8192)    # the window less the answer
    _no_foreign_credential(worker)
    for request in _turn(monkeypatch, tmp_path, worker):
        assert request["url"] == "http://10.0.0.5:8000/v1/chat/completions"
        assert request["auth"] is None
        assert request["body"]["model"] == "Qwen/Qwen3-32B"
        assert request["body"]["chat_template_kwargs"] == {"enable_thinking": False}
        assert "deployment_only" not in request["body"]


@pytest.mark.parametrize("backend", ["process", "docker", "k8s"])
def test_an_openrouter_entry_sends_the_operators_key_to_openrouter_only(monkeypatch, tmp_path,
                                                                         backend):
    worker = _worker(monkeypatch, tmp_path, backend, choice="or-sonnet", model_config=OWN)
    assert worker["VEXA_LLM_BASE_URL"] == worker["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert worker["VEXA_LLM_API_KEY"] == worker["ANTHROPIC_AUTH_TOKEN"] == OPERATOR_KEY
    assert worker["ANTHROPIC_API_KEY"] == "" and worker["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    _no_foreign_credential(worker, allowed=(OPERATOR_KEY,))
    for request in _turn(monkeypatch, tmp_path, worker):
        assert request["url"] == "https://openrouter.ai/api/v1/chat/completions"
        assert request["auth"] == f"Bearer {OPERATOR_KEY}"
        assert request["body"]["model"] == "anthropic/claude-sonnet-4.5"


def test_an_openrouter_entry_on_claude_code_pins_every_tier_to_the_chosen_model(monkeypatch,
                                                                                 tmp_path):
    decl = json.loads(json.dumps(EXAMPLE))
    decl["providers"]["openrouter"]["harness"] = "claude-code"
    # the CLI on OpenRouter offers no effort control (the adapter refuses levels at boot)
    or_sonnet = next(m for m in decl["models"] if m["id"] == "or-sonnet")
    or_sonnet.pop("capabilities", None)
    worker = _worker(monkeypatch, tmp_path, choice="or-sonnet", catalog=parse(json.dumps(decl), ENV))
    assert worker["VEXA_RUNNER"] == "claude-code"
    assert worker["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert worker["ANTHROPIC_AUTH_TOKEN"] == OPERATOR_KEY and worker["ANTHROPIC_API_KEY"] == ""
    assert worker["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    for tier in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                 "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
        assert worker[tier] == "anthropic/claude-sonnet-4.5", tier
    _no_foreign_credential(worker, allowed=(OPERATOR_KEY,))


@pytest.mark.parametrize("backend", ["process", "docker", "k8s"])
def test_the_subscription_entry_sends_the_subscription_to_anthropic_and_nowhere_else(
        monkeypatch, tmp_path, backend):
    """The deployment's subscription has one legitimate destination. The deployment's gateway (its
    ANTHROPIC_BASE_URL and gateway token) is stamped out, so the CLI's own default — Anthropic —
    is where the backfilled OAuth token goes."""
    worker = _worker(monkeypatch, tmp_path, backend, choice="claude")
    assert worker["VEXA_RUNNER"] == "claude-code"
    assert worker["ANTHROPIC_BASE_URL"] == "" and worker["VEXA_LLM_BASE_URL"] == ""
    assert worker["ANTHROPIC_AUTH_TOKEN"] == worker["ANTHROPIC_API_KEY"] == ""
    assert worker["CLAUDE_CODE_OAUTH_TOKEN"] == "deployment-oauth"
    assert worker["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == ""      # not the gateway's alias
    _no_foreign_credential(worker, allowed=("deployment-oauth",))


def test_an_anthropic_api_key_goes_as_the_api_key_to_anthropic(monkeypatch, tmp_path):
    decl = json.loads(json.dumps(EXAMPLE))
    decl["providers"]["anthropic"] = {"adapter": "anthropic", "auth": "secret",
                                      "secret_ref": "env:VEXA_MODEL_SECRET_ANTHROPIC"}
    worker = _worker(monkeypatch, tmp_path, choice="claude", catalog=parse(json.dumps(decl), ENV))
    assert worker["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"
    assert worker["ANTHROPIC_API_KEY"] == ENV["VEXA_MODEL_SECRET_ANTHROPIC"]
    assert worker["ANTHROPIC_AUTH_TOKEN"] == "" and worker["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    _no_foreign_credential(worker, allowed=(ENV["VEXA_MODEL_SECRET_ANTHROPIC"],))


# ── 2. the person's key goes to the person's endpoint, and nothing else does ────────────────────

@pytest.mark.parametrize("backend", ["process", "docker", "k8s"])
def test_the_own_endpoint_entry_carries_the_persons_key_and_nothing_of_the_deployments(
        monkeypatch, tmp_path, backend):
    worker = _worker(monkeypatch, tmp_path, backend, choice="mine", model_config=OWN)
    assert worker["VEXA_LLM_BASE_URL"] == OWN["base_url"]
    assert worker["VEXA_LLM_API_KEY"] == PERSON_KEY
    _no_foreign_credential(worker, allowed=(PERSON_KEY,))
    for request in _turn(monkeypatch, tmp_path, worker):
        assert request["url"] == f"{OWN['base_url']}/chat/completions"
        assert request["auth"] == f"Bearer {PERSON_KEY}"
        assert request["body"]["model"] == "z-ai/glm-5.3"


def test_a_catalog_secret_is_absent_from_a_worker_on_another_provider(monkeypatch, tmp_path):
    for choice in ("qwen3-32b", "claude", "mine"):
        worker = _worker(monkeypatch, tmp_path, choice=choice, model_config=OWN)
        assert OPERATOR_KEY not in worker.values(), choice


# ── 3. one place decides; no catalog changes nothing ────────────────────────────────────────────

@pytest.mark.parametrize("config", [None, {}, OWN, {"mode": "subscription", "model": "claude-x"},
                                    {"mode": "custom", "base_url": "https://evil.example.com/v1",
                                     "api_key": PERSON_KEY}],
                         ids=["none", "empty", "own", "subscription", "refused"])
def test_without_a_catalog_the_dispatch_is_byte_identical(monkeypatch, tmp_path, config):
    agent_api, _ = _deployment()
    _isolate(monkeypatch, agent_api)
    settings = load_settings(workspaces_dir=str(tmp_path), agent_model="claude-sonnet-5")
    before = build_unit_env(settings, _INV, unit_id="unit-1", token="tok", model_config=config)
    for catalog in (None, Catalog(None)):
        after = build_unit_env(settings, _INV, unit_id="unit-1", token="tok", model_config=config,
                               catalog=catalog, model_choice="qwen3-32b", admin=True)
        before.pop("VEXA_START", None), after.pop("VEXA_START", None)
        assert after == before


def test_a_pick_that_cannot_run_is_refused_before_any_env_exists(monkeypatch, tmp_path):
    with pytest.raises(ModelChoiceFault) as exc:
        _spec(monkeypatch, tmp_path, choice="retired-model")
    assert exc.value.kind == UNKNOWN_MODEL


def test_a_refused_own_endpoint_files_the_friction_it_always_filed(monkeypatch, tmp_path):
    reports: list[dict] = []
    agent_api, _ = _deployment()
    _isolate(monkeypatch, agent_api)
    with pytest.raises(ModelChoiceFault):
        build_unit_env(load_settings(workspaces_dir=str(tmp_path)), _INV, unit_id="unit-1",
                       token="tok", friction=reports.append,
                       model_config=dict(OWN, base_url="https://evil.example.com/v1"),
                       catalog=parse(json.dumps(EXAMPLE), ENV), model_choice="mine")
    assert reports and reports[0]["kind"] == "refusal"
    assert PERSON_KEY not in json.dumps(reports)


# ── the effort level, on the wire, per adapter (founder 2026-10-10) ─────────────────────────────

def test_openrouter_sends_the_effort_as_reasoning_effort_and_the_entrys_output_cap(monkeypatch, tmp_path):
    worker = _worker(monkeypatch, tmp_path, choice="or-sonnet")          # default_effort: medium
    assert worker["VEXA_AGENT_MAX_OUTPUT_TOKENS"] == "8192" and worker["VEXA_AGENT_EFFORT"] == ""
    for request in _turn(monkeypatch, tmp_path, worker):
        assert request["body"]["reasoning"] == {"effort": "medium"}
        assert request["body"]["max_tokens"] == 8192
    picked = _worker(monkeypatch, tmp_path, choice="or-sonnet", effort="high")
    for request in _turn(monkeypatch, tmp_path, picked):
        assert request["body"]["reasoning"] == {"effort": "high"}


@pytest.mark.parametrize("effort, thinking", [("", False), ("none", False), ("high", True)])
def test_a_qwen_toggle_sends_the_effort_as_enable_thinking(monkeypatch, tmp_path, effort, thinking):
    worker = _worker(monkeypatch, tmp_path, choice="qwen3-32b", effort=effort)
    for request in _turn(monkeypatch, tmp_path, worker):
        assert request["body"]["chat_template_kwargs"] == {"enable_thinking": thinking}
        assert "reasoning_effort" not in request["body"]


def test_an_openai_compatible_entry_sends_the_openai_reasoning_effort_field(monkeypatch, tmp_path):
    decl = json.loads(json.dumps(EXAMPLE))
    qwen = next(m for m in decl["models"] if m["id"] == "qwen3-32b")
    qwen.pop("effort_control")
    qwen["capabilities"].update(reasoning_efforts=["low", "high"], default_effort="low")
    worker = _worker(monkeypatch, tmp_path, choice="qwen3-32b", effort="high",
                     catalog=parse(json.dumps(decl), ENV))
    for request in _turn(monkeypatch, tmp_path, worker):
        assert request["body"]["reasoning_effort"] == "high"


def test_the_claude_cli_receives_the_effort_as_its_effort_flag(monkeypatch, tmp_path):
    from llm.claude_code import ClaudeCodeHarness
    worker = _worker(monkeypatch, tmp_path, choice="claude", effort="xhigh")
    assert worker["VEXA_RUNNER"] == "claude-code" and worker["VEXA_AGENT_EFFORT"] == "xhigh"
    _isolate(monkeypatch, worker)
    argvs: list[list[str]] = []

    def fake_cli(argv, cwd):
        argvs.append(list(argv))
        yield json.dumps({"type": "result", "subtype": "success", "result": "ok", "session_id": "s"})

    list(ClaudeCodeHarness(exec_fn=fake_cli).run_turn(tmp_path, "hi"))
    argv = argvs[0]
    assert argv[argv.index("--effort") + 1] == "xhigh"


def test_no_effort_rides_into_a_route_whose_model_did_not_offer_it(monkeypatch, tmp_path):
    """A person's Settings → Models effort (or the deployment's) never reaches a catalog route: on
    a model with no effort control, nothing is sent; on one with levels, only the picked level."""
    worker = _worker(monkeypatch, tmp_path, choice="claude", model_config={"effort": "max"})
    assert worker["VEXA_AGENT_EFFORT"] == ""


@pytest.mark.parametrize("choice, effort", [("claude", "none"), ("or-sonnet", "xhigh"),
                                            ("qwen3-32b", "medium")])
def test_an_effort_the_model_does_not_offer_refuses_the_turn_typed(monkeypatch, tmp_path, choice, effort):
    with pytest.raises(ModelChoiceFault) as exc:
        _spec(monkeypatch, tmp_path, choice=choice, effort=effort)
    f = exc.value.as_dict()
    assert (f["source"], f["kind"], f["model"]) == ("model-provider", "effort_unsupported", choice)
    assert exc.value.http_status == 422


# ── the context budget is the model's own window, never an empty stamp (app.dev, 2026-10-10) ───

@pytest.mark.parametrize("backend", ["process", "docker", "k8s"])
def test_an_unpicked_chat_runs_on_the_default_entrys_window(monkeypatch, tmp_path, backend):
    """No pick: the catalog's default (qwen3-32b, a 32768 window) decides the budget."""
    worker = _worker(monkeypatch, tmp_path, backend, choice="")
    assert worker["VEXA_AGENT_CONTEXT_TOKENS"] == str(32768 - 8192)


@pytest.mark.parametrize("backend", ["process", "docker"])
def test_an_entry_with_no_window_never_erases_the_deployments_budget(monkeypatch, tmp_path, backend):
    """An entry that names no window leaves the key to the deployment: the runtime's forwarded
    VEXA_AGENT_CONTEXT_TOKENS reaches the worker, never an empty stamp that wins over it."""
    worker = _worker(monkeypatch, tmp_path, backend, choice="or-sonnet")
    assert worker["VEXA_AGENT_CONTEXT_TOKENS"] == "24000"        # the deployment's, from _deployment()


def test_the_route_never_stamps_an_empty_context_budget(monkeypatch, tmp_path):
    for choice in ("qwen3-32b", "or-sonnet", "claude", ""):
        spec = _spec(monkeypatch, tmp_path, choice=choice)
        assert spec.get("VEXA_AGENT_CONTEXT_TOKENS", "unset") != "", choice


def test_an_entrys_output_cap_is_the_room_kept_for_the_answer():
    from control_plane.dispatch import context_budget
    assert context_budget(131072) == 131072 - 8192
    assert context_budget(131072, 4096) == 131072 - 4096
    assert context_budget(None) is None
