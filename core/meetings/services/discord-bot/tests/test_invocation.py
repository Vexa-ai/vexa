"""invocation.v1 parsing/validation — the adapter's own boot-config seam."""

import json

import pytest

from discord_bot.contracts import SPAWNABLE_PLATFORMS, conforms_invocation
from discord_bot.invocation import InvocationError, load_invocation, parse_invocation


def _valid_config() -> dict:
    return {
        "platform": "discord",
        "meetingUrl": "https://discord.com/channels/111111111111111111/222222222222222222",
        "botName": "Vexa",
        "nativeMeetingId": "222222222222222222",
        "redisUrl": "redis://redis:6379",
        "connectionId": "sess-uid",
        "meetingApiCallbackUrl": "http://meeting-api:8080/runtime/callback",
    }


def test_discord_is_a_spawnable_platform():
    # invocation.v1's sealed Platform enum, read straight from the schema (#875).
    assert "discord" in SPAWNABLE_PLATFORMS


def test_parse_invocation_accepts_a_conforming_discord_config():
    inv = parse_invocation(json.dumps(_valid_config()))
    assert inv["platform"] == "discord"
    assert inv["nativeMeetingId"] == "222222222222222222"
    conforms_invocation(inv)  # must also satisfy the sealed schema directly


def test_parse_invocation_missing_env_is_fatal():
    with pytest.raises(InvocationError, match="missing or empty"):
        parse_invocation(None)
    with pytest.raises(InvocationError, match="missing or empty"):
        parse_invocation("   ")


def test_parse_invocation_bad_json_is_fatal():
    with pytest.raises(InvocationError, match="not valid JSON"):
        parse_invocation("{not json")


def test_parse_invocation_schema_violation_is_fatal():
    bad = _valid_config()
    del bad["redisUrl"]  # required by invocation.v1
    with pytest.raises(InvocationError, match="failed validation"):
        parse_invocation(json.dumps(bad))


def test_parse_invocation_rejects_wrong_platform():
    wrong = _valid_config()
    wrong["platform"] = "zoom"
    with pytest.raises(InvocationError, match="not 'discord'"):
        parse_invocation(json.dumps(wrong))


def test_parse_invocation_rejects_platform_outside_the_sealed_enum():
    bad = _valid_config()
    bad["platform"] = "webex"
    with pytest.raises(InvocationError, match="failed validation"):
        parse_invocation(json.dumps(bad))


def test_load_invocation_reads_vexa_bot_config_env():
    env = {"VEXA_BOT_CONFIG": json.dumps(_valid_config())}
    inv = load_invocation(env)
    assert inv["platform"] == "discord"


def test_load_invocation_falls_back_to_legacy_bot_config_alias():
    """build_workload_spec (meeting-api) emits BOTH VEXA_BOT_CONFIG and the legacy BOT_CONFIG
    alias; this service is authoritative on the former but must still boot under the latter."""
    env = {"BOT_CONFIG": json.dumps(_valid_config())}
    inv = load_invocation(env)
    assert inv["platform"] == "discord"


def test_load_invocation_prefers_vexa_bot_config_over_legacy_alias():
    good = _valid_config()
    bad_alias = _valid_config()
    bad_alias["platform"] = "zoom"
    env = {"VEXA_BOT_CONFIG": json.dumps(good), "BOT_CONFIG": json.dumps(bad_alias)}
    inv = load_invocation(env)
    assert inv["platform"] == "discord"
