"""The deployment default for the captured-signal tape (``VEXA_CAPTURE_SIGNAL_DEFAULT``).

The Helm chart ships it ``false``: an enterprise install tapes nothing unless an operator turns it
on. meeting-api reads it in exactly one case — identity gave no decision (unreachable, or an
admin-api that predates the field) — so that case can no longer turn taping ON behind the operator's
back. A reachable identity's explicit boolean is still final. Compose and Lite leave it unset, which
keeps their behaviour (ON) byte-for-byte.
"""
from __future__ import annotations

import json

import pytest

from meeting_api.bot_spawn import request_bot
from meeting_api.bot_spawn.env_flags import InvalidDeploymentFlag, capture_signal_default
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

SECRET = "test-admin-token"
USER = 7


async def _spawned_capture(monkeypatch, ctx, slug):
    from meeting_api.bot_spawn import service as spawn_service

    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    if ctx is not None:
        async def fake_ctx(_user_id):
            return ctx
        monkeypatch.setattr(spawn_service, "_fetch_bot_context", fake_ctx)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                      native_meeting_id=f"cap-{slug}", redis_url="redis://redis:6379/0",
                      token_secret=SECRET)
    return json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])["captureSignalEnabled"]


async def test_an_unreachable_identity_does_not_turn_tapes_on_where_the_deployment_said_off(monkeypatch):
    # Deny: the old code defaulted ON here regardless of the deployment, so a Helm install whose
    # identity blipped taped meetings nobody had agreed to tape.
    monkeypatch.setenv("VEXA_CAPTURE_SIGNAL_DEFAULT", "false")
    assert await _spawned_capture(monkeypatch, None, "unreachable") is False


@pytest.mark.parametrize("ctx,slug", [({}, "absent"), ({"capture_signal": "true"}, "str")])
async def test_no_boolean_answer_falls_to_the_deployment_default_off(monkeypatch, ctx, slug):
    monkeypatch.setenv("VEXA_CAPTURE_SIGNAL_DEFAULT", "false")
    assert await _spawned_capture(monkeypatch, ctx, slug) is False


async def test_an_explicit_identity_answer_still_wins_over_the_deployment_default(monkeypatch):
    # An operator who turned taping on for one account (platform or per-user setting) gets it.
    monkeypatch.setenv("VEXA_CAPTURE_SIGNAL_DEFAULT", "false")
    assert await _spawned_capture(monkeypatch, {"capture_signal": True}, "explicit-on") is True
    monkeypatch.setenv("VEXA_CAPTURE_SIGNAL_DEFAULT", "true")
    assert await _spawned_capture(monkeypatch, {"capture_signal": False}, "explicit-off") is False


async def test_unset_keeps_the_compose_and_lite_behaviour(monkeypatch):
    monkeypatch.delenv("VEXA_CAPTURE_SIGNAL_DEFAULT", raising=False)
    assert await _spawned_capture(monkeypatch, None, "unset") is True


@pytest.mark.parametrize("raw,expected", [
    ("", True), ("  ", True), ("true", True), ("1", True), ("ON", True),
    ("false", False), ("0", False), ("no", False), ("Off", False),
])
def test_the_switch_reads_the_boolean_vocabulary(raw, expected):
    assert capture_signal_default(raw) is expected


@pytest.mark.parametrize("raw", ["flase", "disabled", "2"])
def test_a_typo_in_the_switch_refuses_the_boot(raw):
    from meeting_api.__main__ import _capture_signal_default_at_boot
    from meeting_api.config_preflight import ConfigError

    with pytest.raises(InvalidDeploymentFlag):
        capture_signal_default(raw)
    with pytest.raises(ConfigError, match="VEXA_CAPTURE_SIGNAL_DEFAULT"):
        _capture_signal_default_at_boot({"VEXA_CAPTURE_SIGNAL_DEFAULT": raw})
