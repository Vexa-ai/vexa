"""
Webhook delivery with exponential backoff and HMAC signing.

Provides reliable delivery for both:
- Internal hooks (billing, analytics) via POST_MEETING_HOOKS env var
- Per-client webhooks via user-configured webhook_url + webhook_secret
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


async def deliver(
    url: str,
    payload: Dict[str, Any],
    webhook_secret: Optional[str] = None,
    timeout: float = 30.0,
    max_retries: int = 3,
    label: str = "",
) -> Optional[httpx.Response]:
    """Deliver a webhook with exponential backoff retry.

    Args:
        url: Target URL.
        payload: JSON payload dict.
        webhook_secret: Optional HMAC signing secret.
        timeout: Request timeout in seconds.
        max_retries: Number of retry attempts.
        label: Label for log messages.

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
        return None
