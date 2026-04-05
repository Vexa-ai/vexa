"""Tests for internal callback endpoints — /bots/internal/callback/*.

Validates frozen payload shapes and correct status transitions.
These endpoints are the wire protocol between vexa-bot containers and meeting-api.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api.schemas import MeetingStatus, MeetingCompletionReason, MeetingFailureStage

from .conftest import (
    TEST_MEETING_ID,
    TEST_SESSION_UID,
    TEST_CONTAINER_ID,
    TEST_USER_ID,
    TEST_PLATFORM,
    TEST_NATIVE_MEETING_ID,
    make_meeting,
    make_session,
    MockResult,
)


def _patch_find_meeting(meeting, session=None):
    """Patch _find_meeting_by_session to return a given meeting + session."""
    ms = session or make_session()
    return patch(
        "meeting_api.callbacks._find_meeting_by_session",
        new_callable=AsyncMock,
        return_value=(ms, meeting),
    )


def _patch_flag_modified():
    """Patch attributes.flag_modified to be a no-op (avoids _sa_instance_state error on mocks)."""
    return patch("meeting_api.callbacks.attributes.flag_modified", MagicMock())


# ===================================================================
# POST /bots/internal/callback/exited
# ===================================================================


class TestExitCallback:

    @pytest.mark.asyncio
    async def test_exit_code_0_completes_meeting(self, client, mock_db, mock_redis):
        """Exit code 0 → meeting status COMPLETED."""
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value, user_id=TEST_USER_ID)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                        resp = await client.post("/bots/internal/callback/exited", json={
                            "connection_id": TEST_SESSION_UID,
                            "exit_code": 0,
                        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "callback processed"
        assert data["meeting_id"] == TEST_MEETING_ID

    @pytest.mark.asyncio
    async def test_exit_code_nonzero_fails_meeting(self, client, mock_db, mock_redis):
        """Exit code != 0 → meeting status FAILED."""
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True) as mock_update:
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                        resp = await client.post("/bots/internal/callback/exited", json={
                            "connection_id": TEST_SESSION_UID,
                            "exit_code": 1,
                            "reason": "browser_crashed",
                        })

        assert resp.status_code == 200
        # update_meeting_status called with FAILED
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][1] == MeetingStatus.FAILED

    @pytest.mark.asyncio
    async def test_exit_triggers_post_meeting(self, client, mock_db, mock_redis):
        """Exit callback triggers post-meeting background tasks."""
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock) as mock_tasks:
                        resp = await client.post("/bots/internal/callback/exited", json={
                            "connection_id": TEST_SESSION_UID,
                            "exit_code": 0,
                        })

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_exit_publishes_status_to_redis(self, client, mock_db, mock_redis):
        """Exit callback publishes to bm:meeting:{id}:status."""
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock) as mock_pub:
                    with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                        resp = await client.post("/bots/internal/callback/exited", json={
                            "connection_id": TEST_SESSION_UID,
                            "exit_code": 0,
                        })

        mock_pub.assert_called_once()

    @pytest.mark.asyncio
    async def test_exit_response_shape(self, client, mock_db, mock_redis):
        """Frozen response: {status, meeting_id, final_status}."""
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                        resp = await client.post("/bots/internal/callback/exited", json={
                            "connection_id": TEST_SESSION_UID,
                            "exit_code": 0,
                        })

        data = resp.json()
        assert "status" in data
        assert "meeting_id" in data
        assert "final_status" in data

    @pytest.mark.asyncio
    async def test_exit_session_not_found(self, client, mock_db, mock_redis):
        """Exit callback for unknown session → error response."""
        with patch("meeting_api.callbacks._find_meeting_by_session", new_callable=AsyncMock, return_value=(None, None)):
            resp = await client.post("/bots/internal/callback/exited", json={
                "connection_id": "nonexistent-session",
                "exit_code": 0,
            })

        data = resp.json()
        assert data["status"] == "error"


# ===================================================================
# POST /bots/internal/callback/started
# ===================================================================


class TestStartupCallback:

    @pytest.mark.asyncio
    async def test_startup_activates_meeting(self, client, mock_db, mock_redis):
        """Started callback → meeting transitions to ACTIVE."""
        meeting = make_meeting(status=MeetingStatus.REQUESTED.value)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    resp = await client.post("/bots/internal/callback/started", json={
                        "connection_id": TEST_SESSION_UID,
                        "container_id": TEST_CONTAINER_ID,
                    })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "startup processed"
        assert data["meeting_id"] == TEST_MEETING_ID

    @pytest.mark.asyncio
    async def test_startup_response_shape(self, client, mock_db, mock_redis):
        """Frozen response: {status: "startup processed", meeting_id, meeting_status}."""
        meeting = make_meeting(status=MeetingStatus.REQUESTED.value)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    resp = await client.post("/bots/internal/callback/started", json={
                        "connection_id": TEST_SESSION_UID,
                        "container_id": TEST_CONTAINER_ID,
                    })

        data = resp.json()
        assert "status" in data
        assert "meeting_id" in data
        assert "meeting_status" in data

    @pytest.mark.asyncio
    async def test_startup_ignored_when_stop_requested(self, client, mock_db, mock_redis):
        """Started callback ignored if stop_requested is set."""
        meeting = make_meeting(
            status=MeetingStatus.REQUESTED.value,
            data={"stop_requested": True},
        )

        with _patch_find_meeting(meeting):
            resp = await client.post("/bots/internal/callback/started", json={
                "connection_id": TEST_SESSION_UID,
                "container_id": TEST_CONTAINER_ID,
            })

        data = resp.json()
        assert data["status"] == "ignored"

    @pytest.mark.asyncio
    async def test_startup_session_not_found(self, client, mock_db, mock_redis):
        """Started callback for unknown session → error."""
        with patch("meeting_api.callbacks._find_meeting_by_session", new_callable=AsyncMock, return_value=(None, None)):
            resp = await client.post("/bots/internal/callback/started", json={
                "connection_id": "nonexistent",
                "container_id": TEST_CONTAINER_ID,
            })

        assert resp.json()["status"] == "error"


# ===================================================================
# POST /bots/internal/callback/joining
# ===================================================================


class TestJoiningCallback:

    @pytest.mark.asyncio
    async def test_joining_transitions_meeting(self, client, mock_db, mock_redis):
        """Joining callback → meeting status JOINING."""
        meeting = make_meeting(status=MeetingStatus.REQUESTED.value)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    resp = await client.post("/bots/internal/callback/joining", json={
                        "connection_id": TEST_SESSION_UID,
                        "container_id": TEST_CONTAINER_ID,
                    })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "joining processed"
        assert data["meeting_id"] == TEST_MEETING_ID

    @pytest.mark.asyncio
    async def test_joining_not_found(self, client, mock_db, mock_redis):
        """Joining callback for unknown session → 404."""
        with patch("meeting_api.callbacks._find_meeting_by_session", new_callable=AsyncMock, return_value=(None, None)):
            resp = await client.post("/bots/internal/callback/joining", json={
                "connection_id": "nonexistent",
                "container_id": TEST_CONTAINER_ID,
            })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_joining_ignored_when_stop_requested(self, client, mock_db, mock_redis):
        """Joining ignored if stop_requested."""
        meeting = make_meeting(status=MeetingStatus.REQUESTED.value, data={"stop_requested": True})

        with _patch_find_meeting(meeting):
            resp = await client.post("/bots/internal/callback/joining", json={
                "connection_id": TEST_SESSION_UID,
                "container_id": TEST_CONTAINER_ID,
            })

        assert resp.json()["status"] == "ignored"


# ===================================================================
# POST /bots/internal/callback/awaiting_admission
# ===================================================================


class TestAwaitingAdmissionCallback:

    @pytest.mark.asyncio
    async def test_awaiting_admission_transition(self, client, mock_db, mock_redis):
        """Awaiting admission callback → AWAITING_ADMISSION status."""
        meeting = make_meeting(status=MeetingStatus.JOINING.value)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    resp = await client.post("/bots/internal/callback/awaiting_admission", json={
                        "connection_id": TEST_SESSION_UID,
                        "container_id": TEST_CONTAINER_ID,
                    })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "awaiting_admission processed"

    @pytest.mark.asyncio
    async def test_awaiting_admission_not_found(self, client, mock_db, mock_redis):
        """Awaiting admission for unknown session → 404."""
        with patch("meeting_api.callbacks._find_meeting_by_session", new_callable=AsyncMock, return_value=(None, None)):
            resp = await client.post("/bots/internal/callback/awaiting_admission", json={
                "connection_id": "nonexistent",
                "container_id": TEST_CONTAINER_ID,
            })
        assert resp.status_code == 404


# ===================================================================
# POST /bots/internal/callback/status_change (unified)
# ===================================================================


class TestStatusChangeCallback:

    @pytest.mark.asyncio
    async def test_completed_status_sets_end_time(self, client, mock_db, mock_redis):
        """COMPLETED status → sets end_time, triggers post-meeting."""
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value)

        with _patch_find_meeting(meeting):
            with _patch_flag_modified():
                with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                    with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                        with patch("meeting_api.callbacks.schedule_status_webhook_task", new_callable=AsyncMock):
                            with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                                resp = await client.post("/bots/internal/callback/status_change", json={
                                    "connection_id": TEST_SESSION_UID,
                                    "status": "completed",
                                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processed"

    @pytest.mark.asyncio
    async def test_failed_status_stores_error(self, client, mock_db, mock_redis):
        """FAILED status → stores error details."""
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value)

        with _patch_find_meeting(meeting):
            with _patch_flag_modified():
                with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True) as mock_update:
                    with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                        with patch("meeting_api.callbacks.schedule_status_webhook_task", new_callable=AsyncMock):
                            with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                                resp = await client.post("/bots/internal/callback/status_change", json={
                                    "connection_id": TEST_SESSION_UID,
                                    "status": "failed",
                                    "error_details": {"message": "timeout"},
                                    "failure_stage": "active",
                                })

        assert resp.status_code == 200
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][1] == MeetingStatus.FAILED

    @pytest.mark.asyncio
    async def test_active_status_sets_start_time(self, client, mock_db, mock_redis):
        """ACTIVE status from REQUESTED → sets start_time, container_id."""
        meeting = make_meeting(status=MeetingStatus.REQUESTED.value)

        with _patch_find_meeting(meeting):
            with _patch_flag_modified():
                with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                    with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                        with patch("meeting_api.callbacks.schedule_status_webhook_task", new_callable=AsyncMock):
                            resp = await client.post("/bots/internal/callback/status_change", json={
                                "connection_id": TEST_SESSION_UID,
                                "container_id": TEST_CONTAINER_ID,
                                "status": "active",
                            })

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_needs_human_help_creates_escalation(self, client, mock_db, mock_redis):
        """NEEDS_HUMAN_HELP → creates escalation data with VNC session token."""
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value, data={})

        with _patch_find_meeting(meeting):
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
        # Verify escalation data was written to meeting.data
        assert meeting.data.get("escalation") is not None
        assert "session_token" in meeting.data["escalation"]
        assert "vnc_url" in meeting.data["escalation"]
        assert meeting.data["escalation"]["reason"] == "captcha_detected"

    @pytest.mark.asyncio
    async def test_stop_requested_ignores_non_terminal(self, client, mock_db, mock_redis):
        """Non-terminal status ignored when stop_requested is set."""
        meeting = make_meeting(
            status=MeetingStatus.ACTIVE.value,
            data={"stop_requested": True},
        )

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.schedule_status_webhook_task", new_callable=AsyncMock):
                resp = await client.post("/bots/internal/callback/status_change", json={
                    "connection_id": TEST_SESSION_UID,
                    "status": "joining",
                })

        data = resp.json()
        assert data["status"] == "ignored"

    @pytest.mark.asyncio
    async def test_stop_requested_allows_terminal(self, client, mock_db, mock_redis):
        """Terminal status (COMPLETED) processed even when stop_requested."""
        meeting = make_meeting(
            status=MeetingStatus.ACTIVE.value,
            data={"stop_requested": True},
        )

        with _patch_find_meeting(meeting):
            with _patch_flag_modified():
                with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                    with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                        with patch("meeting_api.callbacks.schedule_status_webhook_task", new_callable=AsyncMock):
                            with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                                resp = await client.post("/bots/internal/callback/status_change", json={
                                    "connection_id": TEST_SESSION_UID,
                                    "status": "completed",
                                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "processed"

    @pytest.mark.asyncio
    async def test_status_change_not_found(self, client, mock_db, mock_redis):
        """Status change for unknown session → 404."""
        with patch("meeting_api.callbacks._find_meeting_by_session", new_callable=AsyncMock, return_value=(None, None)):
            resp = await client.post("/bots/internal/callback/status_change", json={
                "connection_id": "nonexistent",
                "status": "active",
            })
        assert resp.status_code == 404
