"""Boot-frozen operator callback for terminal service facts.

Customer webhook destinations are untrusted input and remain behind the SSRF
guard. This adapter is configured only by the deployment operator, freezes one
destination at boot and may explicitly opt into private HTTP for an in-cluster
service. Meeting/user payloads never select or alter that destination.
"""
from __future__ import annotations

import math
import os
import re
from ipaddress import ip_address
from typing import Any, Optional
from urllib.parse import urlsplit

from .delivery import DeliveryResult, Transport, _deliver_signed
from .retry import RetryQueue, drain_retry_queue

SYSTEM_RETRY_QUEUE_KEY = "webhook:system:retry_queue"
SYSTEM_PROCESSING_KEY = "webhook:system:retry_processing"
SYSTEM_DEAD_LETTER_KEY = "webhook:system:dead_letter"
_TERMINAL_EVENTS = frozenset({"meeting.completed", "bot.failed"})
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_private_http_target(hostname: str) -> bool:
    """Return whether plaintext HTTP is explicitly limited to a local target."""
    host = hostname.lower()
    try:
        address = ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        return address.is_private or address.is_loopback or address.is_link_local

    if host == "localhost" or host.endswith(".localhost"):
        return True

    labels = host.split(".")
    if not all(_DNS_LABEL.fullmatch(label) for label in labels):
        return False
    if len(labels) == 1:
        return True
    return host.endswith(".svc") or host.endswith(".svc.cluster.local")


class SystemWebhookSink:
    """Signed delivery to one deployment-owned terminal callback."""

    configured = True

    def __init__(
        self,
        *,
        url: str,
        secret: str,
        transport: Transport,
        redis: Any = None,
        allow_private_http: bool = False,
    ):
        parsed = urlsplit(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("system webhook URL must be absolute HTTP(S)")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "system webhook URL cannot contain credentials, query or fragment",
            )
        if parsed.scheme == "http":
            if not allow_private_http:
                raise ValueError(
                    "system webhook private HTTP requires explicit operator opt-in",
                )
            if not _is_private_http_target(parsed.hostname):
                raise ValueError(
                    "system webhook HTTP target must be loopback, private IP or "
                    "an explicit in-cluster service name",
                )
        if not secret.strip():
            raise ValueError("system webhook secret is required")

        self.url = url.strip()
        self.secret = secret.strip()
        self.transport = transport
        self.queue = (
            RetryQueue(redis, key=SYSTEM_RETRY_QUEUE_KEY)
            if redis is not None
            else None
        )
        self._redis = redis

    async def deliver(
        self,
        envelope: dict,
        *,
        label: str = "",
    ) -> DeliveryResult:
        if envelope.get("event_type") not in _TERMINAL_EVENTS:
            return DeliveryResult(status="suppressed")
        return await _deliver_signed(
            transport=self.transport,
            queue=self.queue,
            url=self.url,
            envelope=envelope,
            webhook_secret=self.secret,
            label=label,
            metadata={"scope": "operator-system"},
        )

    async def retry_depth(self) -> int:
        if self.queue is None:
            return 0
        return await self.queue.depth()

    async def drain(self, *, now: Optional[float] = None) -> int:
        if self._redis is None:
            return 0
        return await drain_retry_queue(
            self._redis,
            self.transport,
            now=now,
            key=SYSTEM_RETRY_QUEUE_KEY,
            processing_key=SYSTEM_PROCESSING_KEY,
            dead_letter_key=SYSTEM_DEAD_LETTER_KEY,
        )


def build_system_webhook_from_env(
    redis: Any,
    *,
    transport: Transport | None = None,
) -> SystemWebhookSink | None:
    """Build the optional operator callback from deployment config."""
    url = (os.getenv("VEXA_SYSTEM_WEBHOOK_URL") or "").strip()
    secret = (os.getenv("VEXA_SYSTEM_WEBHOOK_SECRET") or "").strip()
    if not url and not secret:
        return None
    if not url or not secret:
        raise RuntimeError(
            "VEXA_SYSTEM_WEBHOOK_URL and VEXA_SYSTEM_WEBHOOK_SECRET "
            "must be configured together",
        )

    try:
        timeout = float(os.getenv("VEXA_SYSTEM_WEBHOOK_TIMEOUT_S", "10"))
    except ValueError as exc:
        raise RuntimeError(
            "VEXA_SYSTEM_WEBHOOK_TIMEOUT_S must be a finite number greater "
            "than 0 and at most 60",
        ) from exc
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 60:
        raise RuntimeError(
            "VEXA_SYSTEM_WEBHOOK_TIMEOUT_S must be a finite number greater "
            "than 0 and at most 60",
        )

    if transport is None:
        import httpx

        async def transport(url: str, body: bytes, headers: dict):
            async with httpx.AsyncClient(timeout=timeout) as client:
                return await client.post(url, content=body, headers=headers)

    return SystemWebhookSink(
        url=url,
        secret=secret,
        transport=transport,
        redis=redis,
        allow_private_http=_truthy(
            os.getenv("VEXA_SYSTEM_WEBHOOK_ALLOW_PRIVATE_HTTP"),
        ),
    )
