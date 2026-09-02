"""The harness is a PER-SUBJECT dial (PRD decisions 37 + 38), not a deployment-wide one.

`VEXA_RUNNER` was read from the process environment, so trying a different harness meant changing
it for every person on the instance. Rehearsing a state on Qwen while the founder keeps running on
the deployment's default is only possible if the dial rides the same per-subject config the model
and the endpoint already ride — and these tests are what says it does.
"""
from control_plane.dispatch import overlay_model_config
from shared.units import RUNNERS


def _base() -> dict:
    """The env as `build_unit_env` builds it before the overlay runs — VEXA_RUNNER already set
    from the dispatch, which is what the overlay has to be able to beat."""
    return {"VEXA_RUNNER": "claude-code"}


def test_a_subject_config_with_a_runner_wins_over_the_dispatch_default():
    env = _base()
    overlay_model_config(env, {"runner": "openai-agent"})
    assert env["VEXA_RUNNER"] == "openai-agent"


def test_the_qwen_dials_reach_the_worker_together():
    """The whole decision-37 target in one config: our harness, the CCC endpoint, the model, and
    the extra_body without which vLLM/Qwen returns no valid JSON at all."""
    env = _base()
    overlay_model_config(env, {
        "runner": "openai-agent", "mode": "custom",
        "base_url": "http://192.168.1.6:8001/v1", "model": "qwen3.8-27b",
        "extra_body": '{"chat_template_kwargs":{"enable_thinking":false}}'})
    assert env["VEXA_RUNNER"] == "openai-agent"
    # ONE endpoint, stamped ONCE (PRD decision 34). The harness reads
    # `VEXA_LLM_BASE_URL or ANTHROPIC_BASE_URL` and `VEXA_LLM_API_KEY or ANTHROPIC_AUTH_TOKEN`,
    # and takes the model from `VEXA_LLM_MODEL or VEXA_AGENT_MODEL` — so the per-subject gateway
    # reaches it through these without a second pair in a second dialect.
    assert env["ANTHROPIC_BASE_URL"] == "http://192.168.1.6:8001/v1"
    assert env["VEXA_AGENT_MODEL"] == "qwen3.8-27b"
    # `extra_body` is the exception: no Anthropic-dialect equivalent exists, and without it a
    # self-hosted Qwen reasons its whole budget away and returns nothing parseable.
    assert env["VEXA_LLM_EXTRA_BODY"] == '{"chat_template_kwargs":{"enable_thinking":false}}'
    # The completion pipeline's own dials stay gone: nothing reads them (decision 34).
    assert "VEXA_LLM_PROVIDER" not in env and "VEXA_MEETING_MODEL" not in env


def test_a_config_with_no_runner_leaves_the_dispatch_default_alone():
    """The founder's dispatches are untouched: no `runner` in his prefs, no change to his env."""
    env = _base()
    overlay_model_config(env, {"model": "claude-sonnet-4-5", "mode": "subscription"})
    assert env["VEXA_RUNNER"] == "claude-code"


def test_an_unknown_runner_is_dropped_not_raised():
    """Same contract as the model allowlist: a stale preference must never brick a turn."""
    env = _base()
    overlay_model_config(env, {"runner": "harness-that-does-not-exist"})
    assert env["VEXA_RUNNER"] == "claude-code"


def test_every_name_in_the_shared_list_is_accepted():
    for name in RUNNERS:
        env = _base()
        overlay_model_config(env, {"runner": name})
        assert env["VEXA_RUNNER"] == name


def test_the_runner_is_not_gated_by_the_model_allowlist():
    """The allowlist is about MODELS. Reusing it for the harness would mean an operator who pinned
    two model names had also, silently, forbidden every runner."""
    env = _base()
    overlay_model_config(env, {"runner": "openai-agent"}, allowlist="claude-sonnet-4-5")
    assert env["VEXA_RUNNER"] == "openai-agent"
