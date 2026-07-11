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

    def test_self_hosted_jitsi_host_inferred(self):
        # A host naming jitsi is a jitsi deployment; the caller keeps the raw URL as
        # meeting_url so the bot joins on that host, not the meet.jit.si template.
        # The native id embeds the host (room@host) — a jitsi room is deployment-scoped,
        # so same-named rooms on different deployments never share an identity key.
        assert parse_meeting_url("https://jitsi.example.org/MyRoom") == ("jitsi", "MyRoom@jitsi.example.org")

    def test_self_hosted_meet_convention_inferred_on_paste(self):
        assert parse_meeting_url("https://meet.example.org/TeamSync") == ("jitsi", "TeamSync@meet.example.org")
        # Regionalized deployments put "meet" mid-hostname.
        assert parse_meeting_url("https://eu.meet.example.org/QualifiedRoomName") == (
            "jitsi",
            "QualifiedRoomName@eu.meet.example.org",
        )
        # "meet" must be a whole label — meetings.example.org is NOT a jitsi convention.
        assert parse_meeting_url("https://meetings.example.org/Room") is None
        # …and NOT in the free-text (ICS) scan, where the meet-label rule is too loose.
        assert parse_meeting_url("https://eu.meet.example.org/TeamSync", generic_hosts=False) is None
        # meet.google.com is claimed by the Meet rule first — never captured by the fallback.
        assert parse_meeting_url("https://meet.google.com/abc-defg-hij") == ("google_meet", "abc-defg-hij")


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

    def test_meet_label_host_not_imported_from_free_text(self):
        # The meet-label convention is pasted-link-only — an ICS full of arbitrary
        # links must not guess rooms. Declaring the host (below) is the opt-in.
        assert find_meeting_link("agenda: https://eu.meet.example.org/Weekly") is None


class TestConfiguredJitsiHosts:
    def test_declared_host_parses_and_imports(self, monkeypatch):
        monkeypatch.setenv("VEXA_JITSI_HOSTS", "eu.meet.example.org, calls.example.io")
        # Pasted link on a declared host — parses in strict mode too. Declared or not, a
        # non-canonical deployment's native id stays deployment-scoped (room@host).
        assert parse_meeting_url("https://eu.meet.example.org/Weekly", generic_hosts=False) == (
            "jitsi",
            "Weekly@eu.meet.example.org",
        )
        # A declared host with NO jitsi/meet naming at all.
        assert parse_meeting_url("https://calls.example.io/Standup", generic_hosts=False) == (
            "jitsi",
            "Standup@calls.example.io",
        )
        # Calendar (ICS) free-text scan now imports it — the point of the setting.
        got = find_meeting_link("agenda: https://eu.meet.example.org/Weekly today")
        assert got == ("jitsi", "Weekly@eu.meet.example.org", "https://eu.meet.example.org/Weekly")

    def test_unset_env_declares_nothing(self, monkeypatch):
        monkeypatch.delenv("VEXA_JITSI_HOSTS", raising=False)
        assert parse_meeting_url("https://calls.example.io/Standup") is None


class TestConstructMeetingUrl:
    def test_jitsi_requires_explicit_url(self):
        # A jitsi room name is deployment-scoped — constructing a URL from the bare id
        # would join the PUBLIC meet.jit.si room of that name (the wrong meeting), so
        # jitsi has no template: callers pass meeting_url, like zoom.
        assert construct_meeting_url("jitsi", "VexaStandup") is None

    def test_zoom_still_requires_explicit_url(self):
        assert construct_meeting_url("zoom", "84335626851") is None
