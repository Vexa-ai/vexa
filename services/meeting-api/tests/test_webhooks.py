"""Tests for webhook delivery logic."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import make_meeting, TEST_MEETING_ID, TEST_USER_ID


# ===================================================================
# Webhook helper functions
# ===================================================================


class TestWebhookHelpers:

    def test_resolve_event_type(self):
        """Status → event type mapping.

        Note: "completed" is intentionally NOT mapped to "meeting.completed".
        send_completion_webhook owns the meeting.completed event end-to-end;
        the status path would otherwise double-fire it on the terminal
        transition (see release 260418-webhooks, commit 19cff9d).
        """
        from meeting_api.webhooks import _resolve_event_type

        assert _resolve_event_type("active") == "meeting.started"
        assert _resolve_event_type("failed") == "bot.failed"
        assert _resolve_event_type("joining") == "meeting.status_change"
        assert _resolve_event_type("awaiting_admission") == "meeting.status_change"
        assert _resolve_event_type("stopping") == "meeting.status_change"
        # "completed" falls through to the meeting.status_change fallback;
        # the canonical meeting.completed comes via send_completion_webhook.
        assert _resolve_event_type("completed") == "meeting.status_change"

    def test_is_event_enabled_defaults(self):
        """Default: only meeting.completed is enabled."""
        from meeting_api.webhooks import _is_event_enabled

        assert _is_event_enabled(None, "meeting.completed") is True
        assert _is_event_enabled(None, "meeting.started") is False
        assert _is_event_enabled({}, "meeting.completed") is True

    def test_is_event_enabled_custom_config(self):
        """User can enable/disable events via webhook_events config in meeting.data."""
        from meeting_api.webhooks import _is_event_enabled

        meeting_data = {
            "webhook_events": {
                "meeting.completed": True,
                "meeting.started": True,
                "bot.failed": False,
            }
        }
        assert _is_event_enabled(meeting_data, "meeting.completed") is True
        assert _is_event_enabled(meeting_data, "meeting.started") is True
        assert _is_event_enabled(meeting_data, "bot.failed") is False

    def test_build_meeting_event_data(self):
        """Event data includes frozen meeting fields."""
        from meeting_api.webhooks import _build_meeting_event_data

        now = datetime.utcnow()
        meeting = make_meeting(
            status="completed",
            start_time=now,
            end_time=now,
            constructed_meeting_url="https://meet.google.com/abc",
        )
        # Add native_meeting_id property
        object.__setattr__(meeting, "native_meeting_id", "abc-defg-hij")

        data = _build_meeting_event_data(meeting)
        assert data["id"] == TEST_MEETING_ID
        assert data["user_id"] == TEST_USER_ID
        assert data["platform"] == "google_meet"
        assert data["status"] == "completed"

    def test_get_webhook_config_from_meeting_data(self):
        """Webhook config is read from meeting.data."""
        from meeting_api.webhooks import _get_webhook_config

        meeting = make_meeting(data={
            "webhook_url": "https://example.com/hook",
            "webhook_secret": "secret123",
        })
        url, secret = _get_webhook_config(meeting)
        assert url == "https://example.com/hook"
        assert secret == "secret123"

    def test_get_webhook_config_empty(self):
        """No webhook config → returns None, None."""
        from meeting_api.webhooks import _get_webhook_config

        meeting = make_meeting(data={})
        url, secret = _get_webhook_config(meeting)
        assert url is None
        assert secret is None


# ===================================================================
# v0.10.5 additive promote — completion_reason + failure_stage
# ===================================================================


class TestAdditivePromote:
    """Surface honesty: typed fields hoisted from JSONB data.

    See features/bot-lifecycle/README.md (FM-006) for the full envelope plan.
    This is the 80%-value half-step landing in 0.10.5.
    """

    def test_meeting_response_hoists_completion_reason_from_data(self):
        """Schema construction hoists completion_reason from data → typed field."""
        from meeting_api.schemas import (
            MeetingResponse,
            MeetingCompletionReason,
            MeetingStatus,
        )

        meeting = make_meeting(
            status=MeetingStatus.COMPLETED.value,
            data={"completion_reason": "stopped", "failure_stage": None},
        )
        # native_meeting_id is needed for MeetingResponse construction
        object.__setattr__(meeting, "native_meeting_id", "abc-defg-hij")

        resp = MeetingResponse.model_validate(meeting)
        assert resp.completion_reason == MeetingCompletionReason.STOPPED
        assert resp.failure_stage is None

    def test_meeting_response_hoists_failure_stage_for_failed_meeting(self):
        """Schema construction hoists both completion_reason + failure_stage from data."""
        from meeting_api.schemas import (
            MeetingResponse,
            MeetingCompletionReason,
            MeetingFailureStage,
            MeetingStatus,
        )

        meeting = make_meeting(
            status=MeetingStatus.FAILED.value,
            data={
                "completion_reason": "stopped_before_admission",
                "failure_stage": "joining",
            },
        )
        object.__setattr__(meeting, "native_meeting_id", "abc-defg-hij")

        resp = MeetingResponse.model_validate(meeting)
        assert resp.completion_reason == MeetingCompletionReason.STOPPED_BEFORE_ADMISSION
        assert resp.failure_stage == MeetingFailureStage.JOINING

    def test_webhook_payload_includes_completion_reason_and_failure_stage(self):
        """Webhook event data includes hoisted fields; data still ships intact."""
        from meeting_api.webhooks import _build_meeting_event_data

        now = datetime.utcnow()
        meeting = make_meeting(
            status="completed",
            start_time=now,
            end_time=now,
            data={"completion_reason": "evicted", "failure_stage": None, "extra": "kept"},
        )
        object.__setattr__(meeting, "native_meeting_id", "abc-defg-hij")

        result = _build_meeting_event_data(meeting)
        assert result["completion_reason"] == "evicted"
        assert result["failure_stage"] is None
        # Back-compat: data blob still ships intact (additive, not replacement)
        assert result["data"]["completion_reason"] == "evicted"
        assert result["data"]["extra"] == "kept"


# ===================================================================
# send_completion_webhook
# ===================================================================


class TestSendCompletionWebhook:

    @pytest.mark.asyncio
    async def test_no_webhook_url_skips(self):
        """Meeting with no webhook_url in data → skip webhook."""
        from meeting_api.webhooks import send_completion_webhook

        meeting = make_meeting(data={})
        db = AsyncMock()
        await send_completion_webhook(meeting, db)
        # Should not raise, just return

    @pytest.mark.asyncio
    async def test_webhook_calls_deliver(self):
        """Webhook with valid URL in meeting.data → calls deliver()."""
        from meeting_api.webhooks import send_completion_webhook

        now = datetime.utcnow()
        meeting = make_meeting(
            status="completed",
            start_time=now,
            end_time=now,
            data={"webhook_url": "https://example.com/webhook"},
        )
        object.__setattr__(meeting, "native_meeting_id", "abc-defg-hij")
        object.__setattr__(meeting, "constructed_meeting_url", "https://meet.google.com/abc")

        db = AsyncMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("meeting_api.webhooks.deliver", new_callable=AsyncMock, return_value=mock_resp):
            with patch("meeting_api.webhooks.validate_webhook_url"):
                with patch("meeting_api.webhooks.build_envelope", return_value={"event": "test"}):
                    await send_completion_webhook(meeting, db)


# ===================================================================
# Webhook HMAC signing
# ===================================================================


class TestWebhookSigning:

    @pytest.mark.asyncio
    async def test_webhook_with_secret_uses_hmac(self):
        """When meeting.data has webhook_secret, deliver is called with it."""
        from meeting_api.webhooks import send_completion_webhook

        now = datetime.utcnow()
        meeting = make_meeting(
            status="completed",
            start_time=now,
            end_time=now,
            data={
                "webhook_url": "https://example.com/webhook",
                "webhook_secret": "my-secret-key",
            },
        )
        object.__setattr__(meeting, "native_meeting_id", "abc-defg-hij")
        object.__setattr__(meeting, "constructed_meeting_url", None)

        db = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("meeting_api.webhooks.deliver", new_callable=AsyncMock, return_value=mock_resp) as mock_deliver:
            with patch("meeting_api.webhooks.validate_webhook_url"):
                with patch("meeting_api.webhooks.build_envelope", return_value={"event": "test"}):
                    await send_completion_webhook(meeting, db)

        mock_deliver.assert_called_once()
        call_kwargs = mock_deliver.call_args[1]
        assert call_kwargs["webhook_secret"] == "my-secret-key"

    @pytest.mark.asyncio
    async def test_webhook_without_secret_no_signature(self):
        """When no webhook_secret in meeting.data, deliver is called with None secret."""
        from meeting_api.webhooks import send_completion_webhook

        now = datetime.utcnow()
        meeting = make_meeting(
            status="completed",
            start_time=now,
            end_time=now,
            data={"webhook_url": "https://example.com/webhook"},
        )
        object.__setattr__(meeting, "native_meeting_id", "abc-defg-hij")
        object.__setattr__(meeting, "constructed_meeting_url", None)

        db = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("meeting_api.webhooks.deliver", new_callable=AsyncMock, return_value=mock_resp) as mock_deliver:
            with patch("meeting_api.webhooks.validate_webhook_url"):
                with patch("meeting_api.webhooks.build_envelope", return_value={"event": "test"}):
                    await send_completion_webhook(meeting, db)

        call_kwargs = mock_deliver.call_args[1]
        assert call_kwargs["webhook_secret"] is None


# ===================================================================
# send_status_webhook
# ===================================================================


class TestSendStatusWebhook:

    @pytest.mark.asyncio
    async def test_status_webhook_calls_deliver_for_enabled_event(self):
        """Status webhook with enabled event type → calls deliver()."""
        from meeting_api.webhooks import send_status_webhook

        now = datetime.utcnow()
        meeting = make_meeting(
            status="active",
            start_time=now,
            data={
                "webhook_url": "https://example.com/webhook",
                "webhook_secret": "my-secret",
                "webhook_events": {"meeting.started": True},
            },
        )
        object.__setattr__(meeting, "native_meeting_id", "abc-defg-hij")
        object.__setattr__(meeting, "constructed_meeting_url", None)

        db = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("meeting_api.webhooks.deliver", new_callable=AsyncMock, return_value=mock_resp) as mock_deliver:
            with patch("meeting_api.webhooks.validate_webhook_url"):
                with patch("meeting_api.webhooks.build_envelope", return_value={"event": "test"}):
                    await send_status_webhook(meeting, db)

        mock_deliver.assert_called_once()
        call_kwargs = mock_deliver.call_args[1]
        assert call_kwargs["webhook_secret"] == "my-secret"

    @pytest.mark.asyncio
    async def test_status_webhook_skips_disabled_event(self):
        """Status webhook with disabled event type → does NOT call deliver()."""
        from meeting_api.webhooks import send_status_webhook

        meeting = make_meeting(
            status="active",
            data={
                "webhook_url": "https://example.com/webhook",
                # No webhook_events config → defaults to only meeting.completed
            },
        )
        object.__setattr__(meeting, "native_meeting_id", "abc-defg-hij")
        object.__setattr__(meeting, "constructed_meeting_url", None)

        db = AsyncMock()

        with patch("meeting_api.webhooks.deliver", new_callable=AsyncMock) as mock_deliver:
            await send_status_webhook(meeting, db)

        mock_deliver.assert_not_called()

    @pytest.mark.asyncio
    async def test_status_webhook_no_url_skips(self):
        """Status webhook with no webhook_url → skip."""
        from meeting_api.webhooks import send_status_webhook

        meeting = make_meeting(data={})
        db = AsyncMock()
        await send_status_webhook(meeting, db)
        # Should not raise
