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
    async def test_self_initiated_leave_during_stopping_completes(self, client, mock_db, mock_redis):
        """self_initiated_leave with exit code 1 during stopping → COMPLETED, not FAILED."""
        meeting = make_meeting(status=MeetingStatus.STOPPING.value)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True) as mock_update:
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                        resp = await client.post("/bots/internal/callback/exited", json={
                            "connection_id": TEST_SESSION_UID,
                            "exit_code": 1,
                            "reason": "self_initiated_leave",
                        })

        assert resp.status_code == 200
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][1] == MeetingStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_sigkill_during_stopping_completes(self, client, mock_db, mock_redis):
        """Exit code 137 (SIGKILL from docker stop) during stopping → COMPLETED, not FAILED."""
        meeting = make_meeting(status=MeetingStatus.STOPPING.value)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True) as mock_update:
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                        resp = await client.post("/bots/internal/callback/exited", json={
                            "connection_id": TEST_SESSION_UID,
                            "exit_code": 137,
                            "reason": "self_initiated_leave",
                        })

        assert resp.status_code == 200
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][1] == MeetingStatus.COMPLETED

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


# ===================================================================
# v0.10.5 FM-001/FM-002/FM-003 — central classifier coverage
# ===================================================================
#
# Pre-fix: bot exits with reason="post_join_setup_error" (gmeet
# end-of-meeting page navigation crash) hit the else branch at
# callbacks.py:311 → status=failed, completion_reason=NULL,
# failure_stage=ACTIVE. Prod 7d aggregate had 182 NULL-bucket rows
# (FM-002), 127 mislabeled failure_stage (FM-003), and meeting 11161
# (FM-001) — a 30-min gmeet meeting with 197 segments delivered painted
# as FAILED.
#
# Post-fix: the else branch routes through _classify_stopped_exit. ALL
# non-stopping exits go through the central classifier. Unknown bot
# reasons get logged WARN + stuffed into transition_metadata.unknown_bot_reason.
# failure_stage derives from meeting.status at write time.


class TestFM001ClassifierCoverage:
    """v0.10.5 FM-001/FM-002/FM-003 — every non-stopping exit routes through the classifier."""

    @pytest.mark.asyncio
    async def test_post_join_setup_error_with_segments_classified_completed(
        self, client, mock_db, mock_redis,
    ):
        """FM-001: gmeet end-of-meeting nav crash on a meeting that reached active and produced segments → COMPLETED.

        This is the meeting-11161 shape: bot exited code 1 with
        reason="post_join_setup_error", meeting reached active, transcripts
        persisted. Pre-fix → FAILED + NULL. Post-fix → COMPLETED + STOPPED.
        """
        meeting = make_meeting(
            status=MeetingStatus.ACTIVE.value,
            start_time=datetime.utcnow(),
            data={
                "status_transition": [
                    {"from": "joining", "to": "awaiting_admission"},
                    {"from": "awaiting_admission", "to": "active"},
                ],
                "transcribe_enabled": True,
            },
        )
        # Simulate >30s active + segments > 0
        from datetime import timedelta
        meeting.start_time = datetime.utcnow() - timedelta(minutes=30)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True) as mock_update:
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                        # Mock the segment-count query in _classify_stopped_exit
                        with patch("meeting_api.callbacks.select") as mock_select:
                            mock_db.execute = AsyncMock(return_value=MockResult(scalar_value=197))
                            resp = await client.post("/bots/internal/callback/exited", json={
                                "connection_id": TEST_SESSION_UID,
                                "exit_code": 1,
                                "reason": "post_join_setup_error",
                            })

        assert resp.status_code == 200
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        # Reached active + duration ≥ 30s + segments > 0 → COMPLETED
        assert call_args[0][1] == MeetingStatus.COMPLETED, (
            f"FM-001 regression: gmeet end-of-meeting nav exit not classified as COMPLETED, got {call_args[0][1]}"
        )

    @pytest.mark.asyncio
    async def test_unknown_bot_reason_logged_and_stashed(
        self, client, mock_db, mock_redis,
    ):
        """FM-002 canary: unknown payload.reason value gets WARN-logged + stashed in transition_metadata."""
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True) as mock_update:
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                        mock_db.execute = AsyncMock(return_value=MockResult(scalar_value=0))
                        resp = await client.post("/bots/internal/callback/exited", json={
                            "connection_id": TEST_SESSION_UID,
                            "exit_code": 1,
                            "reason": "some_future_uncatalogued_reason",
                        })

        assert resp.status_code == 200
        mock_update.assert_called_once()
        # transition_metadata should carry unknown_bot_reason
        meta = mock_update.call_args[1].get("transition_metadata", {})
        assert meta.get("unknown_bot_reason") == "some_future_uncatalogued_reason", (
            f"FM-002 canary failed: unknown_bot_reason not in transition_metadata: {meta}"
        )

    @pytest.mark.asyncio
    async def test_failure_stage_derived_from_meeting_status_not_payload(
        self, client, mock_db, mock_redis,
    ):
        """FM-003: failure_stage on a FAILED route derives from meeting.status, not payload.failure_stage.

        Bot reports failure_stage='joining' (stale from its internal tracker),
        but meeting.status='active'. Server-side derivation should write 'active'.
        """
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value)
        # No status_transition[] → not reached_active → classifier returns FAILED + STOPPED_BEFORE_ADMISSION

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True) as mock_update:
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                        mock_db.execute = AsyncMock(return_value=MockResult(scalar_value=0))
                        resp = await client.post("/bots/internal/callback/exited", json={
                            "connection_id": TEST_SESSION_UID,
                            "exit_code": 1,
                            "reason": "post_join_setup_error",
                            "failure_stage": "joining",   # bot reports stale stage
                        })

        assert resp.status_code == 200
        mock_update.assert_called_once()
        # target_status should be FAILED (no segments, didn't reach active)
        assert mock_update.call_args[0][1] == MeetingStatus.FAILED
        # failure_stage should be ACTIVE (derived from meeting.status), NOT 'joining' (from payload)
        kwargs = mock_update.call_args[1]
        assert kwargs.get("failure_stage") == MeetingFailureStage.ACTIVE, (
            f"FM-003 regression: failure_stage not derived from meeting.status, got {kwargs.get('failure_stage')}"
        )
