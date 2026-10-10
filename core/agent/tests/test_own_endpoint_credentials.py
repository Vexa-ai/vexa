"""A PERSON'S OWN MODEL ENDPOINT RECEIVES THE PERSON'S CREDENTIAL AND NOTHING ELSE — on every harness.

The environment half has held since #1783: on a person's own route the dispatch stamps every key
either harness reads, the empty string included, so no deployment key reaches that endpoint. This
pins the other half — the credential a CLI keeps in its config directory:

1. the dispatch marks the person's route (`VEXA_MODEL_ROUTE=subject`) and no other;
2. on that route the claude-code harness removes the stored credential before the CLI starts, and
   refuses the turn when it cannot — the path it clears is the one the runtime stages a
   subscription to in a process-backend HOME;
3. a harness that would sign in from its config directory when it has no key runs on a person's
   endpoint only with that person's key: the dispatch refuses the endpoint otherwise (the deployment
   route applies and a friction record says why), and the Test button refuses it the same way.

The openai-agent harness reads no file (its keyless case sends no credential at all —
`test_subject_endpoint_route.py`), and the codex harness does not reach a person's endpoint.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

import pytest

from control_plane import config_test, model_endpoint
from control_plane.dispatch import build_unit_env, subject_route_env
from llm import claude_code
from llm.claude_code import CREDENTIAL_FILE, ClaudeCodeHarness, cli_credential_path
from shared.config import load_settings

REPO = Path(__file__).resolve().parents[3]
OWN = "https://openrouter.ai/api"
INV = {"identity": {"subject": "u_2", "launcher": "user:u_2"}, "runner": "claude-code",
       "workspaces": [{"id": "u_2", "mode": "rw"}], "trigger": "message",
       "start": {"entrypoint": {"inline": "hi"}}}


def _runtime_staged_claude_path() -> str:
    """Where the runtime stages the claude subscription in a process-backend HOME, read from its
    source — this domain does not import the runtime's modules (gate:test-isolation)."""
    text = (REPO / "core" / "runtime" / "src" / "runtime_kernel" / "profiles.py").read_text()
    name = re.search(r'^CLAUDE_CREDENTIALS_FILENAME = "([^"]+)"', text, re.M).group(1)
    prefix = re.search(r'home_path=f"([^"{]*)\{CLAUDE_CREDENTIALS_FILENAME\}"', text).group(1)
    return prefix + name


# ── 1. the dispatch marks the person's route, and only that one ─────────────────────────────────

def test_the_persons_own_route_is_marked():
    assert subject_route_env(OWN, "k", "")["VEXA_MODEL_ROUTE"] == "subject"


def test_the_deployments_route_carries_no_mark(tmp_path, monkeypatch):
    monkeypatch.delenv("VEXA_MODEL_ROUTE", raising=False)
    env = build_unit_env(load_settings(workspaces_dir=str(tmp_path)), INV, unit_id="u",
                         token="t", model_config={"mode": "subscription"})
    assert "VEXA_MODEL_ROUTE" not in env


# ── 2. the claude-code harness leaves the CLI no stored credential on that route ────────────────

def _home(tmp_path, monkeypatch, *, route: str | None) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    cred = home / ".claude" / CREDENTIAL_FILE
    cred.write_text('{"claudeAiOauth": {"accessToken": "deployment-subscription-placeholder"}}')
    monkeypatch.setenv("HOME", str(home))
    if route is None:
        monkeypatch.delenv("VEXA_MODEL_ROUTE", raising=False)
    else:
        monkeypatch.setenv("VEXA_MODEL_ROUTE", route)
    return cred


def _run(tmp_path) -> tuple[list[dict], list[bool]]:
    seen: list[bool] = []

    def exec_fn(argv, cwd):
        seen.append(cli_credential_path().exists())
        yield json.dumps({"type": "result", "result": "ok", "session_id": "s"})

    events = list(ClaudeCodeHarness(exec_fn=exec_fn).run_turn(tmp_path, "hi"))
    return events, seen


def test_on_the_persons_route_the_cli_starts_with_no_stored_credential(tmp_path, monkeypatch):
    cred = _home(tmp_path, monkeypatch, route="subject")
    events, seen = _run(tmp_path)
    assert seen == [False], "the CLI started beside the deployment's stored credential"
    assert not cred.exists()
    assert events[-1]["ok"] is True


def test_a_stored_credential_that_cannot_be_removed_refuses_the_turn(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("root removes a file from a read-only directory")
    cred = _home(tmp_path, monkeypatch, route="subject")
    cred.parent.chmod(stat.S_IRUSR | stat.S_IXUSR)          # read-only, like a mounted Secret
    try:
        events, seen = _run(tmp_path)
    finally:
        cred.parent.chmod(stat.S_IRWXU)
    assert seen == [], "the CLI was started"
    assert events[-1]["type"] == "done" and events[-1]["ok"] is False
    # TYPED (P18, unit.v1 `Fault` — architecture pass 6, S66): the worker refused, not the provider, and the
    # chat says so with the operator's remedy rather than a prose reply alone.
    assert events[-1]["fault"]["source"] == "agent-worker"
    assert events[-1]["fault"]["kind"] == "credential_conflict"
    assert "operator" in events[-1]["fault"]["remedy"]


def test_on_the_deployments_route_the_stored_credential_is_the_one_it_runs_on(tmp_path, monkeypatch):
    cred = _home(tmp_path, monkeypatch, route=None)
    _events, seen = _run(tmp_path)
    assert seen == [True] and cred.exists()


def test_the_harness_clears_where_the_runtime_stages_the_subscription():
    """The process backend stages the deployment's subscription at this path in the child's HOME;
    the harness must clear exactly that path, or the clearing protects nothing."""
    assert _runtime_staged_claude_path() == f".claude/{CREDENTIAL_FILE}"


# ── 3. no key of the person's own: the endpoint is refused, not signed into from the home ───────

def _keyless(runner: str) -> dict:
    return {"mode": "custom", "base_url": OWN, "api_key": "", "runner": runner, "model": "m"}


def test_a_keyless_own_endpoint_on_claude_code_is_refused(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_BASE_URL", "VEXA_LLM_BASE_URL", "VEXA_MODEL_BASE_URL_ALLOW"):
        monkeypatch.delenv(k, raising=False)
    reports: list[dict] = []
    env = build_unit_env(load_settings(workspaces_dir=str(tmp_path)), INV, unit_id="u", token="t",
                         model_config=_keyless("claude-code"), friction=reports.append)
    assert env.get("ANTHROPIC_BASE_URL") != OWN and "VEXA_MODEL_ROUTE" not in env
    assert reports and reports[0]["kind"] == "refusal"
    assert "API key" in reports[0]["happened"]


def test_a_keyless_own_endpoint_on_openai_agent_still_runs(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_BASE_URL", "VEXA_LLM_BASE_URL", "VEXA_MODEL_BASE_URL_ALLOW"):
        monkeypatch.delenv(k, raising=False)
    env = build_unit_env(load_settings(workspaces_dir=str(tmp_path)), INV, unit_id="u", token="t",
                         model_config=_keyless("openai-agent"))
    assert env["VEXA_LLM_BASE_URL"] == OWN and env["VEXA_LLM_API_KEY"] == ""
    assert env["VEXA_MODEL_ROUTE"] == "subject"


def test_the_deployment_runner_counts_when_the_person_names_none(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_RUNNER", "claude-code")
    cfg = {"mode": "custom", "base_url": OWN, "api_key": ""}
    assert model_endpoint.route_refusal(OWN, "", "claude-code")
    env = build_unit_env(load_settings(workspaces_dir=str(tmp_path)), INV, unit_id="u", token="t",
                         model_config=cfg)
    assert "VEXA_MODEL_ROUTE" not in env


def test_the_test_button_refuses_it_the_same_way():
    sent = []
    out = config_test.run_models_test(_keyless("claude-code"), env={},
                                      post=lambda *a: sent.append(a) or (200, "{}"))
    assert out["ok"] is False and "API key" in out["summary"] and sent == []
