"""Per-platform capture ceiling.

Only Google Meet has a working alone-in-meeting detector (services/bot join-driver's
onEveryoneLeft is a no-op for every other platform), so elsewhere a bot whose participants
all leave runs to the ceiling and bills the whole way. Capping those platforms lower bounds
that exposure with no new DOM selectors, until each has a roster counter.
"""
import pytest

from meeting_api.zaki_control.router import ControlConfig

BASE = {
    "ZAKI_MINUTES_CONTROL_ENABLED": "true",
    "MINUTES_ENGINE_CONTROL_TOKEN": "c" * 32,
    "MINUTES_CONTROL_MAX_CAPTURE_SECONDS": "14400",
    "ZAKI_MINUTES_ADMITTED_PLATFORMS": "google_meet,teams,zoom",
}


def _config(**overrides):
    return ControlConfig.from_env({**BASE, **overrides})


def test_capped_platforms_get_their_own_ceiling_and_meet_keeps_the_deployment_max():
    config = _config(ZAKI_MINUTES_PLATFORM_CAPTURE_SECONDS="teams=3600,zoom=3600")
    assert config.capture_seconds_for("google_meet") == 14_400
    assert config.capture_seconds_for("teams") == 3_600
    assert config.capture_seconds_for("zoom") == 3_600
    # A platform absent from the map is unaffected.
    assert config.capture_seconds_for("jitsi") == 14_400


def test_unset_map_preserves_the_previous_single_ceiling_behaviour():
    config = _config()
    for platform in ("google_meet", "teams", "zoom", "jitsi"):
        assert config.capture_seconds_for(platform) == 14_400


def test_a_platform_ceiling_can_never_exceed_the_deployment_maximum():
    # Parsing refuses it outright...
    with pytest.raises(RuntimeError):
        _config(ZAKI_MINUTES_PLATFORM_CAPTURE_SECONDS="teams=99999")
    # ...and capture_seconds_for clamps defensively even if one were constructed directly,
    # because this value bounds both the reserve and the bot's lifetime.
    direct = ControlConfig(
        enabled=True, operator_enabled=True, signing_secret="c" * 32,
        max_capture_seconds=3_600, platform_capture_seconds=(("teams", 14_400),),
    )
    assert direct.capture_seconds_for("teams") == 3_600


@pytest.mark.parametrize("value", ["teams=10", "nope=3600", "teams", "teams=", "teams=abc"])
def test_malformed_ceilings_fail_closed_at_boot(value):
    # A silently-ignored ceiling would leave the exposure it exists to bound, so every
    # malformed form must refuse to boot rather than degrade to the maximum.
    with pytest.raises(RuntimeError):
        _config(ZAKI_MINUTES_PLATFORM_CAPTURE_SECONDS=value)
