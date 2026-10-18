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
