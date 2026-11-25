"""Boot-frozen operator callbacks for terminal service facts.

Customer webhook destinations are untrusted input and remain behind the SSRF
guard. This adapter is configured only by the deployment operator, freezes its
destinations at boot and may explicitly opt into private HTTP for an in-cluster
service. Meeting/user payloads never select or alter a destination.

There is one legacy target (``VEXA_SYSTEM_WEBHOOK_URL`` + ``_SECRET``, unchanged
keys, queues and wire bytes) and an optional list of further named targets
(``VEXA_SYSTEM_WEBHOOK_TARGETS`` + ``VEXA_SYSTEM_WEBHOOK_TARGET_SECRETS``). Every
target has its own secret, its own timeout, and its own retry, processing and
dead-letter lists, and targets are delivered and drained concurrently, so one
failing or slow target never delays or drops delivery to another.
"""
from __future__ import annotations

import asyncio
import json
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
DEFAULT_TARGET_NAME = "default"
_TARGET_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
_TARGET_KEYS = frozenset({"name", "url", "allow_private_http", "timeout_s"})


def system_queue_keys(name: str = DEFAULT_TARGET_NAME) -> tuple[str, str, str]:
    """Return ``(retry, processing, dead_letter)`` Redis keys for one target.

    The legacy target keeps today's keys exactly, so entries already queued on
    a running deployment are drained by the same code after an upgrade.
    """
    if name == DEFAULT_TARGET_NAME:
        return (
            SYSTEM_RETRY_QUEUE_KEY,
            SYSTEM_PROCESSING_KEY,
            SYSTEM_DEAD_LETTER_KEY,
        )
    return (
        f"webhook:system:{name}:retry_queue",
        f"webhook:system:{name}:retry_processing",
        f"webhook:system:{name}:dead_letter",
    )


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
        name: str = DEFAULT_TARGET_NAME,
    ):
        if name != DEFAULT_TARGET_NAME and not _TARGET_NAME.fullmatch(name):
            raise ValueError(
                "system webhook target name must match [a-z][a-z0-9-]{0,31}",
            )
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

        self.name = name
        self.url = url.strip()
        self.secret = secret.strip()
        self.transport = transport
        (
            self.retry_key,
            self.processing_key,
            self.dead_letter_key,
        ) = system_queue_keys(name)
        self.queue = (
            RetryQueue(redis, key=self.retry_key)
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
            key=self.retry_key,
            processing_key=self.processing_key,
            dead_letter_key=self.dead_letter_key,
        )


class SystemWebhookFanout:
    """Several independent operator targets behind the one sink interface.

    ``deliver`` returns one ``(target name, DeliveryResult)`` pair per target.
    Targets run concurrently and each failure is contained to its own pair:
    a target that raises, times out or queues never changes what another
    target receives, when it receives it, or which queue it retries from.
    """

    configured = True

    def __init__(self, sinks: list[SystemWebhookSink]):
        if not sinks:
            raise ValueError("system webhook fanout needs at least one target")
        names = [sink.name for sink in sinks]
        if len(set(names)) != len(names):
            raise ValueError("system webhook target names must be unique")
        self.sinks = tuple(sinks)

    async def _deliver_one(
        self,
        sink: SystemWebhookSink,
        envelope: dict,
        label: str,
    ) -> tuple[str, DeliveryResult]:
        try:
            return sink.name, await sink.deliver(envelope, label=label)
        except Exception as exc:  # noqa: BLE001 — contain one target's fault
            return sink.name, DeliveryResult(
                status="failed",
                error=type(exc).__name__,
            )

    async def deliver(
        self,
        envelope: dict,
        *,
        label: str = "",
    ) -> list[tuple[str, DeliveryResult]]:
        return list(
            await asyncio.gather(
                *(self._deliver_one(sink, envelope, label) for sink in self.sinks),
            ),
        )

    async def retry_depth(self) -> int:
        depths = await asyncio.gather(*(sink.retry_depth() for sink in self.sinks))
        return sum(depths)

    async def drain(self, *, now: Optional[float] = None) -> int:
        results = await asyncio.gather(
            *(sink.drain(now=now) for sink in self.sinks),
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, BaseException)]
        processed = sum(r for r in results if not isinstance(r, BaseException))
        if errors:
            # Every other target has drained already; surface the first fault
            # to the worker's guard so it is logged, not swallowed.
            raise errors[0]
        return processed


_TIMEOUT_ERROR = "must be a finite number greater than 0 and at most 60"


def _timeout(value: Any, field: str) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{field} {_TIMEOUT_ERROR}") from exc
    if isinstance(value, bool) or not math.isfinite(timeout) or timeout <= 0 or timeout > 60:
        raise RuntimeError(f"{field} {_TIMEOUT_ERROR}")
    return timeout


def _httpx_transport(timeout: float) -> Transport:
    import httpx

    async def transport(url: str, body: bytes, headers: dict):
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, content=body, headers=headers)

    return transport


def _extra_targets_from_env() -> list[dict]:
    """Parse the named targets and their secrets. Errors never echo a secret."""
    raw_targets = (os.getenv("VEXA_SYSTEM_WEBHOOK_TARGETS") or "").strip()
    raw_secrets = (os.getenv("VEXA_SYSTEM_WEBHOOK_TARGET_SECRETS") or "").strip()
    if not raw_targets and not raw_secrets:
        return []
    if not raw_targets or not raw_secrets:
        raise RuntimeError(
            "VEXA_SYSTEM_WEBHOOK_TARGETS and VEXA_SYSTEM_WEBHOOK_TARGET_SECRETS "
            "must be configured together",
        )
    try:
        targets = json.loads(raw_targets)
    except ValueError as exc:
        raise RuntimeError(
            "VEXA_SYSTEM_WEBHOOK_TARGETS must be a JSON list of targets",
        ) from exc
    try:
        secrets = json.loads(raw_secrets)
    except ValueError:
        # `from None`: the decoder's message quotes the input, which is secret.
        raise RuntimeError(
            "VEXA_SYSTEM_WEBHOOK_TARGET_SECRETS must be a JSON object of "
            "target name to secret",
        ) from None
    if not isinstance(targets, list) or not targets:
        raise RuntimeError(
            "VEXA_SYSTEM_WEBHOOK_TARGETS must be a non-empty JSON list",
        )
    if not isinstance(secrets, dict) or not all(
        isinstance(value, str) and value.strip() for value in secrets.values()
    ):
        raise RuntimeError(
            "VEXA_SYSTEM_WEBHOOK_TARGET_SECRETS must map each target name to "
            "a non-empty secret",
        )

    parsed: list[dict] = []
    for target in targets:
        if not isinstance(target, dict):
            raise RuntimeError("each system webhook target must be an object")
        unknown = set(target) - _TARGET_KEYS
        if unknown:
            raise RuntimeError(
                "system webhook target has unknown field(s): "
                + ",".join(sorted(unknown)),
            )
        name = target.get("name")
        if not isinstance(name, str) or not _TARGET_NAME.fullmatch(name):
            raise RuntimeError(
                "system webhook target name must match [a-z][a-z0-9-]{0,31}",
            )
        if name == DEFAULT_TARGET_NAME:
            raise RuntimeError(
                "system webhook target name 'default' is reserved for "
                "VEXA_SYSTEM_WEBHOOK_URL",
            )
        url = target.get("url")
        if not isinstance(url, str) or not url.strip():
            raise RuntimeError(f"system webhook target {name} needs a url")
        allow_private_http = target.get("allow_private_http", False)
        if not isinstance(allow_private_http, bool):
            raise RuntimeError(
                f"system webhook target {name} allow_private_http must be boolean",
            )
        timeout = target.get("timeout_s")
        parsed.append({
            "name": name,
            "url": url,
            "allow_private_http": allow_private_http,
            "timeout_s": (
                None
                if timeout is None
                else _timeout(timeout, f"system webhook target {name} timeout_s")
            ),
        })

    names = [target["name"] for target in parsed]
    if len(set(names)) != len(names):
        raise RuntimeError("system webhook target names must be unique")
    if set(secrets) != set(names):
        raise RuntimeError(
            "VEXA_SYSTEM_WEBHOOK_TARGET_SECRETS must name exactly the "
            "targets in VEXA_SYSTEM_WEBHOOK_TARGETS",
        )
    for target in parsed:
        target["secret"] = secrets[target["name"]].strip()
    return parsed


def build_system_webhook_from_env(
    redis: Any,
    *,
    transport: Transport | None = None,
) -> SystemWebhookSink | SystemWebhookFanout | None:
    """Build the optional operator callback(s) from deployment config.

    With only the legacy pair this returns the same single ``SystemWebhookSink``
    as before. With named targets it returns a ``SystemWebhookFanout`` whose
    first member is the legacy target when that is configured.
    """
    url = (os.getenv("VEXA_SYSTEM_WEBHOOK_URL") or "").strip()
    secret = (os.getenv("VEXA_SYSTEM_WEBHOOK_SECRET") or "").strip()
    if bool(url) != bool(secret):
        raise RuntimeError(
            "VEXA_SYSTEM_WEBHOOK_URL and VEXA_SYSTEM_WEBHOOK_SECRET "
            "must be configured together",
        )
    extra = _extra_targets_from_env()
    if not url and not extra:
        return None
    timeout = _timeout(
        os.getenv("VEXA_SYSTEM_WEBHOOK_TIMEOUT_S", "10"),
        "VEXA_SYSTEM_WEBHOOK_TIMEOUT_S",
    )

    used_secrets = [secret] if secret else []
    for target in extra:
        if target["secret"] in used_secrets:
            raise RuntimeError(
                f"system webhook target {target['name']} must have its own "
                "secret, not one shared with another target",
            )
        used_secrets.append(target["secret"])

    sinks: list[SystemWebhookSink] = []
    if url:
        sinks.append(
            SystemWebhookSink(
                url=url,
                secret=secret,
                transport=transport or _httpx_transport(timeout),
                redis=redis,
                allow_private_http=_truthy(
                    os.getenv("VEXA_SYSTEM_WEBHOOK_ALLOW_PRIVATE_HTTP"),
                ),
            ),
        )
    for target in extra:
        sinks.append(
            SystemWebhookSink(
                name=target["name"],
                url=target["url"],
                secret=target["secret"],
                transport=transport
                or _httpx_transport(target["timeout_s"] or timeout),
                redis=redis,
                allow_private_http=target["allow_private_http"],
            ),
        )
    if not extra:
        return sinks[0]
    return SystemWebhookFanout(sinks)
