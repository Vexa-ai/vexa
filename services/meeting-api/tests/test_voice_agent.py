"""Tests for voice agent endpoints — /speak, /chat, /screen, /avatar, /events."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from meeting_api.schemas import MeetingStatus

from .conftest import (
    TEST_MEETING_ID,
    TEST_PLATFORM,
    TEST_NATIVE_MEETING_ID,
    TEST_USER_ID,
    make_meeting,
)


def _patch_find_active(meeting):
    return patch(
        "meeting_api.voice_agent._find_active_meeting",
        new_callable=AsyncMock,
        return_value=meeting,
    )


@pytest.fixture
def active_meeting():
    return make_meeting(status=MeetingStatus.ACTIVE.value)


# ===================================================================
# POST /bots/{platform}/{id}/speak
# ===================================================================


class TestSpeak:

    @pytest.mark.asyncio
    async def test_speak_text(self, client, mock_redis, active_meeting):
        """POST /speak with text → publishes TTS command to Redis."""
        with _patch_find_active(active_meeting):
            resp = await client.post(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/speak",
                json={"text": "Hello, world!"},
            )

        assert resp.status_code == 202
        mock_redis.publish.assert_called()
        channel, payload = mock_redis.publish.call_args[0]
        assert channel == f"bot_commands:meeting:{TEST_MEETING_ID}"
        parsed = json.loads(payload)
        assert parsed["action"] == "speak"
        assert parsed["text"] == "Hello, world!"

    @pytest.mark.asyncio
    async def test_speak_audio_url(self, client, mock_redis, active_meeting):
        """POST /speak with audio_url → publishes speak_audio command."""
        with _patch_find_active(active_meeting):
            resp = await client.post(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/speak",
                json={"audio_url": "https://example.com/audio.wav"},
            )

        assert resp.status_code == 202
        parsed = json.loads(mock_redis.publish.call_args[0][1])
        assert parsed["action"] == "speak_audio"

    @pytest.mark.asyncio
    async def test_speak_no_content(self, client, mock_redis, active_meeting):
        """POST /speak without text or audio → 400."""
        with _patch_find_active(active_meeting):
            resp = await client.post(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/speak",
                json={},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_speak_auth_required(self, unauthed_client):
        """POST /speak without auth → 403."""
        resp = await unauthed_client.post(
            f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/speak",
            json={"text": "Hello"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_speak_redis_unavailable(self, client, active_meeting):
        """POST /speak when Redis is None → 503."""
        from meeting_api import meetings as meetings_mod
        meetings_mod.set_redis(None)
        try:
            with _patch_find_active(active_meeting):
                resp = await client.post(
                    f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/speak",
                    json={"text": "Hello"},
                )
            assert resp.status_code == 503
        finally:
            # conftest will restore redis in cleanup, but be safe
            pass


# ===================================================================
# DELETE /bots/{platform}/{id}/speak (interrupt)
# ===================================================================


class TestSpeakStop:

    @pytest.mark.asyncio
    async def test_speak_stop(self, client, mock_redis, active_meeting):
        """DELETE /speak → publishes speak_stop command."""
        with _patch_find_active(active_meeting):
            resp = await client.delete(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/speak",
            )

        assert resp.status_code == 202
        parsed = json.loads(mock_redis.publish.call_args[0][1])
        assert parsed["action"] == "speak_stop"


# ===================================================================
# POST /bots/{platform}/{id}/chat
# ===================================================================


class TestChat:

    @pytest.mark.asyncio
    async def test_chat_send(self, client, mock_redis, active_meeting):
        """POST /chat with text → publishes chat_send to Redis."""
        with _patch_find_active(active_meeting):
            resp = await client.post(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/chat",
                json={"text": "Hello meeting!"},
            )

        assert resp.status_code == 202
        parsed = json.loads(mock_redis.publish.call_args[0][1])
        assert parsed["action"] == "chat_send"
        assert parsed["text"] == "Hello meeting!"

    @pytest.mark.asyncio
    async def test_chat_send_empty_text(self, client, mock_redis, active_meeting):
        """POST /chat without text → 400."""
        with _patch_find_active(active_meeting):
            resp = await client.post(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/chat",
                json={},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_chat_read_empty(self, client, mock_redis, active_meeting):
        """GET /chat with no messages → empty list."""
        with _patch_find_active(active_meeting):
            resp = await client.get(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/chat",
            )

        assert resp.status_code == 200
        assert resp.json()["messages"] == []

    @pytest.mark.asyncio
    async def test_chat_read_with_messages(self, client, mock_redis, active_meeting):
        """GET /chat reads messages from Redis."""
        mock_redis.lrange = AsyncMock(return_value=[
            json.dumps({"sender": "Alice", "text": "Hi"}),
            json.dumps({"sender": "Bob", "text": "Hello"}),
        ])

        with _patch_find_active(active_meeting):
            resp = await client.get(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/chat",
            )

        assert resp.status_code == 200
        assert len(resp.json()["messages"]) == 2

    @pytest.mark.asyncio
    async def test_chat_auth_required(self, unauthed_client):
        resp = await unauthed_client.post(
            f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/chat",
            json={"text": "Hello"},
        )
        assert resp.status_code == 403


# ===================================================================
# POST /bots/{platform}/{id}/screen
# ===================================================================


class TestScreen:

    @pytest.mark.asyncio
    async def test_screen_show_url(self, client, mock_redis, active_meeting):
        """POST /screen with url type → publishes screen_show."""
        with _patch_find_active(active_meeting):
            resp = await client.post(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/screen",
                json={"type": "url", "url": "https://example.com"},
            )

        assert resp.status_code == 202
        parsed = json.loads(mock_redis.publish.call_args[0][1])
        assert parsed["action"] == "screen_show"
        assert parsed["type"] == "url"

    @pytest.mark.asyncio
    async def test_screen_show_html(self, client, mock_redis, active_meeting):
        """POST /screen with html type → publishes with html content."""
        with _patch_find_active(active_meeting):
            resp = await client.post(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/screen",
                json={"type": "html", "html": "<h1>Hello</h1>"},
            )

        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_screen_invalid_type(self, client, mock_redis, active_meeting):
        """POST /screen with invalid type → 400."""
        with _patch_find_active(active_meeting):
            resp = await client.post(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/screen",
                json={"type": "invalid"},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_screen_stop(self, client, mock_redis, active_meeting):
        """DELETE /screen → publishes screen_stop."""
        with _patch_find_active(active_meeting):
            resp = await client.delete(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/screen",
            )

        assert resp.status_code == 202
        parsed = json.loads(mock_redis.publish.call_args[0][1])
        assert parsed["action"] == "screen_stop"

    @pytest.mark.asyncio
    async def test_screen_auth_required(self, unauthed_client):
        resp = await unauthed_client.post(
            f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/screen",
            json={"type": "url", "url": "https://example.com"},
        )
        assert resp.status_code == 403


# ===================================================================
# Avatar endpoints
# ===================================================================


class TestAvatar:

    @pytest.mark.asyncio
    async def test_avatar_set(self, client, mock_redis, active_meeting):
        """PUT /avatar → publishes avatar_set."""
        with _patch_find_active(active_meeting):
            resp = await client.put(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/avatar",
                json={"url": "https://example.com/avatar.png"},
            )

        assert resp.status_code == 202
        parsed = json.loads(mock_redis.publish.call_args[0][1])
        assert parsed["action"] == "avatar_set"

    @pytest.mark.asyncio
    async def test_avatar_set_no_content(self, client, mock_redis, active_meeting):
        """PUT /avatar without url or image_base64 → 400."""
        with _patch_find_active(active_meeting):
            resp = await client.put(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/avatar",
                json={},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_avatar_reset(self, client, mock_redis, active_meeting):
        """DELETE /avatar → publishes avatar_reset."""
        with _patch_find_active(active_meeting):
            resp = await client.delete(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/avatar",
            )

        assert resp.status_code == 202
        parsed = json.loads(mock_redis.publish.call_args[0][1])
        assert parsed["action"] == "avatar_reset"


# ===================================================================
# GET /bots/{platform}/{id}/events
# ===================================================================


class TestEvents:

    @pytest.mark.asyncio
    async def test_events_empty(self, client, mock_redis, active_meeting):
        """GET /events with no events → empty list."""
        with _patch_find_active(active_meeting):
            resp = await client.get(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/events",
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_events_with_data(self, client, mock_redis, active_meeting):
        """GET /events reads from Redis event log."""
        mock_redis.lrange = AsyncMock(return_value=[
            json.dumps({"type": "speak_start", "ts": "2025-01-01T00:00:00"}),
        ])

        with _patch_find_active(active_meeting):
            resp = await client.get(
                f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/events",
            )

        assert resp.status_code == 200
        assert resp.json()["count"] == 1
