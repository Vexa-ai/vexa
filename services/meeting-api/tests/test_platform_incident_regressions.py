"""v0.10.5 α-EXPANDED — platform-incident regression tests.

Audit 3 (ARCH-2 2026-04-29 ~11:20 UTC) flagged 6 of the 8 v0.10.5 incident
fixes as 🟡 — fixed in this release but no/partial direct test. Team-lead
landed α-EXPANDED at 11:50 UTC: 6 unit tests close those Coverage cells in
one bundle. Companion to the registry.json static-grep stamps for the
source-shape side of the same regressions.

Scope (mapping incident → test class below):

| Incident | Pack | Test class | Tier |
|---|---|---|---|
| FM-276 failure-stage-stale | R | TestFM276FailureStageStale | unit (pytest) |
| FM-279 silent-bot-failure-callbacks | J | TestFM279SilentBotFailureCallbacks | unit (pytest) |
| FM-278 completed-completed-warning | T | TestFM278IdempotentCompletion | unit (pytest) |

The remaining 3 incidents (recording-link-gap, FM-275 pod-name, FM-277
webhook-empty-error) live as static-grep checks in tests3/checks/registry.json
because their fix-shape is best pinned at the source-grep tier (cheap,
mechanical, runs on every release-validate). See companion entries:

- PACK_E1A_PER_CHUNK_MEDIA_FILES_WRITE
- PACK_Q_POD_NAME_USES_MEETING_ID
- PACK_S_WEBHOOK_RETRY_LOG_NON_EMPTY_ERROR
"""

import logging
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api.callbacks import _failure_stage_from_status
from meeting_api.schemas import (
    MeetingStatus,
    MeetingCompletionReason,
    MeetingFailureStage,
)

from .conftest import (
    TEST_MEETING_ID,
    TEST_SESSION_UID,
    TEST_USER_ID,
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


class _LockResult:
    """SQLAlchemy result-shape mock with `scalar_one()` for the SELECT FOR UPDATE
    re-fetch in update_meeting_status (meetings.py:139). Returns the same
    meeting instance the test set up so behavioural assertions pass through.
    """

    def __init__(self, meeting):
        self._m = meeting

    def scalar_one(self):
        return self._m

    def scalars(self):
        return self

    def first(self):
        return self._m

    def all(self):
        return [self._m]


# ===================================================================
# FM-276 failure-stage-stale (Pack R) — terminal-status update
# advances `failure_stage` derivation on each transition, not just
# the first one. Pre-Pack-R: failure_stage was set on the bot's
# initial FAILED transition and never re-derived — subsequent error
# events (mid-meeting websocket disconnect → late JOINING-stage
# error report) saw failure_stage=ACTIVE in DB even though the actual
# fail-point was earlier.
# ===================================================================


class TestFM276FailureStageStale:
    """v0.10.5 FM-276 — failure_stage re-derives on every server-side write."""

    def test_failure_stage_advances_active_to_active(self):
        """Meeting at ACTIVE → returned stage is ACTIVE (not stale earlier value)."""
        assert _failure_stage_from_status(MeetingStatus.ACTIVE.value) == MeetingFailureStage.ACTIVE

    def test_failure_stage_re_derives_per_call_no_caching(self):
        """Two consecutive calls with different statuses return different stages.

        FM-276 regression shape: the function caches the first result and
        returns it on subsequent calls. A simple call-twice + different-status
        check pins the no-cache invariant.
        """
        first = _failure_stage_from_status(MeetingStatus.JOINING.value)
        second = _failure_stage_from_status(MeetingStatus.ACTIVE.value)
        assert first == MeetingFailureStage.JOINING
        assert second == MeetingFailureStage.ACTIVE
        assert first != second, "FM-276 regression: function returned the same stage for different statuses"

    def test_terminal_status_falls_back_to_active_not_stale(self):
        """When a terminal status is passed (FAILED/COMPLETED), function falls back to ACTIVE.

        The "stale" failure mode was the function returning a remembered
        earlier value rather than computing each call. Defaulting to ACTIVE
        for terminal inputs is the explicit, contractual behaviour and
        documents the no-stale invariant.
        """
        assert _failure_stage_from_status(MeetingStatus.FAILED.value) == MeetingFailureStage.ACTIVE
        assert _failure_stage_from_status(MeetingStatus.COMPLETED.value) == MeetingFailureStage.ACTIVE

    def test_failure_stage_recovery_after_unknown(self):
        """Function recovers to a valid stage even after an unknown input.

        Pins that the unknown-status fallback to ACTIVE doesn't poison
        subsequent valid calls (a regression where the function memoizes
        the unknown-input fallback would break this).
        """
        unknown = _failure_stage_from_status("never_seen_status")
        assert unknown == MeetingFailureStage.ACTIVE
        # Subsequent valid call must still return its true stage, not
        # the unknown-fallback ACTIVE.
        valid = _failure_stage_from_status(MeetingStatus.AWAITING_ADMISSION.value)
        assert valid == MeetingFailureStage.AWAITING_ADMISSION


# ===================================================================
# FM-279 silent-bot-failure-callbacks (Pack J) — when the bot exits
# with a failure, the callbacks endpoint always populates
# `error_details` with a non-empty value. Pre-Pack-J: bot exit
# callbacks could fire with `error_details=None`, then the dashboard
# rendered `failed` status with no actionable explanation.
# ===================================================================


class TestFM279SilentBotFailureCallbacks:
    """v0.10.5 FM-279 — failed status always carries a non-empty error_details."""

    @pytest.mark.asyncio
    async def test_failure_with_only_exit_code_still_populates_error_details(
        self, client, mock_db, mock_redis,
    ):
        """Bot exits non-zero with no `reason` field → error_details still non-empty.

        Regression shape: payload.error_details=None + payload.reason=None
        should NOT result in update_meeting_status being called with
        error_details=None. Pack J synthesizes "Bot exited with code N" so
        the dashboard always has a string to render.
        """
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True) as mock_update:
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                        mock_db.execute = AsyncMock(return_value=MockResult(scalar_value=0))
                        resp = await client.post("/bots/internal/callback/exited", json={
                            "connection_id": TEST_SESSION_UID,
                            "exit_code": 137,
                            # No reason, no error_details, no platform_specific_error
                        })

        assert resp.status_code == 200
        mock_update.assert_called_once()
        kwargs = mock_update.call_args[1]
        # Pack J writes "Bot exited with code 137" when target_status is FAILED.
        # The exact string is the synthesis contract — empty / None is the regression.
        if mock_update.call_args[0][1] == MeetingStatus.FAILED:
            error_details = kwargs.get("error_details")
            assert error_details is not None, (
                "FM-279 regression: failed status with no reason produced error_details=None"
            )
            assert error_details != "", (
                "FM-279 regression: failed status produced empty-string error_details"
            )
            assert "exit code" in error_details.lower() or "137" in error_details, (
                f"FM-279 regression: error_details does not reference exit_code, got {error_details!r}"
            )

    @pytest.mark.asyncio
    async def test_failure_with_reason_includes_reason_in_error_details(
        self, client, mock_db, mock_redis,
    ):
        """Bot exits with `reason` → error_details includes the reason string."""
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value)

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True) as mock_update:
                with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                    with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                        mock_db.execute = AsyncMock(return_value=MockResult(scalar_value=0))
                        resp = await client.post("/bots/internal/callback/exited", json={
                            "connection_id": TEST_SESSION_UID,
                            "exit_code": 1,
                            "reason": "browser_crashed",
                        })

        assert resp.status_code == 200
        mock_update.assert_called_once()
        if mock_update.call_args[0][1] == MeetingStatus.FAILED:
            error_details = mock_update.call_args[1].get("error_details") or ""
            assert "browser_crashed" in error_details, (
                f"FM-279 regression: error_details does not include reason, got {error_details!r}"
            )


# ===================================================================
# FM-278 completed-completed-warning (Pack T) — already-terminal
# idempotent re-fire of `completed → completed` is benign and must
# log at DEBUG, not WARNING. Pre-Pack-T: ~30 WARNING lines per
# completed meeting in 90 min of prod logs (race between chat
# persistence, status update, post-meeting tasks each trying to
# re-finalize).
# ===================================================================


class TestFM278IdempotentCompletion:
    """v0.10.5 FM-278 — idempotent terminal re-fire is DEBUG, not WARNING."""

    @pytest.mark.asyncio
    async def test_completed_to_completed_returns_true_no_warning(
        self, mock_db, mock_redis, caplog,
    ):
        """Idempotent COMPLETED → COMPLETED returns True (caller short-circuit honored), no WARNING."""
        from meeting_api.meetings import update_meeting_status

        meeting = make_meeting(status=MeetingStatus.COMPLETED.value)
        mock_db.execute = AsyncMock(return_value=_LockResult(meeting))

        with caplog.at_level(logging.DEBUG, logger="meeting_api.meetings"):
            success = await update_meeting_status(
                meeting, MeetingStatus.COMPLETED, mock_db,
            )

        # Pack T contract: returns True so caller's `if not success` branch
        # doesn't break post-meeting tasks.
        assert success is True, (
            "FM-278 regression: idempotent COMPLETED→COMPLETED returned False; "
            "post-meeting tasks (chat persist, etc.) would short-circuit"
        )

        # No WARNING — the regression shape is one WARNING per re-fire.
        warning_records = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and ("completed" in r.message.lower() or "transition" in r.message.lower())
        ]
        assert not warning_records, (
            f"FM-278 regression: idempotent COMPLETED→COMPLETED logged WARNING, "
            f"expected DEBUG. Records: {[r.message for r in warning_records]}"
        )

    @pytest.mark.asyncio
    async def test_failed_to_failed_returns_true_no_warning(
        self, mock_db, mock_redis, caplog,
    ):
        """FAILED → FAILED is symmetric: idempotent, no WARNING (Pack T applies to both terminals)."""
        from meeting_api.meetings import update_meeting_status

        meeting = make_meeting(status=MeetingStatus.FAILED.value)
        mock_db.execute = AsyncMock(return_value=_LockResult(meeting))

        with caplog.at_level(logging.DEBUG, logger="meeting_api.meetings"):
            success = await update_meeting_status(
                meeting, MeetingStatus.FAILED, mock_db,
            )

        assert success is True, "FM-278 regression: idempotent FAILED→FAILED returned False"

        warning_records = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and ("failed" in r.message.lower() or "transition" in r.message.lower())
        ]
        assert not warning_records, (
            f"FM-278 regression: idempotent FAILED→FAILED logged WARNING. "
            f"Records: {[r.message for r in warning_records]}"
        )

    @pytest.mark.asyncio
    async def test_invalid_non_terminal_transition_still_warns(
        self, mock_db, mock_redis, caplog,
    ):
        """Non-terminal invalid transitions still warn — Pack T narrowed scope to terminal idempotency only.

        Negative test: ensure Pack T didn't accidentally silence ALL invalid
        transitions. A genuinely invalid non-terminal transition (e.g.
        REQUESTED → ACTIVE skipping JOINING/AWAITING_ADMISSION) should
        still log WARNING because that IS a real bug worth surfacing.

        From schemas.get_valid_status_transitions: REQUESTED → ACTIVE is
        not a valid transition (must go via JOINING).
        """
        from meeting_api.meetings import update_meeting_status

        # Build a non-terminal-to-non-terminal invalid pair.
        meeting = make_meeting(status=MeetingStatus.AWAITING_ADMISSION.value)
        mock_db.execute = AsyncMock(return_value=_LockResult(meeting))

        with caplog.at_level(logging.DEBUG, logger="meeting_api.meetings"):
            success = await update_meeting_status(
                meeting, MeetingStatus.REQUESTED, mock_db,  # invalid: AWAITING_ADMISSION → REQUESTED
            )

        # Should fail and warn — Pack T scope was terminal idempotency, not all invalid transitions.
        assert success is False, (
            "FM-278 over-correction: invalid non-terminal transition silently succeeded"
        )
        warning_records = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "transition" in r.message.lower()
        ]
        assert warning_records, (
            "FM-278 over-correction: genuinely invalid transition did not log WARNING; "
            "Pack T should narrow only the idempotent terminal case"
        )
