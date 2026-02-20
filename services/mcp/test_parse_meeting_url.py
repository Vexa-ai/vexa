"""
Unit tests for _parse_meeting_url and Platform.construct_meeting_url.

Run with: pytest services/mcp/test_parse_meeting_url.py -v
  (from the repo root, with shared-models installed or on PYTHONPATH)
"""
import hashlib
import sys
import os
import pytest

# Allow running from repo root without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "libs", "shared-models"))

from fastapi import HTTPException
from main import _parse_meeting_url
from shared_models.schemas import Platform


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse(url: str):
    """Shorthand: call _parse_meeting_url and return the result dict."""
    return _parse_meeting_url(url)


def assert_422(url: str, fragment: str = ""):
    """Assert that parsing raises a 422 HTTPException (optionally checking detail text)."""
    with pytest.raises(HTTPException) as exc_info:
        _parse_meeting_url(url)
    assert exc_info.value.status_code == 422
    if fragment:
        assert fragment.lower() in str(exc_info.value.detail).lower(), (
            f"Expected '{fragment}' in detail: {exc_info.value.detail}"
        )


# ---------------------------------------------------------------------------
# Google Meet
# ---------------------------------------------------------------------------

class TestGoogleMeet:
    def test_standard_code(self):
        r = parse("https://meet.google.com/abc-defg-hij")
        assert r.platform == "google_meet"
        assert r.native_meeting_id == "abc-defg-hij"
        assert r.passcode is None
        assert r.warnings == []

    def test_standard_code_with_authuser_param(self):
        r = parse("https://meet.google.com/abc-defg-hij?authuser=0&hs=pCv")
        assert r.native_meeting_id == "abc-defg-hij"

    def test_custom_workspace_nickname(self):
        r = parse("https://meet.google.com/our-team-standup")
        assert r.platform == "google_meet"
        assert r.native_meeting_id == "our-team-standup"
        assert any("workspace" in w.lower() for w in r.warnings)

    def test_custom_nickname_short_minimum(self):
        # 5 chars is the minimum (1 + 3 middle + 1)
        r = parse("https://meet.google.com/ab-cd")
        assert r.native_meeting_id == "ab-cd"

    def test_lookup_url_rejected(self):
        assert_422("https://meet.google.com/lookup/c2dhdn5hqs", "lookup")

    def test_invalid_code_rejected(self):
        assert_422("https://meet.google.com/INVALID_CODE!")

    def test_empty_path_rejected(self):
        assert_422("https://meet.google.com/")


# ---------------------------------------------------------------------------
# Teams personal (teams.live.com) — no regression
# ---------------------------------------------------------------------------

class TestTeamsPersonal:
    def test_standard_with_passcode(self):
        r = parse("https://teams.live.com/meet/9361792952021?p=abc12345")
        assert r.platform == "teams"
        assert r.native_meeting_id == "9361792952021"
        assert r.passcode == "abc12345"
        assert r.teams_base_host is None
        assert r.meeting_url is None

    def test_no_passcode_warns(self):
        r = parse("https://teams.live.com/meet/9361792952021")
        assert r.native_meeting_id == "9361792952021"
        assert r.passcode is None
        assert any("passcode" in w.lower() for w in r.warnings)

    def test_invalid_path_rejected(self):
        assert_422("https://teams.live.com/join/9361792952021")


# ---------------------------------------------------------------------------
# Teams enterprise short URL (Track A)
# ---------------------------------------------------------------------------

class TestTeamsEnterpriseShort:
    def test_teams_microsoft_com(self):
        r = parse("https://teams.microsoft.com/meet/33749853217630?p=em7xplMpIFquiFGvn8")
        assert r.platform == "teams"
        assert r.native_meeting_id == "33749853217630"
        assert r.passcode == "em7xplMpIFquiFGvn8"
        assert r.teams_base_host == "teams.microsoft.com"
        assert r.meeting_url is None

    def test_no_passcode_warns(self):
        r = parse("https://teams.microsoft.com/meet/33749853217630")
        assert r.teams_base_host == "teams.microsoft.com"
        assert any("passcode" in w.lower() for w in r.warnings)

    def test_gcc_gov(self):
        r = parse("https://gov.teams.microsoft.us/meet/12345678901234")
        assert r.platform == "teams"
        assert r.native_meeting_id == "12345678901234"
        assert r.teams_base_host == "gov.teams.microsoft.us"

    def test_dod(self):
        r = parse("https://dod.teams.microsoft.us/meet/12345678901234")
        assert r.teams_base_host == "dod.teams.microsoft.us"

    def test_v2_deep_link(self):
        r = parse("https://teams.microsoft.com/v2/?meetingjoin=true#/meet/33749853217630?p=em7xplMpIFquiFGvn8&anon=true&deeplinkId=c34d42b3")
        assert r.platform == "teams"
        assert r.native_meeting_id == "33749853217630"
        assert r.passcode == "em7xplMpIFquiFGvn8"
        assert r.teams_base_host == "teams.microsoft.com"

    def test_v2_deep_link_no_passcode_warns(self):
        r = parse("https://teams.microsoft.com/v2/?meetingjoin=true#/meet/33749853217630")
        assert r.native_meeting_id == "33749853217630"
        assert any("passcode" in w.lower() for w in r.warnings)


# ---------------------------------------------------------------------------
# Teams enterprise legacy long URL (Track B)
# ---------------------------------------------------------------------------

class TestTeamsEnterpriseLong:
    LONG_URL = (
        "https://teams.microsoft.com/l/meetup-join/"
        "19%3Ameeting_MjM2NzczMmEtMmRiNi00MGNhLWI1ZTYtMjI0ODQxMjI4NGNk%40thread.skype"
        "/0?context=%7B%22Tid%22%3A%22d0880d3f-e6d1-4a41-9e81-b8fbcddf7b6c%22%7D"
    )

    def test_long_url_parsed(self):
        r = parse(self.LONG_URL)
        assert r.platform == "teams"
        # native_meeting_id is a 16-char hex hash
        assert len(r.native_meeting_id) == 16
        assert all(c in "0123456789abcdef" for c in r.native_meeting_id)
        # raw URL is preserved
        assert r.meeting_url == self.LONG_URL
        assert r.passcode is None
        assert any("legacy" in w.lower() for w in r.warnings)

    def test_hash_is_deterministic(self):
        r1 = parse(self.LONG_URL)
        r2 = parse(self.LONG_URL)
        assert r1.native_meeting_id == r2.native_meeting_id

    def test_hash_matches_sha256(self):
        r = parse(self.LONG_URL)
        expected = hashlib.sha256(self.LONG_URL.encode()).hexdigest()[:16]
        assert r.native_meeting_id == expected

    def test_unsupported_enterprise_path_rejected(self):
        assert_422("https://teams.microsoft.com/l/channel/something")


# ---------------------------------------------------------------------------
# Zoom
# ---------------------------------------------------------------------------

class TestZoom:
    def test_standard_meeting(self):
        r = parse("https://zoom.us/j/12345678901?pwd=Abc123")
        assert r.platform == "zoom"
        assert r.native_meeting_id == "12345678901"
        assert r.passcode == "Abc123"

    def test_regional_subdomain(self):
        r = parse("https://us02web.zoom.us/j/12345678901")
        assert r.native_meeting_id == "12345678901"

    def test_vanity_subdomain(self):
        r = parse("https://company.zoom.us/j/12345678901?pwd=xyz")
        assert r.native_meeting_id == "12345678901"

    def test_webinar_w_path(self):
        r = parse("https://zoom.us/w/98765432101?pwd=abc")
        assert r.native_meeting_id == "98765432101"

    def test_web_client_wc_join(self):
        r = parse("https://zoom.us/wc/join/12345678901")
        assert r.native_meeting_id == "12345678901"

    def test_9_digit_legacy_id(self):
        r = parse("https://zoom.us/j/123456789")
        assert r.native_meeting_id == "123456789"

    def test_zoomgov(self):
        r = parse("https://frbmeetings.zoomgov.com/j/12345678901?pwd=xyz")
        assert r.platform == "zoom"
        assert r.native_meeting_id == "12345678901"

    def test_my_personal_link_rejected(self):
        assert_422("https://zoom.us/my/john.smith", "personal meeting room")

    def test_12_digit_id_rejected(self):
        assert_422("https://zoom.us/j/123456789012")  # 12 digits > max 11

    def test_zoom_events_rejected(self):
        assert_422("https://events.zoom.us/ev/abc123", "zoom events")


# ---------------------------------------------------------------------------
# Platform.construct_meeting_url
# ---------------------------------------------------------------------------

class TestConstructMeetingUrl:
    # Google Meet
    def test_google_meet_standard(self):
        assert Platform.construct_meeting_url("google_meet", "abc-defg-hij") == "https://meet.google.com/abc-defg-hij"

    def test_google_meet_custom_nickname(self):
        assert Platform.construct_meeting_url("google_meet", "our-standup") == "https://meet.google.com/our-standup"

    def test_google_meet_invalid(self):
        assert Platform.construct_meeting_url("google_meet", "INVALID!") is None

    # Teams personal (default, no regression)
    def test_teams_live_default(self):
        assert Platform.construct_meeting_url("teams", "9361792952021", "abc12345") == \
            "https://teams.live.com/meet/9361792952021?p=abc12345"

    def test_teams_live_no_passcode(self):
        assert Platform.construct_meeting_url("teams", "9361792952021") == \
            "https://teams.live.com/meet/9361792952021"

    # Teams enterprise short (Track A)
    def test_teams_enterprise_short(self):
        assert Platform.construct_meeting_url("teams", "33749853217630", "xyz", base_host="teams.microsoft.com") == \
            "https://teams.microsoft.com/meet/33749853217630?p=xyz"

    def test_teams_gcc(self):
        assert Platform.construct_meeting_url("teams", "12345678901234", base_host="gov.teams.microsoft.us") == \
            "https://gov.teams.microsoft.us/meet/12345678901234"

    # Teams legacy long URL (Track B) — hex hash → returns None
    def test_teams_hex_hash_returns_none(self):
        assert Platform.construct_meeting_url("teams", "a3f7c2d891b04e5f") is None

    def test_teams_invalid_id_returns_none(self):
        assert Platform.construct_meeting_url("teams", "abc") is None

    # Zoom
    def test_zoom_standard(self):
        assert Platform.construct_meeting_url("zoom", "12345678901", "pwd123") == \
            "https://zoom.us/j/12345678901?pwd=pwd123"

    def test_zoom_no_passcode(self):
        assert Platform.construct_meeting_url("zoom", "12345678901") == "https://zoom.us/j/12345678901"

    def test_zoom_9_digit(self):
        assert Platform.construct_meeting_url("zoom", "123456789") == "https://zoom.us/j/123456789"

    def test_zoom_invalid_returns_none(self):
        assert Platform.construct_meeting_url("zoom", "123") is None

    # Invalid platform
    def test_unknown_platform_returns_none(self):
        assert Platform.construct_meeting_url("unknown_platform", "abc123") is None


# ---------------------------------------------------------------------------
# MeetingCreate schema passcode validation
# ---------------------------------------------------------------------------

class TestPasscodeValidation:
    def test_teams_4_char_passcode_accepted(self):
        from shared_models.schemas import MeetingCreate
        mc = MeetingCreate(platform="teams", native_meeting_id="9361792952021", passcode="ab12")
        assert mc.passcode == "ab12"

    def test_teams_6_char_passcode_accepted(self):
        from shared_models.schemas import MeetingCreate
        mc = MeetingCreate(platform="teams", native_meeting_id="9361792952021", passcode="IXw5Jh")
        assert mc.passcode == "IXw5Jh"

    def test_teams_20_char_passcode_accepted(self):
        from shared_models.schemas import MeetingCreate
        mc = MeetingCreate(platform="teams", native_meeting_id="9361792952021", passcode="A" * 20)
        assert mc.passcode == "A" * 20

    def test_teams_3_char_passcode_rejected(self):
        from pydantic import ValidationError
        from shared_models.schemas import MeetingCreate
        with pytest.raises(ValidationError):
            MeetingCreate(platform="teams", native_meeting_id="9361792952021", passcode="ab1")

    def test_teams_21_char_passcode_rejected(self):
        from pydantic import ValidationError
        from shared_models.schemas import MeetingCreate
        with pytest.raises(ValidationError):
            MeetingCreate(platform="teams", native_meeting_id="9361792952021", passcode="A" * 21)


# ---------------------------------------------------------------------------
# MeetingCreate native_meeting_id validation for Teams
# ---------------------------------------------------------------------------

class TestTeamsNativeMeetingIdValidation:
    def test_numeric_id_accepted(self):
        from shared_models.schemas import MeetingCreate
        mc = MeetingCreate(platform="teams", native_meeting_id="9361792952021")
        assert mc.native_meeting_id == "9361792952021"

    def test_hex_hash_accepted(self):
        from shared_models.schemas import MeetingCreate
        mc = MeetingCreate(platform="teams", native_meeting_id="a3f7c2d891b04e5f")
        assert mc.native_meeting_id == "a3f7c2d891b04e5f"

    def test_full_url_rejected(self):
        from pydantic import ValidationError
        from shared_models.schemas import MeetingCreate
        with pytest.raises(ValidationError):
            MeetingCreate(platform="teams", native_meeting_id="https://teams.microsoft.com/meet/123")

    def test_short_id_rejected(self):
        from pydantic import ValidationError
        from shared_models.schemas import MeetingCreate
        with pytest.raises(ValidationError):
            MeetingCreate(platform="teams", native_meeting_id="12345")
