"""Integration tests — full bot lifecycle flows using TestClient.

Tests end-to-end flows: create → callback → exit, create → stop,
status change → Redis publish.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api.schemas import MeetingStatus

from .conftest import (
    TEST_USER_ID,
    TEST_MEETING_ID,
    TEST_SESSION_UID,
    TEST_CONTAINER_ID,
    TEST_CONTAINER_NAME,
    TEST_PLATFORM,
    TEST_NATIVE_MEETING_ID,
    make_meeting,
    make_session,
    make_user,
    MockResult,
)


def _patch_flag_modified():
    return patch("meeting_api.callbacks.attributes.flag_modified", MagicMock())


def _setup_create_meeting_db(mock_db):
    """Set up mock_db for POST /bots standard flow."""
    test_user = make_user()
    call_count = 0

    async def multi_execute(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MockResult([])
        elif call_count == 2:
            return MockResult([test_user])
        elif call_count == 3:
            return MockResult(scalar_value=0)
        return MockResult()

    mock_db.execute = AsyncMock(side_effect=multi_execute)


# ===================================================================
# Full lifecycle: create → started callback → exit callback
# ===================================================================


class TestCreateToExitFlow:

    @pytest.mark.asyncio
    async def test_create_then_started_then_exited(self, client, mock_db, mock_redis):
        """POST /bots → callback/started → callback/exited → final state."""
        # Step 1: Create meeting
        _setup_create_meeting_db(mock_db)
        runtime_resp = {"container_id": TEST_CONTAINER_ID, "name": TEST_CONTAINER_NAME}

        with patch("meeting_api.meetings._spawn_via_runtime_api", new_callable=AsyncMock, return_value=runtime_resp):
            with patch("meeting_api.meetings.mint_meeting_token", return_value="fake.jwt.token"):
                with patch("meeting_api.meetings.async_session_local") as mock_sf:
                    inner = AsyncMock()
                    inner.add = MagicMock()
                    inner.commit = AsyncMock()
                    mock_sf.return_value.__aenter__ = AsyncMock(return_value=inner)
                    mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

                    create_resp = await client.post("/bots", json={
                        "platform": "google_meet",
                        "native_meeting_id": "abc-defg-hij",
                    })

        assert create_resp.status_code == 201

        # Step 2: Started callback
        meeting_requested = make_meeting(status=MeetingStatus.REQUESTED.value)
        ms = make_session()

        with patch("meeting_api.callbacks._find_meeting_by_session", new_callable=AsyncMock, return_value=(ms, meeting_requested)):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    started_resp = await client.post("/bots/internal/callback/started", json={
                        "connection_id": TEST_SESSION_UID,
                        "container_id": TEST_CONTAINER_ID,
                    })

        assert started_resp.status_code == 200
        assert started_resp.json()["status"] == "startup processed"

        # Step 3: Exit callback
        meeting_active = make_meeting(status=MeetingStatus.ACTIVE.value)

        with patch("meeting_api.callbacks._find_meeting_by_session", new_callable=AsyncMock, return_value=(ms, meeting_active)):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                        exit_resp = await client.post("/bots/internal/callback/exited", json={
                            "connection_id": TEST_SESSION_UID,
                            "exit_code": 0,
                        })

        assert exit_resp.status_code == 200
        assert exit_resp.json()["status"] == "callback processed"


# ===================================================================
# Create → stop flow
# ===================================================================


class TestCreateToStopFlow:

    @pytest.mark.asyncio
    async def test_create_then_stop(self, client, mock_db, mock_redis):
        """POST /bots → DELETE /bots/{platform}/{id} → verify stop."""
        # Step 1: Create
        _setup_create_meeting_db(mock_db)
        runtime_resp = {"container_id": TEST_CONTAINER_ID, "name": TEST_CONTAINER_NAME}

        with patch("meeting_api.meetings._spawn_via_runtime_api", new_callable=AsyncMock, return_value=runtime_resp):
            with patch("meeting_api.meetings.mint_meeting_token", return_value="fake.jwt.token"):
                with patch("meeting_api.meetings.async_session_local") as mock_sf:
                    inner = AsyncMock()
                    inner.add = MagicMock()
                    inner.commit = AsyncMock()
                    mock_sf.return_value.__aenter__ = AsyncMock(return_value=inner)
                    mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

                    create_resp = await client.post("/bots", json={
                        "platform": "google_meet",
                        "native_meeting_id": "abc-defg-hij",
                    })

        assert create_resp.status_code == 201

        # Step 2: Stop
        active_meeting = make_meeting(
            status=MeetingStatus.ACTIVE.value,
            bot_container_id=TEST_CONTAINER_NAME,
        )
        mock_db.execute = AsyncMock(return_value=MockResult([active_meeting]))

        with patch("meeting_api.meetings.update_meeting_status", new_callable=AsyncMock, return_value=True):
            with patch("meeting_api.meetings._delayed_container_stop", new_callable=AsyncMock):
                stop_resp = await client.delete(f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}")

        assert stop_resp.status_code == 202

        # Verify leave command was published
        leave_calls = [
            call for call in mock_redis.publish.call_args_list
            if "leave" in str(call)
        ]
        assert len(leave_calls) > 0


# ===================================================================
# Status change → Redis publish flow
# ===================================================================


class TestStatusChangeToRedisFlow:

    @pytest.mark.asyncio
    async def test_status_change_publishes_to_redis(self, client, mock_db, mock_redis):
        """Status change callback → publish_meeting_status_change called → Redis publish."""
        meeting = make_meeting(status=MeetingStatus.REQUESTED.value)
        ms = make_session()

        with patch("meeting_api.callbacks._find_meeting_by_session", new_callable=AsyncMock, return_value=(ms, meeting)):
            with _patch_flag_modified():
                with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                    with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock) as mock_pub:
                        with patch("meeting_api.callbacks.schedule_status_webhook_task", new_callable=AsyncMock):
                            resp = await client.post("/bots/internal/callback/status_change", json={
                                "connection_id": TEST_SESSION_UID,
                                "container_id": TEST_CONTAINER_ID,
                                "status": "active",
                            })

        assert resp.status_code == 200
        mock_pub.assert_called_once()
        call_args = mock_pub.call_args
        assert call_args[0][1] == MeetingStatus.ACTIVE.value


# ===================================================================
# Callback → webhook flow
# ===================================================================


class TestCallbackToWebhookFlow:

    @pytest.mark.asyncio
    async def test_status_change_triggers_webhook(self, client, mock_db, mock_redis):
        """Status change callback → schedule_status_webhook_task called."""
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value)
        ms = make_session()

        with patch("meeting_api.callbacks._find_meeting_by_session", new_callable=AsyncMock, return_value=(ms, meeting)):
            with _patch_flag_modified():
                with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                    with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                        with patch("meeting_api.callbacks.schedule_status_webhook_task", new_callable=AsyncMock) as mock_webhook:
                            with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                                resp = await client.post("/bots/internal/callback/status_change", json={
                                    "connection_id": TEST_SESSION_UID,
                                    "status": "completed",
                                })

        assert resp.status_code == 200
        mock_webhook.assert_called_once()


# ===================================================================
# NEEDS_HUMAN_HELP → Redis session flow
# ===================================================================


class TestEscalationFlow:

    @pytest.mark.asyncio
    async def test_needs_human_help_stores_session_in_redis(self, client, mock_db, mock_redis):
        """NEEDS_HUMAN_HELP → stores browser_session:{token} in Redis."""
        meeting = make_meeting(
            status=MeetingStatus.ACTIVE.value,
            bot_container_id=TEST_CONTAINER_NAME,
            data={},
        )
        ms = make_session()

        with patch("meeting_api.callbacks._find_meeting_by_session", new_callable=AsyncMock, return_value=(ms, meeting)):
            with _patch_flag_modified():
                with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                    with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                        with patch("meeting_api.callbacks.schedule_status_webhook_task", new_callable=AsyncMock):
                            resp = await client.post("/bots/internal/callback/status_change", json={
                                "connection_id": TEST_SESSION_UID,
                                "container_id": TEST_CONTAINER_ID,
                                "status": "needs_human_help",
                                "reason": "captcha_detected",
                            })

        assert resp.status_code == 200

        # Verify Redis.set was called with browser_session key
        set_calls = mock_redis.set.call_args_list
        browser_session_calls = [
            c for c in set_calls
            if "browser_session:" in str(c)
        ]
        assert len(browser_session_calls) > 0
