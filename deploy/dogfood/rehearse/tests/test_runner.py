"""`rehearse(..., runner=…)` — the harness is a per-subject dial (PRD decisions 37 + 38).

THE CHAIN THIS FILE OWNS HALF OF. A rehearsed state on Qwen has to end with a worker whose env
reads `VEXA_RUNNER=openai-agent` and whose model is `qwen3.8-27b`, and that runs through two
services:

    rehearse(runner=…)  →  PUT /admin/users/{id}/models   ← THIS FILE: the exact config, on the
                                                            scratch subject and on nobody else
    that config         →  worker env                     ← core/agent/tests/test_runner_per_subject.py

Neither half can prove the other, and a test that mocked across the seam would prove nothing at
all. So each half asserts the same literal config, and the two docstrings name each other.
"""
from __future__ import annotations

import pytest

from rehearse.doors import RUNNER_DIALS, DoorRefused, runner_config
from rehearse.engine import Refused, rehearse
from rehearse.stub_doors import StubDoors

QWEN = {"runner": "openai-agent", "mode": "custom",
        "base_url": "http://192.168.1.6:8001/v1", "model": "qwen3.8-27b",
        "extra_body": '{"chat_template_kwargs":{"enable_thinking":false}}'}


def test_the_qwen_dials_are_exactly_what_the_worker_half_expects():
    """The literal `core/agent/tests/test_runner_per_subject.py` feeds to `overlay_model_config`."""
    assert runner_config("openai-agent") == QWEN


def test_an_unknown_runner_is_refused_with_the_names_that_work():
    with pytest.raises(DoorRefused, match="not a runner this tool knows"):
        runner_config("qwen-magic")
    assert "openai-agent" in str(RUNNER_DIALS)


def test_the_name_is_refused_before_a_single_door_is_touched(catalog, env):
    doors = StubDoors()
    with pytest.raises(DoorRefused):
        rehearse("organizer-invited", "x@rehearse.test", doors=doors, catalog=catalog, env=env,
                 runner="typo-agent")
    assert doors.calls == []


def test_every_subject_the_recipe_resolves_is_bound_and_nobody_else(catalog, env):
    doors = StubDoors()
    doors.users["dmitry@vexa.ai"] = "126"          # the founder, already on the instance
    who = "rehearse-group-member@rehearse.test"
    res = rehearse("group-member", who, doors=doors, catalog=catalog, env=env,
                   runner="openai-agent")
    assert res.ok, res.error
    bound = set(doors.runners)
    assert bound == {doors.user_find(who), doors.user_find(f"organizer-{who.split('@')[0]}@rehearse.test")}
    assert "126" not in bound, "the founder's dispatches must be untouched"
    assert all(cfg == QWEN for cfg in doors.runners.values())


def test_the_binding_happens_before_anything_could_dispatch_for_that_subject(catalog, env):
    """Binding after the steps would leave the turns the recipe itself triggers — the post-meeting
    run, the prepare compose — on the deployment's default, which is the thing being measured."""
    doors = StubDoors()
    rehearse("reply-pending", "rehearse-reply-pending@rehearse.test", doors=doors, catalog=catalog,
             env=env, runner="openai-agent")
    kinds = [c[0] for c in doors.calls]
    assert kinds.index("bind_runner") < kinds.index("emit_fact")


def test_no_runner_binds_nothing(catalog, env):
    doors = StubDoors()
    res = rehearse("organizer-invited", "x@rehearse.test", doors=doors, catalog=catalog, env=env)
    assert res.ok and doors.runners == {}


def test_claude_code_clears_the_custom_dials_rather_than_leaving_them_set():
    """Naming the deployment's own harness must UNSET the endpoint and the model, not just the
    runner — a subject left pointing at Qwen while claiming to run the default is worse than one
    that was never rebound. Empty strings are admin-api's clear."""
    cfg = runner_config("claude-code")
    assert cfg["runner"] == "claude-code"
    assert cfg["base_url"] == "" and cfg["model"] == "" and cfg["extra_body"] == ""


def test_the_endpoint_is_a_deployment_fact_not_a_constant(monkeypatch):
    """A different Qwen box, or a different model, is an env var — never an edit to this package."""
    monkeypatch.setenv("VEXA_REHEARSE_LLM_BASE_URL", "http://10.0.0.9:8001/v1")
    monkeypatch.setenv("VEXA_REHEARSE_LLM_MODEL", "qwen3.8-72b")
    import importlib

    from rehearse import doors as doors_mod
    importlib.reload(doors_mod)
    try:
        cfg = doors_mod.runner_config("openai-agent")
        assert cfg["base_url"] == "http://10.0.0.9:8001/v1"
        assert cfg["model"] == "qwen3.8-72b"
    finally:
        monkeypatch.undo()
        importlib.reload(doors_mod)


def test_a_runner_binding_still_refuses_a_real_address(catalog, env):
    """The domain guard runs first and is not weakened by the new argument."""
    with pytest.raises(Refused):
        rehearse("organizer-invited", "dmitry@vexa.ai", doors=StubDoors(), catalog=catalog,
                 env=env, runner="openai-agent")
