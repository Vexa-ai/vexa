"""Pack 0.10.6x-pack-zoom-sdk-restore-capability-boundary — capability boundary tests.

Epic #370 requires `POST /bots` with `platform=zoom_sdk` to return 4xx
(not 201) when the dispatched bot image cannot serve the request:
either the SDK image variant is not deployed (BOT_IMAGE_NAME does not
match the :sdk tag pattern) or Marketplace credentials
(ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET) are missing on meeting-api.

This file covers the schema + enum + OBF/ZAK validator wiring. The
HTTP-tier coverage of the capability boundary (the 422 path inside
meetings.py) is exercised by the Compose gate documented in the epic;
adding a fastapi TestClient harness here would duplicate the broader
test_meetings.py setup and is out of scope for the develop pass.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from meeting_api.schemas import MeetingCreate, Platform


class TestZoomPlatformEnum:
    """Pack F: Platform enum gains ZOOM_SDK + ZOOM_WEB; ZOOM is alias."""

    def test_zoom_sdk_is_accepted(self):
        req = MeetingCreate(platform="zoom_sdk", native_meeting_id="123456789")
        assert req.platform == Platform.ZOOM_SDK
        assert req.platform.value == "zoom_sdk"

    def test_zoom_web_is_accepted(self):
        req = MeetingCreate(platform="zoom_web", native_meeting_id="123456789")
        assert req.platform == Platform.ZOOM_WEB
        assert req.platform.value == "zoom_web"

    def test_legacy_zoom_is_accepted(self):
        # Backward-compat: existing callers send `zoom`. Validator must
        # not reject it. Boundary code in meetings.py is responsible for
        # alias resolution at request time.
        req = MeetingCreate(platform="zoom", native_meeting_id="123456789")
        assert req.platform == Platform.ZOOM

    def test_is_zoom_helper(self):
        assert Platform.is_zoom("zoom")
        assert Platform.is_zoom("zoom_sdk")
        assert Platform.is_zoom("zoom_web")
        assert not Platform.is_zoom("google_meet")
        assert not Platform.is_zoom("teams")

    def test_resolve_legacy_zoom(self):
        # Bare `zoom` resolves to the license-clean default.
        assert Platform.resolve_legacy_zoom("zoom") == "zoom_web"
        # Explicit values pass through unchanged.
        assert Platform.resolve_legacy_zoom("zoom_sdk") == "zoom_sdk"
        assert Platform.resolve_legacy_zoom("zoom_web") == "zoom_web"
        assert Platform.resolve_legacy_zoom("google_meet") == "google_meet"

    def test_construct_meeting_url_handles_all_zoom_variants(self):
        # All three variants share the same URL shape.
        for v in ("zoom", "zoom_sdk", "zoom_web"):
            url = Platform.construct_meeting_url(v, "123456789")
            assert url == "https://zoom.us/j/123456789", f"variant={v}"


class TestZoomTokenFields:
    """Pack #370: OBF/ZAK request fields plumbed for zoom_sdk."""

    def test_obf_token_accepted_for_zoom_sdk(self):
        req = MeetingCreate(
            platform="zoom_sdk",
            native_meeting_id="123456789",
            zoom_obf_token="opaque-obf",
        )
        assert req.zoom_obf_token == "opaque-obf"

    def test_obf_token_accepted_for_zoom_web(self):
        req = MeetingCreate(
            platform="zoom_web",
            native_meeting_id="123456789",
            zoom_obf_token="opaque-obf",
        )
        assert req.zoom_obf_token == "opaque-obf"

    def test_obf_token_rejected_for_non_zoom(self):
        with pytest.raises(ValidationError) as exc:
            MeetingCreate(
                platform="google_meet",
                native_meeting_id="abc-defg-hij",
                zoom_obf_token="opaque-obf",
            )
        assert "zoom_obf_token is only supported for Zoom meetings" in str(exc.value)

    def test_zak_token_accepted_for_zoom_sdk(self):
        req = MeetingCreate(
            platform="zoom_sdk",
            native_meeting_id="123456789",
            zoom_zak_token="opaque-zak",
        )
        assert req.zoom_zak_token == "opaque-zak"

    def test_zak_token_rejected_for_zoom_web(self):
        # ZAK is SDK-only — web client doesn't use ZAK tokens.
        with pytest.raises(ValidationError) as exc:
            MeetingCreate(
                platform="zoom_web",
                native_meeting_id="123456789",
                zoom_zak_token="opaque-zak",
            )
        assert "zoom_zak_token is only supported for platform=zoom_sdk" in str(exc.value)

    def test_zak_token_rejected_for_non_zoom(self):
        with pytest.raises(ValidationError) as exc:
            MeetingCreate(
                platform="google_meet",
                native_meeting_id="abc-defg-hij",
                zoom_zak_token="opaque-zak",
            )
        assert "zoom_zak_token is only supported for platform=zoom_sdk" in str(exc.value)
