"""Tests for webhook delivery history — metadata pass-through and worker meeting updates.

Covers:
- deliver() passing metadata through to retry queue entries
- Retry worker updating meeting records on terminal outcomes
- _update_meeting_delivery_status DB integration
"""
from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from shared_models.webhook_delivery import (
    RETRY_QUEUE_KEY,
    deliver,
    set_redis_client,
    get_redis_client,
)
from shared_models.webhook_retry_worker import (
    BACKOFF_SCHEDULE,
    MAX_AGE_SECONDS,
    _process_queue,
    _update_meeting_delivery_status,
    set_session_factory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeRedis:
    def __init__(self):
        self._data: Dict[str, List[str]] = {}

    async def rpush(self, key: str, *values: str) -> int:
        lst = self._data.setdefault(key, [])
        lst.extend(values)
        return len(lst)

    async def lpop(self, key: str) -> Optional[str]:
        lst = self._data.get(key, [])
        if not lst:
            return None
        return lst.pop(0)

    async def llen(self, key: str) -> int:
        return len(self._data.get(key, []))

    def queue_items(self) -> List[dict]:
        return [json.loads(raw) for raw in self._data.get(RETRY_QUEUE_KEY, [])]


# ---------------------------------------------------------------------------
# Test: deliver() passes metadata through to retry queue
# ---------------------------------------------------------------------------

class TestDeliverMetadata:
    @pytest.mark.asyncio
    async def test_metadata_in_queue_entry(self):
        """When deliver() fails, metadata (including meeting_id) is stored in the queue entry."""
        fake_redis = FakeRedis()

        with patch("shared_models.webhook_delivery.with_retry") as mock_retry:
            mock_retry.side_effect = httpx.ConnectError("refused")

            await deliver(
                url="http://down.example.com/hook",
                payload={"event": "test"},
                label="meta-test",
                redis_client=fake_redis,
                metadata={"meeting_id": 42},
            )

        items = fake_redis.queue_items()
        assert len(items) == 1
        assert items[0]["metadata"] == {"meeting_id": 42}

    @pytest.mark.asyncio
    async def test_no_metadata_key_when_none(self):
        """When metadata is None, no metadata key should be in the queue entry."""
        fake_redis = FakeRedis()

        with patch("shared_models.webhook_delivery.with_retry") as mock_retry:
            mock_retry.side_effect = httpx.ConnectError("refused")

            await deliver(
                url="http://down.example.com/hook",
                payload={"event": "test"},
                redis_client=fake_redis,
            )

        items = fake_redis.queue_items()
        assert len(items) == 1
        assert "metadata" not in items[0]

    @pytest.mark.asyncio
    async def test_metadata_does_not_break_success_path(self):
        """Passing metadata should not affect successful delivery."""
        fake_redis = FakeRedis()

        with patch("shared_models.webhook_delivery.with_retry") as mock_retry:
            mock_retry.return_value = httpx.Response(200, request=httpx.Request("POST", "http://x"))

            resp = await deliver(
                url="http://example.com/hook",
                payload={"event": "test"},
                redis_client=fake_redis,
                metadata={"meeting_id": 42},
            )

        assert resp is not None
        assert resp.status_code == 200
        assert await fake_redis.llen(RETRY_QUEUE_KEY) == 0


# ---------------------------------------------------------------------------
# Test: retry worker updates meeting on success
# ---------------------------------------------------------------------------

class TestWorkerUpdatesMeeting:
    @pytest.mark.asyncio
    async def test_worker_writes_delivered_on_success(self):
        """When the retry worker delivers a webhook with meeting_id, it updates the meeting."""
        fake_redis = FakeRedis()
        now = time.time()

        entry = {
            "url": "http://example.com/hook",
            "payload": {"event": "test"},
            "headers": {"Content-Type": "application/json"},
            "webhook_secret": None,
            "label": "test",
            "attempt": 1,
            "next_retry_at": now - 1,
            "created_at": now,
            "metadata": {"meeting_id": 99},
        }
        await fake_redis.rpush(RETRY_QUEUE_KEY, json.dumps(entry))

        with patch("shared_models.webhook_retry_worker._deliver_one") as mock_deliver, \
             patch("shared_models.webhook_retry_worker._update_meeting_delivery_status") as mock_update:
            mock_deliver.return_value = True
            await _process_queue(fake_redis)

        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][0] == 99  # meeting_id
        status = call_args[0][1]
        assert status["status"] == "delivered"
        assert status["attempts"] == 2  # attempt 1 + this delivery

    @pytest.mark.asyncio
    async def test_worker_writes_failed_on_expired(self):
        """When a queued webhook expires (MAX_AGE), meeting record is updated as failed."""
        fake_redis = FakeRedis()
        now = time.time()

        entry = {
            "url": "http://example.com/hook",
            "payload": {"event": "test"},
            "headers": {"Content-Type": "application/json"},
            "webhook_secret": None,
            "label": "expired-test",
            "attempt": 3,
            "next_retry_at": now - 1,
            "created_at": now - MAX_AGE_SECONDS - 100,
            "metadata": {"meeting_id": 77},
        }
        await fake_redis.rpush(RETRY_QUEUE_KEY, json.dumps(entry))

        with patch("shared_models.webhook_retry_worker._deliver_one") as mock_deliver, \
             patch("shared_models.webhook_retry_worker._update_meeting_delivery_status") as mock_update:
            await _process_queue(fake_redis)

        mock_deliver.assert_not_called()
        mock_update.assert_called_once()
        status = mock_update.call_args[0][1]
        assert status["status"] == "failed"
        assert "Expired" in status["error"]

    @pytest.mark.asyncio
    async def test_worker_no_update_without_meeting_id(self):
        """Queue entries without metadata.meeting_id should not trigger meeting updates."""
        fake_redis = FakeRedis()
        now = time.time()

        entry = {
            "url": "http://example.com/hook",
            "payload": {"event": "test"},
            "headers": {"Content-Type": "application/json"},
            "webhook_secret": None,
            "label": "test",
            "attempt": 0,
            "next_retry_at": now - 1,
            "created_at": now,
        }
        await fake_redis.rpush(RETRY_QUEUE_KEY, json.dumps(entry))

        with patch("shared_models.webhook_retry_worker._deliver_one") as mock_deliver, \
             patch("shared_models.webhook_retry_worker._update_meeting_delivery_status") as mock_update:
            mock_deliver.return_value = True
            await _process_queue(fake_redis)

        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_worker_writes_failed_on_exhausted_retries(self):
        """When all retry attempts are exhausted, meeting is marked as failed."""
        fake_redis = FakeRedis()
        now = time.time()

        entry = {
            "url": "http://example.com/hook",
            "payload": {"event": "test"},
            "headers": {"Content-Type": "application/json"},
            "webhook_secret": None,
            "label": "test",
            "attempt": len(BACKOFF_SCHEDULE),  # exhausted
            "next_retry_at": now - 1,
            "created_at": now,
            "metadata": {"meeting_id": 55},
        }
        await fake_redis.rpush(RETRY_QUEUE_KEY, json.dumps(entry))

        with patch("shared_models.webhook_retry_worker._deliver_one") as mock_deliver, \
             patch("shared_models.webhook_retry_worker._update_meeting_delivery_status") as mock_update:
            mock_deliver.return_value = False
            await _process_queue(fake_redis)

        mock_update.assert_called_once()
        status = mock_update.call_args[0][1]
        assert status["status"] == "failed"
        assert "Exhausted" in status["error"]
        # Should NOT be re-enqueued
        assert await fake_redis.llen(RETRY_QUEUE_KEY) == 0


# ---------------------------------------------------------------------------
# Test: _update_meeting_delivery_status
# ---------------------------------------------------------------------------

class TestUpdateMeetingDeliveryStatus:
    @pytest.mark.asyncio
    async def test_updates_meeting_data_preserving_existing_keys(self):
        """_update_meeting_delivery_status merges into meeting.data correctly."""
        fake_meeting = MagicMock()
        fake_meeting.data = {"transcribe_enabled": True, "some_other_key": "value"}

        fake_session = AsyncMock()
        fake_session.get = AsyncMock(return_value=fake_meeting)

        @asynccontextmanager
        async def fake_factory():
            yield fake_session

        try:
            set_session_factory(fake_factory)

            await _update_meeting_delivery_status(99, {
                "url": "http://example.com/hook",
                "status": "delivered",
                "delivered_at": "2025-01-01T12:00:00+00:00",
            })

            assert fake_meeting.data["webhook_delivery"]["status"] == "delivered"
            assert fake_meeting.data["transcribe_enabled"] is True
            assert fake_meeting.data["some_other_key"] == "value"
            fake_session.commit.assert_called_once()
        finally:
            set_session_factory(None)

    @pytest.mark.asyncio
    async def test_skips_when_no_session_factory(self):
        """When no session factory is configured, update is skipped gracefully."""
        try:
            set_session_factory(None)
            # Should not raise
            await _update_meeting_delivery_status(99, {"status": "delivered"})
        finally:
            set_session_factory(None)

    @pytest.mark.asyncio
    async def test_skips_when_meeting_not_found(self):
        """When meeting doesn't exist in DB, update is skipped."""
        fake_session = AsyncMock()
        fake_session.get = AsyncMock(return_value=None)

        @asynccontextmanager
        async def fake_factory():
            yield fake_session

        try:
            set_session_factory(fake_factory)
            # Should not raise
            await _update_meeting_delivery_status(999, {"status": "delivered"})
            fake_session.commit.assert_not_called()
        finally:
            set_session_factory(None)


# ---------------------------------------------------------------------------
# Test: end-to-end with metadata
# ---------------------------------------------------------------------------

class TestEndToEndWithMetadata:
    @pytest.mark.asyncio
    async def test_metadata_flows_from_deliver_to_worker(self):
        """deliver() fails -> metadata stored in queue -> worker reads meeting_id."""
        fake_redis = FakeRedis()

        # Step 1: deliver() fails with metadata
        with patch("shared_models.webhook_delivery.with_retry") as mock_retry:
            mock_retry.side_effect = httpx.ConnectError("refused")
            await deliver(
                url="http://target.example.com/hook",
                payload={"event": "meeting.completed", "id": 42},
                label="e2e-meta-test",
                redis_client=fake_redis,
                metadata={"meeting_id": 42},
            )

        # Step 2: Make entry ready
        raw = await fake_redis.lpop(RETRY_QUEUE_KEY)
        entry = json.loads(raw)
        entry["next_retry_at"] = time.time() - 1
        await fake_redis.rpush(RETRY_QUEUE_KEY, json.dumps(entry))

        # Step 3: Worker processes — succeeds
        with patch("shared_models.webhook_retry_worker._deliver_one") as mock_deliver, \
             patch("shared_models.webhook_retry_worker._update_meeting_delivery_status") as mock_update:
            mock_deliver.return_value = True
            await _process_queue(fake_redis)

        # Verify meeting was updated with correct meeting_id
        mock_update.assert_called_once()
        assert mock_update.call_args[0][0] == 42
        assert mock_update.call_args[0][1]["status"] == "delivered"
