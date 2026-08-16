"""L2 — the vendored link parser agrees with meeting-api's copy on every shape we send it.

The mailroom extracts ``meeting_url`` from an invite and hands it to ``POST /meetings``, which
parses it AGAIN with ``meeting_api.collector.meeting_link``. A divergence would show up as the
mailroom cheerfully accepting invitations the control plane then 422s — a silent, per-message
failure. These goldens are the shapes both copies must agree on; when the meeting-api parser
changes, this file is what tells you the vendored copy needs the same change.
"""
from __future__ import annotations

import pytest

from vexa_mailroom import find_meeting_link, parse_meeting_url


@pytest.mark.parametrize("url,expected", [
    ("https://meet.google.com/abc-defg-hij", ("google_meet", "abc-defg-hij")),
    ("abc-defg-hij", ("google_meet", "abc-defg-hij")),
    ("https://us02web.zoom.us/j/85512345678?pwd=abc", ("zoom", "85512345678")),
    ("https://teams.live.com/meet/9361792952021?p=IXw5JhZRdoBvKnUXPy", ("teams", "9361792952021")),
    ("https://teams.microsoft.com/l/meetup-join/19%3ameeting_ZjQxYjkwMjEt%40thread.v2/0",
     ("teams", "19:meeting_ZjQxYjkwMjEt@thread.v2")),
    ("https://example.com/not-a-meeting", None),
    ("", None),
])
def test_parse_meeting_url(url, expected):
    assert parse_meeting_url(url) == expected


def test_free_text_scan_finds_the_first_meeting_link():
    text = ("Agenda: https://example.com/agenda\n"
            "Join: https://meet.google.com/xyz-mnop-qrs\n")
    assert find_meeting_link(text) == ("google_meet", "xyz-mnop-qrs",
                                       "https://meet.google.com/xyz-mnop-qrs")


def test_free_text_scan_does_not_invent_a_room_from_an_arbitrary_meet_host():
    """The pasted-link jitsi heuristics are too loose for a calendar description — a vendor's
    ``meet.*`` link in an invite body must not import as a joinable room."""
    assert find_meeting_link("See https://meet.somevendor.example/marketing-page") is None


@pytest.mark.parametrize("url", [
    "https://meet.google.com.attacker.example/abc-defg-hij",
    "https://teams.microsoft.com.attacker.example/l/meetup-join/19%3ameeting_x%40thread.v2/0",
    "https://teams.live.com.attacker.example/meet/9361792952021",
    "https://zoom.us.attacker.example/j/85512345678",
    "https://notzoom.example/j/85512345678",
])
def test_a_lookalike_host_resolves_to_no_platform(url):
    """Host matching is exact-or-subdomain, never a substring.

    The mailbox is public: anyone can send an invitation carrying any URL. A lookalike host must
    not resolve to a platform, or a stranger's link ends up as a meeting we plan to join. This is
    the one place the vendored parser is deliberately stricter than meeting-api's copy — strictness
    only narrows what we accept, so nothing accepted here is rejected downstream.
    """
    assert parse_meeting_url(url) is None
    assert find_meeting_link(f"Join at {url} please") is None


@pytest.mark.parametrize("url,expected", [
    ("https://us02web.zoom.us/j/85512345678", ("zoom", "85512345678")),
    ("https://company.zoom.us/j/85512345678", ("zoom", "85512345678")),
    ("https://zoomgov.com/j/85512345678", ("zoom", "85512345678")),
])
def test_real_zoom_subdomains_still_resolve(url, expected):
    assert parse_meeting_url(url) == expected


def test_trailing_punctuation_is_not_part_of_the_link():
    assert find_meeting_link("Join (https://meet.google.com/abc-defg-hij).") == (
        "google_meet", "abc-defg-hij", "https://meet.google.com/abc-defg-hij")
