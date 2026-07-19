"""Unit tests for ``vexa_mcp.link_parser.parse_meeting_url``.

Ported from 0.10.6 ``services/mcp/tests/test_parse_meeting_url.py`` (the
Platform.construct_meeting_url / MeetingCreate sections belong to meeting-api's
schemas and stayed there).
"""
import hashlib

import pytest
from fastapi import HTTPException

from vexa_mcp.link_parser import parse_meeting_url


def parse(url: str):
    return parse_meeting_url(url)


def assert_422(url: str, fragment: str = ""):
    with pytest.raises(HTTPException) as exc_info:
        parse_meeting_url(url)
    assert exc_info.value.status_code == 422
    if fragment:
        assert fragment.lower() in str(exc_info.value.detail).lower(), (
            f"Expected '{fragment}' in detail: {exc_info.value.detail}"
        )


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
        assert parse(self.LONG_URL).native_meeting_id == parse(self.LONG_URL).native_meeting_id

    def test_hash_matches_sha256(self):
        r = parse(self.LONG_URL)
        assert r.native_meeting_id == hashlib.sha256(self.LONG_URL.encode()).hexdigest()[:16]

    def test_unsupported_enterprise_path_rejected(self):
        assert_422("https://teams.microsoft.com/l/channel/something")


class TestZoom:
    def test_standard_meeting(self):
        r = parse("https://zoom.us/j/12345678901?pwd=Abc123")
        assert r.platform == "zoom"
        assert r.native_meeting_id == "12345678901"
        assert r.passcode == "Abc123"

    def test_regional_subdomain(self):
        assert parse("https://us02web.zoom.us/j/12345678901").native_meeting_id == "12345678901"

    def test_vanity_subdomain(self):
        assert parse("https://company.zoom.us/j/12345678901?pwd=xyz").native_meeting_id == "12345678901"

    def test_webinar_w_path(self):
        assert parse("https://zoom.us/w/98765432101?pwd=abc").native_meeting_id == "98765432101"

    def test_web_client_wc_join(self):
        assert parse("https://zoom.us/wc/join/12345678901").native_meeting_id == "12345678901"

    def test_9_digit_legacy_id(self):
        assert parse("https://zoom.us/j/123456789").native_meeting_id == "123456789"

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


class TestTelemost:
    def test_canonical_link(self):
        r = parse("https://telemost.yandex.ru/j/1111111111")
        assert r.platform == "telemost"
        assert r.native_meeting_id == "1111111111"
        assert r.meeting_url == "https://telemost.yandex.ru/j/1111111111"
        assert r.passcode is None

    def test_trailing_slash(self):
        assert parse("https://telemost.yandex.ru/j/1111111111/").native_meeting_id == "1111111111"

    def test_malformed_link_rejected(self):
        assert_422("https://telemost.yandex.ru/j/not-an-id", "telemost")


class TestJitsi:
    def test_canonical_room(self):
        r = parse("https://meet.jit.si/VexaStandup")
        assert r.platform == "jitsi"
        assert r.native_meeting_id == "VexaStandup"
        assert r.passcode is None
        # The bot always navigates the full URL, so the parser echoes it back.
        assert r.meeting_url == "https://meet.jit.si/VexaStandup"
        assert r.warnings == []

    def test_trailing_slash(self):
        assert parse("https://meet.jit.si/MyRoom/").native_meeting_id == "MyRoom"

    def test_bare_origin_rejected(self):
        assert_422("https://meet.jit.si/", "jitsi")

    def test_multi_segment_path_rejected(self):
        assert_422("https://meet.jit.si/a/b", "jitsi")

    def test_self_hosted_jitsi_host_inferred_with_warning(self):
        r = parse("https://jitsi.example.org/MyRoom")
        assert r.platform == "jitsi"
        # Deployment-scoped id (room@host) — same-named rooms on different hosts never collide.
        assert r.native_meeting_id == "MyRoom@jitsi.example.org"
        assert r.meeting_url == "https://jitsi.example.org/MyRoom"
        # Name-based inference is a guess — the caller is told so.
        assert any("inferred" in w.lower() for w in r.warnings)

    def test_self_hosted_meet_convention_inferred(self):
        r = parse("https://meet.example.org/TeamSync")
        assert r.platform == "jitsi"
        assert r.native_meeting_id == "TeamSync@meet.example.org"
        assert any("inferred" in w.lower() for w in r.warnings)

    def test_regionalized_meet_label_inferred(self):
        r = parse("https://eu.meet.example.org/QualifiedRoomName")
        assert r.platform == "jitsi"
        assert r.native_meeting_id == "QualifiedRoomName@eu.meet.example.org"
        assert r.meeting_url == "https://eu.meet.example.org/QualifiedRoomName"

    def test_declared_host_parses_without_warning(self, monkeypatch):
        # VEXA_JITSI_HOSTS makes a deployment EXPLICIT — same setting meeting-api honours.
        monkeypatch.setenv("VEXA_JITSI_HOSTS", "calls.example.io")
        r = parse("https://calls.example.io/Standup")
        assert r.platform == "jitsi"
        assert r.native_meeting_id == "Standup@calls.example.io"
        assert r.warnings == []

    def test_whitespace_room_rejected(self):
        # The id round-trips into path params — whitespace never parses (matches the
        # meeting-api and terminal twins).
        assert_422("https://meet.jit.si/My Room", "jitsi")

    def test_meet_substring_label_not_inferred(self):
        assert_422("https://meetings.example.org/Room", "unknown provider")


class TestMisc:
    def test_empty_url_rejected(self):
        assert_422("", "empty")

    def test_unknown_provider_rejected(self):
        assert_422("https://example.com/meeting/123", "unknown provider")
