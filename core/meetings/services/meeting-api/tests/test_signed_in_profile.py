"""Only a bot signed in as a bot account, and a browser session, run as the signed-in profile (prod rung
MVP0: customers keep today's bot image); and only when VEXA_SIGNED_IN_BOT_PROFILE names it."""
import pytest

from meeting_api.bot_spawn import invocation as inv


@pytest.mark.parametrize("env,signed_in,expected", [
    ({}, True, "meeting-bot"),
    ({}, False, "meeting-bot"),
    ({"VEXA_SIGNED_IN_BOT_PROFILE": "meeting-bot-signed-in"}, False, "meeting-bot"),
    ({"VEXA_SIGNED_IN_BOT_PROFILE": "meeting-bot-signed-in"}, True, "meeting-bot-signed-in"),
    ({"VEXA_SIGNED_IN_BOT_PROFILE": "  "}, True, "meeting-bot"),
])
def test_bot_profile(env, signed_in, expected):
    assert inv.bot_profile(signed_in=signed_in, env=env) == expected


def test_the_spec_names_the_signed_in_profile_only_for_an_authenticated_invocation(monkeypatch):
    monkeypatch.setenv("VEXA_SIGNED_IN_BOT_PROFILE", "meeting-bot-signed-in")
    build = getattr(inv, "build_workload_spec", None) or getattr(inv, "workload_spec")
    guest = build(workload_id="w1", invocation={"platform": "google_meet"})
    assert guest["profile"] == "meeting-bot"
    signed = build(workload_id="w2", invocation={"platform": "google_meet", "authenticated": True})
    assert signed["profile"] == "meeting-bot-signed-in"
    monkeypatch.delenv("VEXA_SIGNED_IN_BOT_PROFILE")
    assert build(workload_id="w3", invocation={"platform": "google_meet", "authenticated": True})["profile"] == "meeting-bot"
