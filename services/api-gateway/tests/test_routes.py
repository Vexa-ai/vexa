"""Unit tests for api-gateway route definitions.

Inspects the FastAPI app object to verify routes exist and map correctly.
Does NOT send HTTP requests -- just checks the app's route table.
"""
import pytest
from main import app


def _get_routes():
    """Return a dict of {(method, path): route} for all API routes."""
    routes = {}
    for route in app.routes:
        if hasattr(route, "methods") and hasattr(route, "path"):
            for method in route.methods:
                routes[(method.upper(), route.path)] = route
    return routes


ROUTES = _get_routes()


class TestBotManagementRoutes:
    def test_post_bots_exists(self):
        assert ("POST", "/bots") in ROUTES

    def test_delete_bots_exists(self):
        assert ("DELETE", "/bots/{platform}/{native_meeting_id}") in ROUTES

    def test_put_bot_config_exists(self):
        assert ("PUT", "/bots/{platform}/{native_meeting_id}/config") in ROUTES

    def test_get_bot_status_exists(self):
        assert ("GET", "/bots/status") in ROUTES


class TestTranscriptionRoutes:
    def test_get_meetings_exists(self):
        assert ("GET", "/meetings") in ROUTES

    def test_get_transcript_exists(self):
        assert ("GET", "/transcripts/{platform}/{native_meeting_id}") in ROUTES

    def test_post_transcript_share_exists(self):
        assert ("POST", "/transcripts/{platform}/{native_meeting_id}/share") in ROUTES

    def test_get_public_transcript_exists(self):
        assert ("GET", "/public/transcripts/{share_id}.txt") in ROUTES


class TestRecordingRoutes:
    def test_list_recordings_exists(self):
        assert ("GET", "/recordings") in ROUTES

    def test_get_recording_exists(self):
        assert ("GET", "/recordings/{recording_id}") in ROUTES

    def test_delete_recording_exists(self):
        assert ("DELETE", "/recordings/{recording_id}") in ROUTES

    def test_download_media_exists(self):
        assert ("GET", "/recordings/{recording_id}/media/{media_file_id}/download") in ROUTES

    def test_recording_config_get_exists(self):
        assert ("GET", "/recording-config") in ROUTES

    def test_recording_config_put_exists(self):
        assert ("PUT", "/recording-config") in ROUTES


class TestVoiceAgentRoutes:
    def test_speak_post_exists(self):
        assert ("POST", "/bots/{platform}/{native_meeting_id}/speak") in ROUTES

    def test_speak_delete_exists(self):
        assert ("DELETE", "/bots/{platform}/{native_meeting_id}/speak") in ROUTES

    def test_chat_post_exists(self):
        assert ("POST", "/bots/{platform}/{native_meeting_id}/chat") in ROUTES

    def test_chat_get_exists(self):
        assert ("GET", "/bots/{platform}/{native_meeting_id}/chat") in ROUTES

    def test_screen_post_exists(self):
        assert ("POST", "/bots/{platform}/{native_meeting_id}/screen") in ROUTES

    def test_screen_delete_exists(self):
        assert ("DELETE", "/bots/{platform}/{native_meeting_id}/screen") in ROUTES


class TestMeetingRoutes:
    def test_patch_meeting_exists(self):
        assert ("PATCH", "/meetings/{platform}/{native_meeting_id}") in ROUTES

    def test_delete_meeting_exists(self):
        assert ("DELETE", "/meetings/{platform}/{native_meeting_id}") in ROUTES


class TestWebSocketRoute:
    def test_ws_route_exists(self):
        ws_paths = [r.path for r in app.routes if hasattr(r, "path") and "websocket" in type(r).__name__.lower()]
        assert "/ws" in ws_paths


class TestRootRoute:
    def test_root_exists(self):
        assert ("GET", "/") in ROUTES
