"""The Zoom link a forwarded invite carries — attendee `/j/` and host start `/s/` (#1845).

A calendar shows the meeting's OWNER the start link, `https://<tenant>.zoom.us/s/<id>?zak=<jwt>`,
not the attendee link. Both name the same meeting. The `zak` is the host's start token and is
never part of the extracted link: the bot joins as a guest.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows_integrations.mailbox import _meeting_url  # noqa: E402


def test_attendee_join_link():
    text = "Join: https://us02web.zoom.us/j/84123456789?pwd=aBcD1234efGH\n"
    assert _meeting_url(text) == "https://us02web.zoom.us/j/84123456789?pwd=aBcD1234efGH"


def test_host_start_link_is_recognised_without_its_zak():
    text = "Start: https://us06web.zoom.us/s/85176323856?zak=eyJ0eXAiOiJKV1QifQ.e30.sig\n"
    assert _meeting_url(text) == "https://us06web.zoom.us/s/85176323856"


def test_host_start_link_keeps_a_leading_pwd():
    text = "https://us06web.zoom.us/s/85176323856?pwd=aBcD1234efGH&zak=eyJ0eXAiOiJKV1QifQ.e30.sig"
    assert _meeting_url(text) == "https://us06web.zoom.us/s/85176323856?pwd=aBcD1234efGH"
