"""Tests for durable webhook delivery with Redis-backed retry queue."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from shared_models.webhook_delivery import (
    RETRY_QUEUE_KEY,
    _enqueue_failed_webhook,
    deliver,
    set_redis_client,
    get_redis_client,
)
from shared_models.webhook_retry_worker import (
    BACKOFF_SCHEDULE,
    MAX_AGE_SECONDS,
    _deliver_one,
    _process_queue,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeRedis:
    """Minimal in-memory fake that implements the Redis list API used by
    the webhook retry queue (rpush, lpop, llen)."""

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
        """Helper: parse all items currently in the retry queue."""
        return [json.loads(raw) for raw in self._data.get(RETRY_QUEUE_KEY, [])]


def _make_ok_response() -> httpx.Response:
    return httpx.Response(200, request=httpx.Request("POST", "http://x"))


def _make_500_response() -> httpx.Response:
    resp = httpx.Response(500, request=httpx.Request("POST", "http://x"))
    return resp


# ---------------------------------------------------------------------------
# Test: successful delivery never touches Redis
# ---------------------------------------------------------------------------

class TestSuccessfulDelivery:
    @pytest.mark.asyncio
    async def test_no_redis_enqueue_on_success(self):
        """When delivery succeeds on the first try, nothing is enqueued."""
        fake_redis = FakeRedis()

        with patch("shared_models.webhook_delivery.with_retry") as mock_retry:
            mock_retry.return_value = _make_ok_response()

            resp = await deliver(
                url="http://example.com/hook",
                payload={"event": "test"},
                redis_client=fake_redis,
            )

        assert resp is not None
        assert resp.status_code == 200
        assert await fake_redis.llen(RETRY_QUEUE_KEY) == 0


# ---------------------------------------------------------------------------
# Test: delivery fails -> lands in Redis
# ---------------------------------------------------------------------------

class TestFailedDeliveryEnqueues:
    @pytest.mark.asyncio
    async def test_enqueue_on_failure_explicit_client(self):
        """When all in-memory retries fail and redis_client is passed
        explicitly, the webhook is persisted to the retry queue."""
        fake_redis = FakeRedis()

        with patch("shared_models.webhook_delivery.with_retry") as mock_retry:
            mock_retry.side_effect = httpx.ConnectError("connection refused")

            resp = await deliver(
                url="http://down.example.com/hook",
                payload={"event": "meeting.completed"},
                label="test-hook",
                redis_client=fake_redis,
            )

        assert resp is None
        items = fake_redis.queue_items()
        assert len(items) == 1
        entry = items[0]
        assert entry["url"] == "http://down.example.com/hook"
        assert entry["payload"] == {"event": "meeting.completed"}
        assert entry["label"] == "test-hook"
        assert entry["attempt"] == 0
        assert entry["created_at"] > 0
        assert entry["next_retry_at"] > entry["created_at"]

    @pytest.mark.asyncio
    async def test_enqueue_on_failure_module_level_client(self):
        """When the module-level Redis client is set, deliver() uses it
        automatically even without an explicit redis_client argument."""
        fake_redis = FakeRedis()
        old = get_redis_client()
        try:
            set_redis_client(fake_redis)

            with patch("shared_models.webhook_delivery.with_retry") as mock_retry:
                mock_retry.side_effect = httpx.ConnectError("refused")

                resp = await deliver(
                    url="http://down.example.com/hook",
                    payload={"event": "test"},
                )

            assert resp is None
            assert await fake_redis.llen(RETRY_QUEUE_KEY) == 1
        finally:
            set_redis_client(old)

    @pytest.mark.asyncio
    async def test_no_enqueue_without_redis(self):
        """Without any Redis client, failed deliveries are silently dropped
        (backward-compatible behavior)."""
        old = get_redis_client()
        try:
            set_redis_client(None)

            with patch("shared_models.webhook_delivery.with_retry") as mock_retry:
                mock_retry.side_effect = httpx.ConnectError("refused")

                resp = await deliver(
                    url="http://down.example.com/hook",
                    payload={"event": "test"},
                )

            assert resp is None
            # No crash, no Redis interaction — just dropped
        finally:
            set_redis_client(old)


# ---------------------------------------------------------------------------
# Test: worker retries -> delivered
# ---------------------------------------------------------------------------

class TestWorkerRetry:
    @pytest.mark.asyncio
    async def test_worker_delivers_queued_webhook(self):
        """Webhook lands in Redis, worker picks it up and delivers it."""
        fake_redis = FakeRedis()
        now = time.time()

        entry = {
            "url": "http://example.com/hook",
            "payload": {"event": "test"},
            "headers": {"Content-Type": "application/json"},
            "webhook_secret": None,
            "label": "test",
            "attempt": 0,
            "next_retry_at": now - 1,  # already due
            "created_at": now,
        }
        await fake_redis.rpush(RETRY_QUEUE_KEY, json.dumps(entry))

        with patch("shared_models.webhook_retry_worker._deliver_one") as mock_deliver:
            mock_deliver.return_value = True  # success
            processed = await _process_queue(fake_redis)

        assert processed == 1
        assert await fake_redis.llen(RETRY_QUEUE_KEY) == 0
        mock_deliver.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_requeues_on_failure(self):
        """When the worker fails to deliver, it re-enqueues with bumped
        attempt count and backoff."""
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

        with patch("shared_models.webhook_retry_worker._deliver_one") as mock_deliver:
            mock_deliver.return_value = False  # failure
            processed = await _process_queue(fake_redis)

        assert processed == 1
        # Should be re-enqueued with attempt=1
        items = fake_redis.queue_items()
        assert len(items) == 1
        assert items[0]["attempt"] == 1
        assert items[0]["next_retry_at"] >= now + BACKOFF_SCHEDULE[0] - 1

    @pytest.mark.asyncio
    async def test_worker_skips_not_ready(self):
        """Entries whose next_retry_at is in the future are left in the queue."""
        fake_redis = FakeRedis()
        now = time.time()

        entry = {
            "url": "http://example.com/hook",
            "payload": {"event": "test"},
            "headers": {"Content-Type": "application/json"},
            "webhook_secret": None,
            "label": "test",
            "attempt": 0,
            "next_retry_at": now + 9999,  # far in the future
            "created_at": now,
        }
        await fake_redis.rpush(RETRY_QUEUE_KEY, json.dumps(entry))

        with patch("shared_models.webhook_retry_worker._deliver_one") as mock_deliver:
            processed = await _process_queue(fake_redis)

        assert processed == 0
        mock_deliver.assert_not_called()
        # Entry still in queue
        assert await fake_redis.llen(RETRY_QUEUE_KEY) == 1


# ---------------------------------------------------------------------------
# Test: max age exceeded -> dropped
# ---------------------------------------------------------------------------

class TestMaxAgeDrop:
    @pytest.mark.asyncio
    async def test_expired_entry_dropped(self):
        """Entries older than MAX_AGE_SECONDS are dropped without delivery."""
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
            "created_at": now - MAX_AGE_SECONDS - 100,  # expired
        }
        await fake_redis.rpush(RETRY_QUEUE_KEY, json.dumps(entry))

        with patch("shared_models.webhook_retry_worker._deliver_one") as mock_deliver:
            processed = await _process_queue(fake_redis)

        assert processed == 1
        mock_deliver.assert_not_called()  # should NOT attempt delivery
        assert await fake_redis.llen(RETRY_QUEUE_KEY) == 0


# ---------------------------------------------------------------------------
# Test: _deliver_one HTTP behavior
# ---------------------------------------------------------------------------

class TestDeliverOne:
    @pytest.mark.asyncio
    async def test_success(self):
        entry = {
            "url": "http://example.com/hook",
            "payload": {"event": "test"},
            "webhook_secret": None,
            "label": "test",
        }
        mock_response = httpx.Response(200, request=httpx.Request("POST", "http://x"))
        with patch("shared_models.webhook_retry_worker.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.return_value = mock_response
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await _deliver_one(entry)

        assert result is True

    @pytest.mark.asyncio
    async def test_server_error_returns_false(self):
        entry = {
            "url": "http://example.com/hook",
            "payload": {"event": "test"},
            "webhook_secret": None,
            "label": "test",
        }
        mock_response = httpx.Response(503, request=httpx.Request("POST", "http://x"))
        with patch("shared_models.webhook_retry_worker.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.return_value = mock_response
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await _deliver_one(entry)

        assert result is False

    @pytest.mark.asyncio
    async def test_client_error_returns_true(self):
        """4xx errors (except 429) are permanent — don't retry."""
        entry = {
            "url": "http://example.com/hook",
            "payload": {"event": "test"},
            "webhook_secret": None,
            "label": "test",
        }
        mock_response = httpx.Response(404, request=httpx.Request("POST", "http://x"))
        with patch("shared_models.webhook_retry_worker.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.return_value = mock_response
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await _deliver_one(entry)

        assert result is True  # permanent failure = don't re-enqueue

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self):
        entry = {
            "url": "http://example.com/hook",
            "payload": {"event": "test"},
            "webhook_secret": None,
            "label": "test",
        }
        with patch("shared_models.webhook_retry_worker.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.side_effect = httpx.TimeoutException("timed out")
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await _deliver_one(entry)

        assert result is False


# ---------------------------------------------------------------------------
# Test: end-to-end flow — fail, enqueue, worker delivers
# ---------------------------------------------------------------------------

class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_full_flow(self):
        """deliver() fails -> enqueued to Redis -> worker picks up and delivers."""
        fake_redis = FakeRedis()

        # Step 1: deliver() fails all in-memory retries
        with patch("shared_models.webhook_delivery.with_retry") as mock_retry:
            mock_retry.side_effect = httpx.ConnectError("refused")

            resp = await deliver(
                url="http://target.example.com/hook",
                payload={"event": "meeting.completed", "id": 42},
                webhook_secret="s3cret",
                label="e2e-test",
                redis_client=fake_redis,
            )
        assert resp is None
        assert await fake_redis.llen(RETRY_QUEUE_KEY) == 1

        # Step 2: Make the entry ready for retry (set next_retry_at to past)
        raw = await fake_redis.lpop(RETRY_QUEUE_KEY)
        entry = json.loads(raw)
        entry["next_retry_at"] = time.time() - 1
        await fake_redis.rpush(RETRY_QUEUE_KEY, json.dumps(entry))

        # Step 3: Worker processes the queue — this time delivery succeeds
        with patch("shared_models.webhook_retry_worker._deliver_one") as mock_deliver:
            mock_deliver.return_value = True
            processed = await _process_queue(fake_redis)

        assert processed == 1
        assert await fake_redis.llen(RETRY_QUEUE_KEY) == 0
        # Verify the correct entry was delivered
        delivered_entry = mock_deliver.call_args[0][0]
        assert delivered_entry["url"] == "http://target.example.com/hook"
        assert delivered_entry["payload"]["id"] == 42
