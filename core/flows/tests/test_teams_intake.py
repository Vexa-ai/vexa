"""Invite intake is PLATFORM-AWARE — the bank case.

An Outlook mailbox in a bank emits Microsoft Teams invites, and the intake used to carry a single
``https://meet\\.google\\.com/[a-z-]+`` regex: the first real OeNB invite would have parsed as "no
meeting here" and been logged as ignored. These tests pin the three things that fixes it:

  1. the id comes out of the ICS wherever Outlook actually put it — the explicit
     ``X-MICROSOFT-SKYPETEAMSMEETINGURL`` property first, then ``LOCATION``, then the folded and
     escaped ``DESCRIPTION``;
  2. ``platform`` + ``native_meeting_id`` (+ ``passcode``) travel as FACTS from intake to
     ``dispatch_bot``, so no step re-derives an id from a URL's shape;
  3. a platform we cannot join fails TYPED, with the organizer told which platform we saw —
     never a bot dispatched at a meeting it cannot enter.

Fixture provenance: ``tests/ics/README.md``.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows import Done, StepError  # noqa: E402
from flows_integrations.mailbox import parse_ics  # noqa: E402
from flows_integrations.meeting_link import (  # noqa: E402
    NATIVE_MEETING_ID_URL_CHARS, find_in_ics, parse_meeting_url, unfold)
from flows_steps import meeting as mt  # noqa: E402

ICS = Path(__file__).resolve().parent / "ics"


def _ics(name: str) -> str:
    """Read BYTES and decode — ``read_text`` would translate CRLF to LF and quietly stop
    exercising RFC-5545 folding as it actually arrives on the wire."""
    return (ICS / name).read_bytes().decode("utf-8")


DEEP = _ics("outlook-teams-deep-link.ics")
DESC_ONLY = _ics("outlook-teams-description-only.ics")
SHORT = _ics("outlook-teams-short-link.ics")
MEET = _ics("gcal-meet.ics")
ZOOM = _ics("outlook-zoom.ics")
NO_LINK = _ics("outlook-no-meeting-link.ics")

DEEP_THREAD = "19:meeting_NTQ2ZmRlM2ItYjhkMi00Y2FiLWE0NTUtM2Y2ZjJhYzQ4ZTk1@thread.v2"
DESC_THREAD = "19:meeting_MTJhOWM0ZTgtNWQzYi00ODcxLTkyZjAtNmIxZTdkNGM4YTIz@thread.v2"


# ── the ICS itself ───────────────────────────────────────────────────────────────────────

def test_outlook_teams_invite_is_the_bank_case():
    """The invite that used to fall off the end of the parser."""
    ev = parse_ics(DEEP)
    assert ev is not None, "an Outlook/Teams invite parsed as 'no meeting here' — the whole bug"
    assert ev["platform"] == "teams"
    assert ev["native_meeting_id"] == DEEP_THREAD
    assert ev["platform_supported"] is True
    assert ev["url"].startswith("https://teams.microsoft.com/l/meetup-join/")
    assert ev["organizer"] == "priya.raman@example.com"
    # SUMMARY;LANGUAGE=en-US — a name-anchored `^SUMMARY:` misses every localized Outlook
    # property and titled real bank invites "Meeting".
    assert ev["title"] == "Northwind / Vexa — platform review"
    assert ev["start"] > time.time(), "Windows TZID 'W. Europe Standard Time' did not resolve"


def test_x_property_outranks_the_body():
    """Outlook's own statement of the join URL wins over the anchor in the description — on a
    forwarded or edited invite the two disagree, and only the X- property is authoritative."""
    unfolded = unfold(DEEP)
    doctored = "\n".join(
        ln.replace("19%3ameeting_NTQ2ZmRlM2ItYjhkMi00Y2FiLWE0NTUtM2Y2ZjJhYzQ4ZTk1%40thread.v2",
                   "19%3ameeting_STALESTALESTALESTALE%40thread.v2")
        if ln.startswith("DESCRIPTION") else ln
        for ln in unfolded.split("\n"))
    assert "19%3ameeting_STALESTALESTALESTALE%40thread.v2" in doctored, \
        "fixture drifted — the DESCRIPTION anchor is not the string this test doctors"
    ev = parse_ics(doctored)
    assert ev["native_meeting_id"] == DEEP_THREAD, "the description outranked the X- property"


def test_teams_id_survives_folding_and_escaping_in_the_description():
    """No X- property, LOCATION is the literal 'Microsoft Teams Meeting' — the link exists only
    inside a body folded at 75 octets with ICS ``\\n`` / ``\\,`` escapes."""
    ev = parse_ics(DESC_ONLY)
    assert ev["platform"] == "teams"
    assert ev["native_meeting_id"] == DESC_THREAD
    assert ev["url"].startswith("https://teams.microsoft.com/l/meetup-join/")


def test_scheduling_service_url_is_never_the_join_link():
    """``X-MICROSOFT-SCHEDULINGSERVICEUPDATEURL`` carries the thread id UNENCODED on a host that
    passes a substring gate on 'teams.microsoft.com'. The gateway prefers a supplied meeting_url
    over its own template, so mistaking it points the bot at a scheduler API."""
    # Delete every property that holds a real join URL, leaving the management endpoint as the
    # only teams.microsoft.com hit in the VEVENT — which is what the whole-VEVENT fallback scan
    # would then reach.
    drop = ("X-MICROSOFT-SKYPETEAMSMEETINGURL", "DESCRIPTION", "LOCATION")
    stripped = "\n".join(ln for ln in unfold(DEEP).split("\n")
                         if not ln.startswith(drop))
    assert "api.scheduler.teams.microsoft.com" in stripped
    assert "meetup-join" not in stripped, "the fixture still holds a real join URL"
    assert parse_ics(stripped) is None, "a scheduler management endpoint parsed as a join link"


def test_teams_short_link_carries_its_passcode_separately():
    ev = parse_ics(SHORT)
    assert ev["platform"] == "teams"
    assert ev["native_meeting_id"] == "3847269150422"
    assert ev["passcode"] == "7Kq2XdF1nRt9"
    assert ev["platform_supported"] is True


def test_google_meet_still_parses():
    """Regression: nothing that parsed before this change stops parsing."""
    ev = parse_ics(MEET)
    assert ev["platform"] == "google_meet"
    assert ev["native_meeting_id"] == "abc-defg-hij"
    assert ev["url"] == "https://meet.google.com/abc-defg-hij"
    assert ev["platform_supported"] is True


def test_zoom_is_recognized_and_typed_unsupported():
    """Not None — silence is the failure mode this replaces. The organizer is owed a platform
    name, and the flow is owed the fact that it must refuse."""
    ev = parse_ics(ZOOM)
    assert ev is not None
    assert ev["platform"] == "zoom"
    assert ev["native_meeting_id"] == "98412337055"
    assert ev["platform_supported"] is False


@pytest.mark.parametrize("ics,label", [(DEEP, "teams-deep"), (DESC_ONLY, "teams-desc"),
                                      (SHORT, "teams-short"), (MEET, "meet"), (ZOOM, "zoom")])
def test_native_id_is_never_url_shaped(ics, label):
    """#892 — a passcode or query left on the id builds an unjoinable URL and an unfindable row."""
    ev = parse_ics(ics)
    nid = ev["native_meeting_id"]
    assert nid and not any(c in nid for c in NATIVE_MEETING_ID_URL_CHARS)
    assert not any(c.isspace() for c in nid)
    assert len(nid) <= 255


def test_a_meetingless_invite_is_still_none():
    """A real in-person Outlook invite — `LOCATION: Northwind HQ\\, room 4B`, no conferencing URL
    anywhere — must not become a dispatch. (Kept from the pre-change contract.)"""
    assert parse_ics(NO_LINK) is None


def test_a_windows_timezone_resolves_and_an_unknown_one_is_refused():
    """Exchange writes WINDOWS zone names into TZID. `ZoneInfo('W. Europe Standard Time')` raises,
    and that exception escaped parse_ics — one Outlook invite took the poller down mid-batch, so
    no mail after it was processed either. An unmappable name is refused, never guessed."""
    ev = parse_ics(DEEP)
    expected = datetime(2030, 8, 19, 14, 0, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
    assert abs(ev["start"] - expected) < 1
    unknown = DEEP.replace("W. Europe Standard Time", "Middle Earth Standard Time")
    assert parse_ics(unknown) is None, "an unresolvable zone was guessed instead of refused"


# ── agreement with the product's own parser ──────────────────────────────────────────────

def _product_parser():
    """The gateway's parser, imported from the repo by PATH for this test only.

    flows is a separate deployable and its source imports no meeting code — the parsing rules are
    mirrored, not shared. That makes drift the risk, so the drift is what the test checks: the id
    this lane extracts must be the id the gateway would have derived, or a bot lands in the wrong
    room. Skipped rather than failed when the path is absent (flows can be vendored alone)."""
    import importlib.util
    p = (Path(__file__).resolve().parents[2] / "meetings" / "services" / "meeting-api" / "src"
         / "meeting_api" / "collector" / "meeting_link.py")
    if not p.exists():
        pytest.skip(f"product parser not present at {p}")
    spec = importlib.util.spec_from_file_location("_product_meeting_link", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("ics,label", [(DEEP, "teams-deep"), (DESC_ONLY, "teams-desc"),
                                      (SHORT, "teams-short"), (MEET, "meet"), (ZOOM, "zoom")])
def test_our_id_is_the_id_the_gateway_would_derive(ics, label):
    product = _product_parser()
    ev = parse_ics(ics)
    derived = product.parse_meeting_url(ev["url"], generic_hosts=False)
    assert derived is not None, f"the gateway would not accept the URL we chose: {ev['url']}"
    assert derived == (ev["platform"], ev["native_meeting_id"]), (
        f"intake and gateway disagree on {ev['url']}: {derived} vs "
        f"{(ev['platform'], ev['native_meeting_id'])}")


# ── the honest refusal, and the carry-through ────────────────────────────────────────────

class _Ctx:
    """The two attributes ``check_platform`` reads. StepCtx is a dataclass over a Reaction row;
    building one here would test the engine, which this change does not touch."""

    def __init__(self, refs, prior=None):
        self.refs = refs
        self.scratch: dict = {}
        self.prior = prior or {}
        self.clock_now = 0.0


def test_unsupported_platform_fails_typed_and_tells_the_organizer(monkeypatch):
    from flows_steps import emailx
    sent: list[tuple] = []
    monkeypatch.setattr(emailx, "send", lambda *a, **k: sent.append(a) or "<id@test>")
    ctx = _Ctx({"platform": "zoom", "native_meeting_id": "98412337055",
                "platform_supported": False, "organizer": "priya.raman@example.com",
                "title": "Vendor sync", "url": "https://example-bank.zoom.us/j/98412337055"})
    with pytest.raises(StepError) as e:
        mt.check_platform(ctx)
    assert e.value.retryable is False, "a platform we cannot join is not a transient failure"
    assert "zoom" in str(e.value)
    assert len(sent) == 1, "the organizer was not told"
    to, subject, body = sent[0]
    assert to == "priya.raman@example.com"
    assert "Zoom" in body and "Teams" in body, "the refusal must name what we saw and what we join"


@pytest.mark.parametrize("platform", ["teams", "google_meet"])
def test_supported_platform_passes_through(platform):
    out = mt.check_platform(_Ctx({"platform": platform, "platform_supported": True}))
    assert isinstance(out, Done) and out.result["platform"] == platform


def test_an_invite_admitted_before_this_change_is_not_refused():
    """In-flight reactions carry no platform fact. They are Meet by construction (the old intake
    matched nothing else), so absence is not 'unknown' — refusing them would email organizers
    about meetings we can and did join."""
    out = mt.check_platform(_Ctx({"url": "https://meet.google.com/abc-defg-hij"}))
    assert isinstance(out, Done) and out.result["platform"] == "google_meet"


def test_dispatch_sends_the_addressing_key_not_a_url_tail(monkeypatch):
    """The id travels as a fact. The gateway treats a supplied native_meeting_id as
    authoritative, which is what makes intake and dispatch agree by construction."""
    seen: dict = {}

    def fake_http(method, url, headers=None, body=None):
        seen.update(method=method, url=url, body=body)
        return 201, {"id": 7, "native_meeting_id": DEEP_THREAD, "platform": "teams"}

    monkeypatch.setattr(mt, "http", fake_http)
    monkeypatch.setattr(mt, "user_api_key", lambda uid: "k")
    ctx = _Ctx({"platform": "teams", "native_meeting_id": DEEP_THREAD,
                "passcode": "7Kq2XdF1nRt9",
                "url": "https://teams.microsoft.com/l/meetup-join/19%3ameeting_x%40thread.v2/0"},
               prior={"ensure_user": {"uid": 1}})
    out = mt.dispatch_bot(ctx)
    assert seen["body"]["platform"] == "teams"
    assert seen["body"]["native_meeting_id"] == DEEP_THREAD
    assert seen["body"]["passcode"] == "7Kq2XdF1nRt9"
    assert seen["body"]["meeting_url"] == ctx.refs["url"]
    assert out.result["platform"] == "teams"


def test_dispatch_falls_back_to_the_url_tail_only_for_legacy_meet(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(mt, "http", lambda m, u, h=None, b=None: (
        seen.update(body=b) or (201, {"id": 9})))
    monkeypatch.setattr(mt, "user_api_key", lambda uid: "k")
    out = mt.dispatch_bot(_Ctx({"url": "https://meet.google.com/abc-defg-hij"},
                               prior={"ensure_user": {"uid": 1}}))
    assert seen["body"]["native_meeting_id"] == "abc-defg-hij"
    assert seen["body"]["platform"] == "google_meet"
    assert out.result["native"] == "abc-defg-hij"


# ── the unit below the ICS ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://teams.microsoft.com/l/meetup-join/19%3ameeting_ABC%40thread.v2/0?context=%7b%7d",
     ("teams", "19:meeting_ABC@thread.v2")),
    ("https://teams.microsoft.com/meet/3847269150422?p=7Kq2Xd", ("teams", "3847269150422")),
    ("https://teams.live.com/meet/9384726150?p=abc", ("teams", "9384726150")),
    ("https://meet.google.com/abc-defg-hij", ("google_meet", "abc-defg-hij")),
    ("https://example-bank.zoom.us/j/98412337055", ("zoom", "98412337055")),
])
def test_parse_meeting_url_shapes(url, expected):
    link = parse_meeting_url(url)
    assert (link.platform, link.native_meeting_id) == expected


@pytest.mark.parametrize("url", [
    "https://api.scheduler.teams.microsoft.com/teams/tid/19:meeting_ABC@thread.v2/0",
    "https://teams.microsoft.com/l/channel/19%3aabc%40thread.tacv2/General",
    "https://meet.google.com/not-a-code",
    "https://docs.example.com/how-to-join",
    "",
])
def test_parse_meeting_url_refuses_non_joinables(url):
    assert parse_meeting_url(url) is None


def test_find_in_ics_prefers_a_supported_platform_over_an_earlier_unsupported_one():
    """A forwarded thread can carry a Zoom link above the real Teams link. A supported platform
    anywhere beats an unsupported one earlier — but a Zoom-only invite still reports zoom."""
    mixed = DEEP.replace("LOCATION;LANGUAGE=en-US:Microsoft Teams Meeting",
                         "LOCATION;LANGUAGE=en-US:https://example-bank.zoom.us/j/98412337055")
    link = find_in_ics(mixed, mixed)
    assert link.platform == "teams" and link.supported
    only_zoom = find_in_ics(ZOOM, ZOOM)
    assert only_zoom.platform == "zoom" and not only_zoom.supported
