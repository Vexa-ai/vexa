"""L2: the claude-cli completion adapter — tool-less argv contract, JSON-result parsing, and the
error taxonomy (auth-signature → LLMAuthError; other failures → LLMError). CLI faked via run_fn."""
import json

import pytest

from llm import LLMAuthError, LLMError
from llm.claude_cli import ClaudeCliCompletion, build_argv


def _fake_run(returncode: int, payload: dict | str):
    calls = {}

    def run(argv, cwd, timeout):
        calls["argv"], calls["cwd"], calls["timeout"] = argv, cwd, timeout
        out = payload if isinstance(payload, str) else json.dumps(payload)
        return returncode, out

    return run, calls


def test_argv_is_tool_less_json_with_optional_model_and_system():
    argv = build_argv("polish these lines", system="you are a copilot", model="m1")
    assert argv[:3] == ["claude", "-p", "polish these lines"]
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--allowedTools") + 1] == ""  # verified deny-all — a beat runs NO tools
    assert argv[argv.index("--append-system-prompt") + 1] == "you are a copilot"
    assert argv[argv.index("--model") + 1] == "m1"
    # empty model ⇒ no --model flag (the subscription default decides)
    assert "--model" not in build_argv("p")


def test_completes_and_runs_from_neutral_cwd(monkeypatch):
    monkeypatch.delenv("VEXA_LLM_MODEL", raising=False)
    run, calls = _fake_run(0, {"result": "polished notes", "is_error": False, "subtype": "success"})
    result = ClaudeCliCompletion(run_fn=run).complete("polish", model="")
    assert result.text == "polished notes"
    assert result.model == "subscription-default"
    assert calls["cwd"] == "/tmp"  # never the workspace — beats must not load project memory


def test_tolerates_log_noise_around_the_json():
    run, _ = _fake_run(0, 'some warning line\n{"result": "ok", "is_error": false}\ntrailing')
    assert ClaudeCliCompletion(run_fn=run).complete("p").text == "ok"


def test_auth_failure_raises_typed_auth_error():
    run, _ = _fake_run(0, {"result": "API Error: 401 Invalid authentication credentials",
                           "is_error": True, "subtype": "error"})
    with pytest.raises(LLMAuthError):
        ClaudeCliCompletion(run_fn=run).complete("p")


def test_non_auth_failure_raises_llm_error():
    run, _ = _fake_run(1, {"result": "upstream timeout (504)", "is_error": True})
    with pytest.raises(LLMError) as exc:
        ClaudeCliCompletion(run_fn=run).complete("p")
    assert "504" in str(exc.value)


def test_garbage_output_is_an_error_not_a_crash():
    run, _ = _fake_run(0, "not json at all")
    with pytest.raises(LLMError):
        ClaudeCliCompletion(run_fn=run).complete("p")


def test_registry_selects_claude_cli(monkeypatch):
    from llm import completion_from_env
    monkeypatch.setenv("VEXA_LLM_PROVIDER", "claude-cli")
    assert isinstance(completion_from_env(), ClaudeCliCompletion)
