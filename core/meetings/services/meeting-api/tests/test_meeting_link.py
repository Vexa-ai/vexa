"""Unit tests for ``collector.meeting_link.parse_meeting_url`` — the pasted-link →
``(platform, native_meeting_id)`` oracle (the server twin of the terminal's
``parseMeetingInput``). Route-level coverage rides test_planned_meetings.py /
test_calendar_sync.py; this file pins the per-platform parse table directly,
with jitsi as the newest row. Pure string logic — no app, no DB.
"""
from __future__ import annotations

from meeting_api.bot_spawn.service import construct_meeting_url
from meeting_api.collector.meeting_link import find_meeting_link, parse_meeting_url


class TestParseJitsi:
    def test_canonical_room(self):
        assert parse_meeting_url("https://meet.jit.si/VexaStandup") == ("jitsi", "VexaStandup")

    def test_room_case_preserved(self):
        assert parse_meeting_url("https://meet.jit.si/MyRoom") == ("jitsi", "MyRoom")

    def test_trailing_slash(self):
        assert parse_meeting_url("https://meet.jit.si/MyRoom/") == ("jitsi", "MyRoom")

    def test_url_encoded_room_stays_encoded(self):
        # The native id is embedded back into URL templates / path params, so the
        # percent-encoded form IS the id — decoding would corrupt the round-trip.
        assert parse_meeting_url("https://meet.jit.si/Team%20Sync") == ("jitsi", "Team%20Sync")

    def test_bare_origin_rejected(self):
        assert parse_meeting_url("https://meet.jit.si/") is None

    def test_multi_segment_path_rejected(self):
        assert parse_meeting_url("https://meet.jit.si/a/b") is None

    def test_self_hosted_host_not_inferred(self):
        # A self-hosted deployment is not host-inferable — callers pass
        # platform=jitsi + meeting_url to POST /bots instead.
        assert parse_meeting_url("https://jitsi.example.org/MyRoom") is None


class TestParseExistingPlatformsUnchanged:
    def test_gmeet(self):
        assert parse_meeting_url("https://meet.google.com/abc-defg-hij") == ("google_meet", "abc-defg-hij")

    def test_zoom(self):
        assert parse_meeting_url("https://us05web.zoom.us/j/84335626851?pwd=x") == ("zoom", "84335626851")

    def test_teams_short(self):
        assert parse_meeting_url("https://teams.live.com/meet/9361792952021?p=abc") == ("teams", "9361792952021")


class TestFindMeetingLinkJitsi:
    def test_found_in_free_text(self):
        got = find_meeting_link("Join us: https://meet.jit.si/VexaStandup today")
        assert got == ("jitsi", "VexaStandup", "https://meet.jit.si/VexaStandup")


class TestConstructMeetingUrl:
    def test_jitsi_constructs_on_canonical_host(self):
        assert construct_meeting_url("jitsi", "VexaStandup") == "https://meet.jit.si/VexaStandup"

    def test_zoom_still_requires_explicit_url(self):
        assert construct_meeting_url("zoom", "84335626851") is None
