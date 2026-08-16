"""L1 — the environment → ``Settings``, and the composition root's degrade path.

The workspace map is the one setting that decides product behaviour, so its parsing is pinned
here: a malformed pair is dropped rather than resolving to something surprising, and a mailroom
with no map does not run the poller at all (a mailbox that resolves nothing must not appear to
work).
"""
from __future__ import annotations

from vexa_mailroom import settings_from_env
from vexa_mailroom.config import parse_workspace_map
from vexa_mailroom.__main__ import build

BASE = {
    "MAILPIT_URL": "http://mailpit:8025",
    "MEETING_API_URL": "http://gateway:8000",
    "MAILROOM_API_KEY": "key-123",
    "MAILROOM_WORKSPACE_MAP": "mk-dev@dev.vexa.ai=ws-mk-dev",
}


def test_settings_from_env():
    s = settings_from_env({**BASE, "MAILROOM_POLL_INTERVAL_S": "5", "PORT": "8031"})
    assert s.mailpit_url == "http://mailpit:8025"
    assert s.workspaces == {"mk-dev@dev.vexa.ai": "ws-mk-dev"}
    assert s.poll_interval_s == 5.0
    assert s.port == 8031
    assert s.auto_join is True and s.dry_run is False
    assert s.configured is True


def test_internal_secret_reaches_the_app():
    """The guard is only real if the composition root passes it on (it once did not)."""
    s = settings_from_env({**BASE, "MAILROOM_INTERNAL_SECRET": "shh"})
    assert s.internal_secret == "shh"


def test_single_pair_shorthand():
    s = settings_from_env({"MAILROOM_WORKSPACE_ADDRESS": "MK-Dev@dev.vexa.ai",
                           "MAILROOM_WORKSPACE_ID": "ws-mk-dev"})
    assert s.workspaces == {"mk-dev@dev.vexa.ai": "ws-mk-dev"}


def test_workspace_map_parsing():
    assert parse_workspace_map("a@x=ws-1, b@x = ws-2") == {"a@x": "ws-1", "b@x": "ws-2"}
    assert parse_workspace_map("garbage, =ws, a@x=, @x=ws") == {}
    assert parse_workspace_map(None) == {}


def test_build_degrades_instead_of_crashing_when_unconfigured():
    _s, mailroom, reason = build(settings_from_env({"MAILPIT_URL": "http://mailpit:8025"}))
    assert mailroom is None
    assert "MAILROOM_API_KEY" in reason and "MAILROOM_WORKSPACE_MAP" in reason


def test_build_wires_the_real_ports(tmp_path):
    _s, mailroom, reason = build(settings_from_env(
        {**BASE, "MAILROOM_STATE_PATH": str(tmp_path / "state.json")}))
    assert reason == "" and mailroom is not None
    assert mailroom.workspaces == {"mk-dev@dev.vexa.ai": "ws-mk-dev"}
    assert type(mailroom.meetings).__name__ == "MeetingApiClient"


def test_dry_run_swaps_the_control_plane_for_a_recorder(tmp_path):
    _s, mailroom, _reason = build(settings_from_env(
        {**BASE, "MAILROOM_DRY_RUN": "1", "MAILROOM_STATE_PATH": str(tmp_path / "state.json")}))
    assert type(mailroom.meetings).__name__ == "_DryRunMeetingApi"


def test_dry_run_needs_no_api_key(tmp_path):
    _s, mailroom, reason = build(settings_from_env(
        {"MAILPIT_URL": "http://mailpit:8025", "MAILROOM_DRY_RUN": "true",
         "MAILROOM_WORKSPACE_MAP": "mk-dev@dev.vexa.ai=ws-mk-dev",
         "MAILROOM_STATE_PATH": str(tmp_path / "state.json")}))
    assert reason == "" and mailroom is not None
