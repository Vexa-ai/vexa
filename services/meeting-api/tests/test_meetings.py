"""Tests for meetings CRUD — POST /bots, GET /bots/status, DELETE, PUT config.

Validates frozen API contracts (response shapes, field names) and
verifies Runtime API delegation via httpx mocks.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api.schemas import MeetingStatus, MeetingResponse, BotStatusResponse

from .conftest import (
    TEST_USER_ID,
    TEST_MEETING_ID,
    TEST_PLATFORM,
    TEST_NATIVE_MEETING_ID,
    TEST_CONTAINER_ID,
    TEST_CONTAINER_NAME,
    TEST_API_KEY,
    make_meeting,
    make_session,
    make_user,
    MockResult,
)


def _setup_create_meeting_db(mock_db):
    """Set up mock_db for the POST /bots standard flow.

    The endpoint makes several queries:
    1. Duplicate check (select existing meeting) → empty
    2. Count active meetings → 0
    After that: add, commit, refresh for the new meeting.
    """
    call_count = 0

    async def multi_execute(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Duplicate check → no existing meeting
            return MockResult([])
        elif call_count == 2:
            # Count active meetings → 0
            return MockResult(scalar_value=0)
        return MockResult()

    mock_db.execute = AsyncMock(side_effect=multi_execute)


# ===================================================================
# POST /bots — create meeting
# ===================================================================


class TestCreateMeeting:

    @pytest.mark.asyncio
    async def test_create_meeting_success(self, client, mock_db, mock_redis):
        """POST /bots with valid request → 201 with MeetingResponse shape."""
        _setup_create_meeting_db(mock_db)

        runtime_resp = {"container_id": TEST_CONTAINER_ID, "name": TEST_CONTAINER_NAME}
        with patch("meeting_api.meetings._spawn_via_runtime_api", new_callable=AsyncMock, return_value=runtime_resp):
            with patch("meeting_api.meetings.mint_meeting_token", return_value="fake.jwt.token"):
                with patch("meeting_api.meetings.async_session_local") as mock_session_factory:
                    # Mock the session used for MeetingSession creation
                    inner_db = AsyncMock()
                    inner_db.add = MagicMock()
                    inner_db.commit = AsyncMock()
                    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=inner_db)
                    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

                    resp = await client.post("/bots", json={
                        "platform": "google_meet",
                        "native_meeting_id": "abc-defg-hij",
                    })

        assert resp.status_code == 201
        data = resp.json()
        # Frozen field set
        expected_fields = {
            "id", "user_id", "platform", "native_meeting_id",
            "constructed_meeting_url", "status", "bot_container_id",
            "start_time", "end_time", "data", "created_at", "updated_at",
        }
        assert expected_fields.issubset(set(data.keys()))

    @pytest.mark.asyncio
    async def test_create_meeting_calls_runtime_api(self, client, mock_db, mock_redis):
        """POST /bots delegates container creation to Runtime API."""
        _setup_create_meeting_db(mock_db)

        runtime_resp = {"container_id": TEST_CONTAINER_ID, "name": TEST_CONTAINER_NAME}
        with patch("meeting_api.meetings._spawn_via_runtime_api", new_callable=AsyncMock, return_value=runtime_resp) as mock_spawn:
            with patch("meeting_api.meetings.mint_meeting_token", return_value="fake.jwt.token"):
                with patch("meeting_api.meetings.async_session_local") as mock_sf:
                    inner = AsyncMock()
                    inner.add = MagicMock()
                    inner.commit = AsyncMock()
                    mock_sf.return_value.__aenter__ = AsyncMock(return_value=inner)
                    mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

                    resp = await client.post("/bots", json={
                        "platform": "google_meet",
                        "native_meeting_id": "abc-defg-hij",
                    })

        assert resp.status_code == 201
        mock_spawn.assert_called_once()
        call_args = mock_spawn.call_args
        assert call_args[1].get("profile") == "meeting" or call_args[0][0] == "meeting"

    @pytest.mark.asyncio
    async def test_create_meeting_runtime_failure(self, client, mock_db, mock_redis):
        """POST /bots → 500 when Runtime API fails."""
        _setup_create_meeting_db(mock_db)

        with patch("meeting_api.meetings._spawn_via_runtime_api", new_callable=AsyncMock, return_value=None):
            with patch("meeting_api.meetings.mint_meeting_token", return_value="fake.jwt.token"):
                resp = await client.post("/bots", json={
                    "platform": "google_meet",
                    "native_meeting_id": "abc-defg-hij",
                })

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_create_meeting_auth_required(self, unauthed_client):
        """POST /bots without X-API-Key → 403."""
        resp = await unauthed_client.post("/bots", json={
            "platform": "google_meet",
            "native_meeting_id": "abc-defg-hij",
        })
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_meeting_invalid_platform(self, client):
        """POST /bots with invalid platform → 422."""
        resp = await client.post("/bots", json={
            "platform": "invalid_platform",
            "native_meeting_id": "abc-defg-hij",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_meeting_agent_mode(self, client, mock_db, mock_redis):
        """POST /bots with agent_enabled=true, no platform → 201."""
        runtime_resp = {"container_id": TEST_CONTAINER_ID, "name": TEST_CONTAINER_NAME}

        with patch("meeting_api.meetings._spawn_via_runtime_api", new_callable=AsyncMock, return_value=runtime_resp):
            resp = await client.post("/bots", json={
                "agent_enabled": True,
            })

        assert resp.status_code == 201


# ===================================================================
# GET /bots/status — list running bots
# ===================================================================


class TestGetBotsStatus:

    @pytest.mark.asyncio
    async def test_bots_status_returns_running_bots(self, client, mock_db, mock_redis):
        """GET /bots/status → {running_bots: [...]} with frozen fields."""
        with patch("meeting_api.meetings._get_running_bots_from_runtime", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [{
                "container_id": TEST_CONTAINER_ID,
                "container_name": f"meeting-bot-{TEST_MEETING_ID}-abc",
                "platform": TEST_PLATFORM,
                "native_meeting_id": TEST_NATIVE_MEETING_ID,
                "status": "running",
                "normalized_status": "Up",
                "created_at": "2023-11-14T22:13:20+00:00",
                "start_time": None,
                "labels": {},
                "meeting_id_from_name": str(TEST_MEETING_ID),
                "data": {},
            }]

            resp = await client.get("/bots/status")

        assert resp.status_code == 200
        data = resp.json()
        assert "running_bots" in data
        assert isinstance(data["running_bots"], list)
        if data["running_bots"]:
            bot = data["running_bots"][0]
            frozen_fields = {
                "container_id", "container_name", "platform",
                "native_meeting_id", "status", "normalized_status",
                "created_at", "start_time", "labels",
                "meeting_id_from_name", "data",
            }
            assert frozen_fields.issubset(set(bot.keys()))

    @pytest.mark.asyncio
    async def test_bots_status_empty(self, client, mock_db, mock_redis):
        """GET /bots/status when no bots running → empty list."""
        with patch("meeting_api.meetings._get_running_bots_from_runtime", new_callable=AsyncMock, return_value=[]):
            resp = await client.get("/bots/status")

        assert resp.status_code == 200
        assert resp.json()["running_bots"] == []

    @pytest.mark.asyncio
    async def test_bots_status_auth_required(self, unauthed_client):
        """GET /bots/status without auth → 403."""
        resp = await unauthed_client.get("/bots/status")
        assert resp.status_code == 403


# ===================================================================
# DELETE /bots/{platform}/{native_meeting_id} — stop bot
# ===================================================================


class TestStopBot:

    @pytest.mark.asyncio
    async def test_stop_bot_success(self, client, mock_db, mock_redis):
        """DELETE /bots/{platform}/{id} → 202 for active meeting."""
        meeting = make_meeting(
            status=MeetingStatus.ACTIVE.value,
            bot_container_id=TEST_CONTAINER_NAME,
        )
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        with patch("meeting_api.meetings.update_meeting_status", new_callable=AsyncMock, return_value=True):
            with patch("meeting_api.meetings._delayed_container_stop", new_callable=AsyncMock):
                resp = await client.delete(f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}")

        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_stop_bot_not_found(self, client, mock_db, mock_redis):
        """DELETE /bots/{platform}/{id} for non-existent meeting → 404."""
        mock_db.execute = AsyncMock(return_value=MockResult([]))

        resp = await client.delete(f"/bots/{TEST_PLATFORM}/nonexistent-id")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_stop_bot_already_completed(self, client, mock_db, mock_redis):
        """DELETE /bots/{platform}/{id} for completed meeting → message about already stopped."""
        meeting = make_meeting(status=MeetingStatus.COMPLETED.value)
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        resp = await client.delete(f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}")
        assert resp.status_code == 202
        assert "already" in resp.json().get("message", "").lower()

    @pytest.mark.asyncio
    async def test_stop_bot_sends_leave_via_redis(self, client, mock_db, mock_redis):
        """DELETE /bots publishes leave command to Redis channel."""
        meeting = make_meeting(
            status=MeetingStatus.ACTIVE.value,
            bot_container_id=TEST_CONTAINER_NAME,
        )
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        with patch("meeting_api.meetings.update_meeting_status", new_callable=AsyncMock, return_value=True):
            with patch("meeting_api.meetings._delayed_container_stop", new_callable=AsyncMock):
                resp = await client.delete(f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}")

        # Verify Redis publish with leave action
        publish_calls = mock_redis.publish.call_args_list
        leave_published = any(
            "leave" in str(call)
            for call in publish_calls
        )
        assert leave_published

    @pytest.mark.asyncio
    async def test_stop_bot_auth_required(self, unauthed_client):
        """DELETE /bots/{platform}/{id} without auth → 403."""
        resp = await unauthed_client.delete(f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}")
        assert resp.status_code == 403


# ===================================================================
# PUT /bots/{platform}/{meeting_id}/config — reconfigure
# ===================================================================


class TestUpdateBotConfig:

    @pytest.mark.asyncio
    async def test_reconfigure_success(self, client, mock_db, mock_redis):
        """PUT /bots/{platform}/{id}/config → 202, publishes to Redis."""
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value)
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        resp = await client.put(
            f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/config",
            json={"language": "es", "task": "translate"},
        )

        assert resp.status_code == 202
        # Verify Redis publish with reconfigure action
        mock_redis.publish.assert_called()
        channel, payload = mock_redis.publish.call_args[0]
        assert f"bot_commands:meeting:{TEST_MEETING_ID}" == channel
        parsed = json.loads(payload)
        assert parsed["action"] == "reconfigure"
        assert parsed["language"] == "es"

    @pytest.mark.asyncio
    async def test_reconfigure_no_active_meeting(self, client, mock_db, mock_redis):
        """PUT config for non-active meeting → 404 or 409."""
        mock_db.execute = AsyncMock(return_value=MockResult([]))

        resp = await client.put(
            f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/config",
            json={"language": "es"},
        )
        assert resp.status_code in (404, 409)

    @pytest.mark.asyncio
    async def test_reconfigure_auth_required(self, unauthed_client):
        """PUT config without auth → 403."""
        resp = await unauthed_client.put(
            f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/config",
            json={"language": "es"},
        )
        assert resp.status_code == 403
