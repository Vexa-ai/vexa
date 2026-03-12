"""
Webhook delivery with exponential backoff and HMAC signing.

Provides reliable delivery for both:
- Internal hooks (billing, analytics) via POST_MEETING_HOOKS env var
- Per-client webhooks via user-configured webhook_url + webhook_secret

When a Redis client is configured (via ``set_redis_client()`` or the
``redis_client`` parameter), failed deliveries are persisted to a
Redis-backed retry queue for durable delivery by the background worker
(see webhook_retry_worker.py).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, Optional

import httpx

from .retry import with_retry

logger = logging.getLogger(__name__)

RETRY_QUEUE_KEY = "webhook:retry_queue"

# Module-level Redis client — set once at startup via set_redis_client().
_redis_client: Any = None


def set_redis_client(client: Any) -> None:
    """Set the module-level Redis client for durable webhook delivery.

    Call this once at application startup (e.g. in bot-manager's
    startup_event) so that all ``deliver()`` calls automatically get
    Redis-backed retry without needing to pass the client explicitly.
    """
    global _redis_client
    _redis_client = client
    logger.info("Webhook delivery: Redis client configured for durable retry")


def get_redis_client() -> Any:
    """Return the module-level Redis client (may be None)."""
    return _redis_client


def sign_payload(payload_bytes: bytes, secret: str) -> str:
    """Create HMAC-SHA256 signature for webhook payload.

    Returns: "sha256=<hex digest>"
    """
    mac = hmac.new(secret.encode(), payload_bytes, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def build_headers(
    webhook_secret: Optional[str] = None,
    payload_bytes: Optional[bytes] = None,
) -> Dict[str, str]:
    """Build webhook request headers.

    If webhook_secret is provided:
    - Sets Authorization: Bearer <secret> (backward compat)
    - Sets X-Webhook-Signature: sha256=<hmac> (new, verifiable)
    - Sets X-Webhook-Timestamp for replay protection
    """
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if webhook_secret and webhook_secret.strip():
        secret = webhook_secret.strip()
        headers["Authorization"] = f"Bearer {secret}"
        if payload_bytes:
            ts = str(int(time.time()))
            # Sign timestamp + payload to prevent replay attacks
            signed_content = f"{ts}.".encode() + payload_bytes
            sig = hmac.new(secret.encode(), signed_content, hashlib.sha256).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={sig}"
            headers["X-Webhook-Timestamp"] = ts
    return headers


async def _enqueue_failed_webhook(
    redis: Any,
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    webhook_secret: Optional[str],
    label: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Persist a failed webhook to the Redis retry queue.

    Returns True if enqueued successfully, False otherwise.
    """
    now = time.time()
    entry = {
        "url": url,
        "payload": payload,
        "headers": headers,
        "webhook_secret": webhook_secret,
        "label": label,
        "attempt": 0,
        "next_retry_at": now + 60,  # first retry in 1 minute
        "created_at": now,
    }
    if metadata:
        entry["metadata"] = metadata
    try:
        await redis.rpush(RETRY_QUEUE_KEY, json.dumps(entry))
        logger.info(f"Enqueued failed webhook for durable retry: {url} [{label}]")
        return True
    except Exception as e:
        logger.error(f"Failed to enqueue webhook to Redis: {e}")
        return False


async def deliver(
    url: str,
    payload: Dict[str, Any],
    webhook_secret: Optional[str] = None,
    timeout: float = 30.0,
    max_retries: int = 3,
    label: str = "",
    redis_client: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[httpx.Response]:
    """Deliver a webhook with exponential backoff retry.

    Args:
        url: Target URL.
        payload: JSON payload dict.
        webhook_secret: Optional HMAC signing secret.
        timeout: Request timeout in seconds.
        max_retries: Number of retry attempts.
        label: Label for log messages.
        redis_client: Optional async Redis client override. When not
            provided, falls back to the module-level client set via
            ``set_redis_client()``. If neither is available, failed
            deliveries are dropped (current behavior).
        metadata: Optional dict of caller context (e.g. meeting_id).
            Passed through to the retry queue so the worker can update
            the originating record on eventual success/failure.

    Returns:
        The response on success, None on total failure.
    """
    payload_bytes = json.dumps(payload).encode()
    headers = build_headers(webhook_secret, payload_bytes)

    async def _send() -> httpx.Response:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(url, content=payload_bytes, headers=headers, timeout=timeout)
            if resp.status_code >= 500 or resp.status_code == 429:
                resp.raise_for_status()
            return resp

    try:
        resp = await with_retry(_send, max_retries=max_retries, label=label or f"webhook {url}")
        if resp.status_code < 300:
            logger.info(f"Webhook delivered to {url}: {resp.status_code}")
        else:
            logger.warning(f"Webhook {url} returned {resp.status_code}: {resp.text[:200]}")
        return resp
    except Exception as e:
        logger.error(f"Webhook delivery failed after retries for {url}: {e}")
        # Persist to Redis retry queue if a client is available
        effective_redis = redis_client if redis_client is not None else _redis_client
        if effective_redis is not None:
            await _enqueue_failed_webhook(
                effective_redis, url, payload, headers, webhook_secret, label,
                metadata=metadata,
            )
        return None
