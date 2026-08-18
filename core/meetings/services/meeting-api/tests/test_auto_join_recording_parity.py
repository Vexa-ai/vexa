"""A calendar-joined bot records exactly like a manual one (#1216).

The defect, proven on stage rev 194: the auto-join sweep called ``request_bot`` without
``recording_enabled``, so it inherited that parameter's ``False`` default → ``capture_modes=None``
→ no recording pipeline at all, ``data.recording_enabled = false``, and a dashboard that renders
"No audio recording for this meeting" as if that were the normal outcome. The HTTP path resolved
the same flag from the request body else ``RECORDING_ENABLED`` (default true), so **manual bots
recorded and calendar bots never did** — 10 of 10 auto-joined rows unrecorded. Evidence pair:
meeting 26353 (auto, ``BOT_CONFIG.recordingEnabled=False``, 0 recordings) against 26354 (manual,
True, ``master.webm`` 893KB).

Founder ruling 2026-08-17: the split default is not a policy, it is a bug — calendar bots record
like manual bots. There is no per-user recording setting today and this pins none: parity with the
env default is the whole contract.

The second class of test here is the one that keeps it fixed: the sweep and the route must resolve
recording through the SAME function, so a future change to one cannot silently move only one of
them. That is why ``resolve_spawn_flag`` lives in ``env_flags`` rather than in ``router``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from meeting_api.bot_spawn.auto_join import auto_join_tick
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.router import (
    _resolve_recording_enabled,
    _resolve_transcribe_enabled,
)

USER = 7
PLAT, NID = "google_meet", "abc-defg-hij"
NOW = datetime(2026, 8, 17, 15, 0, 0, tzinfo=timezone.utc)

_NO_GATE = lambda: None  # noqa: E731 — these tests bypass the STT capability gate


def _seed(repo, *, mid=1):
    repo._meetings[mid] = {
        "id": mid, "user_id": USER, "platform": PLAT,
        "native_meeting_id": NID, "platform_specific_id": NID,
        "status": "scheduled", "bot_container_id": None, "start_time": None, "end_time": None,
        "data": {"title": "t", "auto_join": True, "scheduled_at": NOW.isoformat()},
        "created_at": "2026-08-17T09:00:00Z", "updated_at": "2026-08-17T09:00:00Z",
    }
    return mid


async def _sweep_bot_config(monkeypatch, **env):
    """One sweep tick; returns (BOT_CONFIG dict the runtime was handed, meeting.data)."""
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo)
    counters = await auto_join_tick(
        repo, runtime, transcribe_gate=_NO_GATE, now=NOW,
        token_secret="s", redis_url="redis://r", allow_uncapped=True,
    )
    assert counters["spawned"] == 1, counters
    config = json.loads(runtime.specs[0]["env"]["VEXA_BOT_CONFIG"])
    return config, repo._meetings[mid]["data"]


# ---- (1) the sweep records by default, and honours an explicit opt-out ----------------------

async def test_sweep_dispatched_bot_records_under_default_env(monkeypatch):
    """THE regression: with no ``RECORDING_ENABLED`` set at all, a calendar bot records."""
    config, data = await _sweep_bot_config(monkeypatch, RECORDING_ENABLED=None)
    assert config["recordingEnabled"] is True, (
        "a calendar-joined bot must record like a manual one (#1216) — "
        "recordingEnabled=False is the stage rev 194 defect"
    )
    assert config["captureModes"] == ["audio", "video"], (
        "recording_enabled=False collapses capture_modes to None — no recording pipeline runs"
    )
    assert data["recording_enabled"] is True, "the meeting row must not read back unrecorded"


async def test_sweep_dispatched_bot_records_when_env_set_but_empty(monkeypatch):
    """``RECORDING_ENABLED=`` from an --env-file is not an explicit opt-out (the v0.12.5 shape)."""
    config, _ = await _sweep_bot_config(monkeypatch, RECORDING_ENABLED="")
    assert config["recordingEnabled"] is True


async def test_sweep_respects_an_explicit_recording_opt_out(monkeypatch):
    """The deployment that says false still gets false — parity means parity in both directions."""
    config, data = await _sweep_bot_config(monkeypatch, RECORDING_ENABLED="false")
    assert config["recordingEnabled"] is False
    assert config.get("captureModes") is None  # None-stripped from the invocation
    assert data["recording_enabled"] is False


async def test_sweep_transcribes_by_default_and_respects_the_opt_out(monkeypatch):
    """Same inherited-default class, same fix: the sweep read no ``TRANSCRIBE_ENABLED`` at all, so
    ``TRANSCRIBE_ENABLED=false`` disabled transcription for POST /bots but not for the sweep."""
    config, _ = await _sweep_bot_config(monkeypatch, TRANSCRIBE_ENABLED=None)
    assert config["transcribeEnabled"] is True
    config, _ = await _sweep_bot_config(monkeypatch, TRANSCRIBE_ENABLED="false")
    assert config["transcribeEnabled"] is False


# ---- (2) the two spawners cannot drift apart again ------------------------------------------

@pytest.mark.parametrize("raw", [None, "", "true", "false", "1", "0", "yes", "off", "maybe"])
async def test_sweep_and_route_resolve_recording_identically(monkeypatch, raw):
    """Parity, pinned across the whole env vocabulary — including the unrecognized value, which
    must keep the default on BOTH paths rather than opting either one out."""
    config, _ = await _sweep_bot_config(monkeypatch, RECORDING_ENABLED=raw)
    route = _resolve_recording_enabled(None)  # what POST /bots would send with no body value
    assert config["recordingEnabled"] is route, (
        f"RECORDING_ENABLED={raw!r}: the sweep spawned recordingEnabled="
        f"{config['recordingEnabled']} while POST /bots would spawn {route}"
    )


@pytest.mark.parametrize("raw", [None, "", "true", "false", "0", "maybe"])
async def test_sweep_and_route_resolve_transcription_identically(monkeypatch, raw):
    config, _ = await _sweep_bot_config(monkeypatch, TRANSCRIBE_ENABLED=raw)
    assert config["transcribeEnabled"] is _resolve_transcribe_enabled(None)


def test_both_route_resolvers_are_the_shared_resolver():
    """Structural half of the parity guarantee: one source of truth, not two copies that agree
    today. A copy-paste divergence is exactly how #1216 shipped."""
    import inspect

    from meeting_api.bot_spawn import env_flags

    for fn in (_resolve_recording_enabled, _resolve_transcribe_enabled):
        assert "resolve_spawn_flag" in inspect.getsource(fn), (
            f"{fn.__name__} must delegate to env_flags.resolve_spawn_flag, never re-implement it"
        )
    assert callable(env_flags.resolve_spawn_flag)
