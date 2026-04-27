"""v0.10.5 — URL handling + Pack X dry_run unit tests.

Two architectural shifts:

1. URL handling — `(URL + platform)` trust model. Parser is best-effort
   metadata extraction; failing to recognize a URL shape is NOT a
   validation gate. White-label / enterprise / never-seen-before URLs
   (LFX, AWS, Bloomberg, etc.) work via the (URL + platform) path
   without requiring per-vendor parser entries.

2. Pack X — `dry_run=true` flag on /bots POST skips runtime-api bot
   launch. Test driver controls full lifecycle via callback endpoints.
   No real Playwright bot, no contamination from real-bot callbacks.

Per project-owner principle 2026-04-27: "we will have endless [white-
label URLs]; cannot create a parser for every one. Allow users to
supply (URL + platform) and trust them."
"""
from __future__ import annotations

import pytest

from meeting_api.schemas import parse_meeting_url, MeetingCreate


# ===================================================================
# parse_meeting_url — canonical shapes (regression locks)
# ===================================================================


class TestParserCanonicalUrls:
    """Canonical zoom.us / meet.google.com / teams.microsoft.com shapes
    must continue to extract metadata as before. These are tight
    contracts — the parser SHOULD recognize them."""

    def test_zoom_us_j_path(self):
        result = parse_meeting_url("https://zoom.us/j/96088138284?pwd=abc123")
        assert result["platform"] == "zoom"
        assert result["native_meeting_id"] == "96088138284"
        assert result["passcode"] == "abc123"

    def test_zoomgov_path(self):
        result = parse_meeting_url("https://zoomgov.com/j/89234567890")
        assert result["platform"] == "zoom"
        assert result["native_meeting_id"] == "89234567890"


# ===================================================================
# parse_meeting_url — fails silently on unrecognized shapes
# ===================================================================


class TestParserBestEffort:
    """When the parser doesn't recognize a URL shape, it raises
    ValueError. The model_validator catches it silently and lets
    downstream validation handle the (URL + platform) trust path.
    This test asserts the parser raises (caller is responsible for
    treating it as best-effort)."""

    def test_lfx_url_unrecognized_raises(self):
        with pytest.raises(ValueError):
            parse_meeting_url(
                "https://zoom-lfx.platform.linuxfoundation.org/meeting/96088138284"
                "?password=c9e528a8-3852-4b82-89c2-96d6f22526ad"
            )

    def test_arbitrary_url_unrecognized_raises(self):
        with pytest.raises(ValueError):
            parse_meeting_url("https://example.com/meeting/12345")


# ===================================================================
# MeetingCreate — Path 3: (platform + meeting_url) trust model
# ===================================================================


class TestUrlPlusPlatformTrustModel:
    """v0.10.5 Path 3 (the architectural shift): user supplies
    `(meeting_url + platform)`. Parser may or may not recognize the
    URL — schema accepts the request either way. No per-vendor parser
    entries proliferating.

    Test cases cover canonical (parser succeeds), white-label
    (parser fails, trust path kicks in), and edge cases."""

    def test_canonical_zoom_url_plus_platform(self):
        """Canonical Zoom URL — parser extracts metadata, request validates."""
        m = MeetingCreate(
            meeting_url="https://zoom.us/j/96088138284?pwd=abc",
            platform="zoom",
        )
        assert m.platform.value == "zoom"
        assert m.native_meeting_id == "96088138284"

    def test_lfx_url_plus_platform_zoom(self):
        """LFX URL (white-label) + platform=zoom — parser fails,
        trust-path accepts. native_meeting_id stays None at validation
        time; handler synthesizes one from URL hash."""
        m = MeetingCreate(
            meeting_url="https://zoom-lfx.platform.linuxfoundation.org/meeting/96088138284?password=secret",
            platform="zoom",
        )
        assert m.platform.value == "zoom"
        assert m.meeting_url is not None

    def test_arbitrary_url_plus_platform(self):
        """Arbitrary URL with platform supplied — accepted via trust
        model. The bot will navigate the URL directly; if it's a real
        Zoom-managed URL, server-side redirect resolves it."""
        m = MeetingCreate(
            meeting_url="https://my-corp.example.com/meet/abc-defg-hij",
            platform="google_meet",
        )
        assert m.platform.value == "google_meet"

    def test_url_only_no_platform_no_id_rejected(self):
        """Pure URL-only (no platform, parser fails) — request is
        rejected with a clear actionable error. The user can either
        (a) tell us the platform, or (b) supply native_meeting_id, or
        (c) use a recognized URL shape."""
        with pytest.raises(ValueError, match="Either provide"):
            MeetingCreate(meeting_url="https://my-corp.example.com/meet/abc")

    def test_native_id_only_still_works(self):
        """Path 1 (existing) still works."""
        m = MeetingCreate(platform="google_meet", native_meeting_id="abc-defg-hij")
        assert m.native_meeting_id == "abc-defg-hij"


# ===================================================================
# Pack X dry_run flag — schema field
# ===================================================================


class TestDryRunSchemaField:
    """Pack X dry_run is a first-class schema field. Production gate
    (VEXA_ENV != 'production') enforced at request_bot handler;
    schema-level test verifies the field exists and round-trips."""

    def test_dry_run_default_false(self):
        m = MeetingCreate(platform="google_meet", native_meeting_id="abc-defg-hij")
        assert m.dry_run is False

    def test_dry_run_explicit_true(self):
        m = MeetingCreate(
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            dry_run=True,
        )
        assert m.dry_run is True

    def test_dry_run_with_url_plus_platform(self):
        """dry_run composes with the (URL + platform) trust model."""
        m = MeetingCreate(
            meeting_url="https://zoom-lfx.platform.linuxfoundation.org/meeting/96088138284?password=x",
            platform="zoom",
            dry_run=True,
        )
        assert m.dry_run is True
        assert m.platform.value == "zoom"
