import pytest

from meeting_signals.policy import parse_model, resolve

MODEL = {"adapter": "jev", "route": "typesafe/jev-1.13", "secret_ref": "broker:signals/openrouter"}


def test_off_by_default_and_off_without_a_model():
    assert not resolve(None)[0].active
    assert not resolve({"transcript_signals": "on"})[0].active
    assert not resolve({"model_signals": MODEL})[0].active
    assert not resolve({"transcript_signals": "off", "model_signals": MODEL})[0].active


def test_on_with_a_model_as_a_map_json_or_flow_map():
    p, problems = resolve({"transcript_signals": "on", "model_signals": MODEL})
    assert p.active and p.model["adapter"] == "jev" and not problems
    import json
    assert resolve({"transcript_signals": True, "model_signals": json.dumps(MODEL)})[0].active
    flow = "{adapter: openai-compatible, route: small, base_url: https://llm.internal/v1, secret_ref: none}"
    p, _ = resolve({"transcript_signals": "on", "model_signals": flow})
    assert p.active and p.model == {"adapter": "openai-compatible", "route": "small",
                                    "base_url": "https://llm.internal/v1", "secret_ref": "none"}


@pytest.mark.parametrize("bad", [
    '{"adapter": "gpt", "route": "x"}',
    '{"adapter": "jev"}',
    '{"adapter": "jev", "route": "x", "secret_ref": "env:OPENROUTER_API_KEY"}',
    '{"adapter": "jev", "route": "x", "api_key": "sk-x"}',
    '{"adapter": "jev", "route": "x", "base_url": "https://x"}',
    'not a map',
])
def test_a_bad_model_keeps_signals_off(bad):
    p, problems = resolve({"transcript_signals": "on", "model_signals": bad})
    assert not p.active and problems
    assert all("sk-x" not in pr for pr in problems)


def test_caps_default_and_ceilings():
    p, _ = resolve({"transcript_signals": "on", "model_signals": MODEL})
    assert (p.wakes_cap, p.cost_cap_usd) == (12, 0.5)
    p, problems = resolve({"transcript_signals": "on", "model_signals": MODEL, "signals_wakes_per_meeting": 9999,
                           "signals_cost_per_meeting_usd": "0.10"})
    assert p.wakes_cap == 500 and p.cost_cap_usd == 0.10 and problems
    p, problems = resolve({"signals_wakes_per_meeting": "lots", "signals_cost_per_meeting_usd": -1})
    assert (p.wakes_cap, p.cost_cap_usd) == (12, 0.5) and len(problems) == 2


def test_parse_model_strips_trailing_slash():
    assert parse_model('{"adapter":"openai-compatible","route":"m","base_url":"http://h/v1/"}')["base_url"] == "http://h/v1"


# ── thinking: off|on for a self-hosted reasoning model (founder decision 14) ──────────────────

def test_thinking_off_is_accepted_for_the_openai_compatible_adapter():
    m = parse_model('{adapter: openai-compatible, route: qwen3.8-27b, secret_ref: none, '
                    'base_url: "http://192.168.1.6:8001/v1", thinking: off}')
    assert m["thinking"] == "off" and m["base_url"] == "http://192.168.1.6:8001/v1"
    assert "thinking" not in parse_model('{adapter: openai-compatible, route: m, secret_ref: none}')


@pytest.mark.parametrize("bad", ['{adapter: openai-compatible, route: m, secret_ref: none, thinking: maybe}',
                                 '{adapter: jev, route: m, secret_ref: none, thinking: off}'])
def test_thinking_is_off_or_on_and_only_for_openai_compatible(bad):
    with pytest.raises(ValueError, match="thinking"):
        parse_model(bad)


def test_the_self_hosted_model_as_agent_api_answers_it_is_the_one_called():
    """Live app.dev 2026-10-18 (meeting 162): agent-api dropped `base_url`/`thinking`, so the qwen
    route was called at OpenRouter and every call answered 401. agent-api now answers the map whole
    (`meeting_listen.policy`, its test pins this same dict); signals must call THAT endpoint."""
    answered = {"transcript_signals": "on", "signals_wakes_per_meeting": 200,
                "model_signals": {"adapter": "openai-compatible", "route": "qwen3.8-27b", "secret_ref": "none",
                                  "base_url": "http://192.168.1.6:8001/v1", "thinking": "off"}}
    p, problems = resolve(answered)
    assert p.active and not problems
    assert p.model["base_url"] == "http://192.168.1.6:8001/v1" and p.model["thinking"] == "off"
