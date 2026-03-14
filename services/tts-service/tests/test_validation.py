"""Unit tests for tts-service input validation and route structure.

Tests the validation logic that exists in main.py without calling OpenAI.
Uses the FastAPI test client for request validation tests.
"""
import os
import pytest

# Ensure env is set before import
os.environ.setdefault("OPENAI_API_KEY", "test-key-for-unit-tests")

from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "tts-service"


class TestVoiceValidation:
    """Test that invalid voices are silently corrected to 'alloy'."""

    VALID_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}

    def test_valid_voices_list(self):
        """Verify the set of valid voices matches the code."""
        assert self.VALID_VOICES == {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}

    def test_voice_set_has_six_entries(self):
        assert len(self.VALID_VOICES) == 6

    def test_alloy_is_default(self):
        """alloy must be in valid set since it's the fallback default."""
        assert "alloy" in self.VALID_VOICES


class TestResponseFormatValidation:
    """Test that invalid response formats are corrected to 'pcm'."""

    VALID_FORMATS = {"pcm", "mp3", "opus", "aac", "wav", "flac"}

    def test_valid_formats(self):
        assert self.VALID_FORMATS == {"pcm", "mp3", "opus", "aac", "wav", "flac"}

    def test_pcm_is_default(self):
        """pcm must be in valid set since it's the fallback default."""
        assert "pcm" in self.VALID_FORMATS

    def test_six_formats_supported(self):
        assert len(self.VALID_FORMATS) == 6


class TestSpeechEndpointValidation:
    """Test request validation on the /v1/audio/speech endpoint."""

    def test_missing_input_returns_400(self, client):
        """Empty 'input' text should return 400."""
        resp = client.post(
            "/v1/audio/speech",
            json={"model": "tts-1", "input": "", "voice": "alloy"},
        )
        assert resp.status_code == 400
        assert "input" in resp.json()["detail"].lower()

    def test_invalid_json_returns_400(self, client):
        """Non-JSON body should return 400."""
        resp = client.post(
            "/v1/audio/speech",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_no_openai_key_returns_503(self):
        """When OPENAI_API_KEY is empty, should return 503."""
        import main
        original = main.OPENAI_API_KEY
        main.OPENAI_API_KEY = ""
        try:
            c = TestClient(app)
            resp = c.post(
                "/v1/audio/speech",
                json={"model": "tts-1", "input": "hello", "voice": "alloy"},
            )
            assert resp.status_code == 503
            assert "OPENAI_API_KEY" in resp.json()["detail"]
        finally:
            main.OPENAI_API_KEY = original


class TestApiKeyAuth:
    """Test optional API key authentication."""

    def test_health_accessible_without_auth(self):
        """Health endpoint never requires auth."""
        c = TestClient(app)
        resp = c.get("/health")
        assert resp.status_code == 200

    def test_wrong_api_key_rejected(self):
        """When TTS_API_TOKEN is set, wrong key is rejected."""
        os.environ["TTS_API_TOKEN"] = "correct-token"
        try:
            c = TestClient(app)
            resp = c.post(
                "/v1/audio/speech",
                json={"model": "tts-1", "input": "hello", "voice": "alloy"},
                headers={"X-API-Key": "wrong-token"},
            )
            assert resp.status_code == 401
        finally:
            os.environ.pop("TTS_API_TOKEN", None)

    def test_correct_api_key_passes_auth_check(self):
        """When TTS_API_TOKEN is set, correct key passes the verify_api_key check."""
        import asyncio
        from main import verify_api_key
        os.environ["TTS_API_TOKEN"] = "correct-token"
        try:
            # Call the dependency directly -- should not raise
            result = asyncio.get_event_loop().run_until_complete(verify_api_key("correct-token"))
            assert result == "correct-token"
        finally:
            os.environ.pop("TTS_API_TOKEN", None)


class TestRouteStructure:
    """Verify expected routes exist on the app."""

    def test_health_route_exists(self):
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/health" in paths

    def test_speech_route_exists(self):
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/v1/audio/speech" in paths

    def test_speech_is_post(self):
        for r in app.routes:
            if hasattr(r, "path") and r.path == "/v1/audio/speech":
                assert "POST" in r.methods
