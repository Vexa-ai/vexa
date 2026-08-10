"""Allow-all and signed HTTP adapters for service-authority.v1."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Mapping, Optional
from urllib.parse import urlparse

import httpx

from .models import (
    AUTHORITY_VERSION,
    ServiceAuthorityDecision,
    ServiceAuthorityRequest,
)
from .ports import ServiceAuthorityUnavailable


def _safe_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("service-authority URL is required")
    try:
        parsed = urlparse(value)
        host = parsed.hostname
    except ValueError as exc:
        raise ValueError("service-authority URL is invalid") from exc
    if (
        not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "service-authority URL must be a credential-free endpoint URL"
        )
    if parsed.scheme == "https":
        return value
    if parsed.scheme != "http":
        raise ValueError("service-authority URL must use https")
    lowered = host.lower()
    loopback = lowered == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        pass
    in_cluster = (
        "." not in lowered
        or lowered.endswith(".svc")
        or lowered.endswith(".svc.cluster.local")
    )
    if not (loopback or in_cluster):
        raise ValueError(
            "plain HTTP service-authority URL must be loopback or in-cluster"
        )
    return value


@dataclass(frozen=True)
class ServiceAuthorityConfig:
    url: str
    secret: str
    timeout_seconds: float
    response_max_age_seconds: float
    mode: str
    contract_version: str = AUTHORITY_VERSION
    failure_policy: str = "closed"

    def __post_init__(self) -> None:
        _safe_url(self.url)
        if not isinstance(self.secret, str) or not self.secret.strip():
            raise ValueError("service-authority secret is required")
        if self.contract_version != AUTHORITY_VERSION:
            raise ValueError("unsupported service-authority contract_version")
        if self.failure_policy != "closed":
            raise ValueError(
                "service-authority failure_policy must be closed"
            )
        if self.mode not in ("enforce", "observe"):
            raise ValueError(
                "service-authority mode must be enforce or observe"
            )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not (0 < self.timeout_seconds <= 30)
        ):
            raise ValueError(
                "service-authority timeout must be within 0..30 seconds"
            )
        if (
            isinstance(self.response_max_age_seconds, bool)
            or not isinstance(self.response_max_age_seconds, (int, float))
            or not (0 < self.response_max_age_seconds <= 300)
        ):
            raise ValueError(
                "service-authority response age must be within 0..300 seconds"
            )


class AllowAllServiceAuthority:
    """Explicit OSS default: no external authority is configured."""

    configured = False
    mode = "unconfigured"

    async def decide(
        self,
        request: ServiceAuthorityRequest,
    ) -> ServiceAuthorityDecision:
        identity = hashlib.sha256(request.to_json_bytes()).hexdigest()
        return ServiceAuthorityDecision(
            authority_version=AUTHORITY_VERSION,
            decision_id=f"service-authority:unconfigured:{identity}",
            request_id=request.request_id,
            service_identity=request.service_identity,
            allow=True,
            reason="unconfigured",
            decided_at=datetime.now(timezone.utc),
            enforced=False,
        )


class HttpServiceAuthority:
    """Timestamp-sign exact request bytes and parse a request-bound decision."""

    configured = True

    def __init__(
        self,
        config: ServiceAuthorityConfig,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.config = config
        self.mode = config.mode
        self._client = client

    async def decide(
        self,
        request: ServiceAuthorityRequest,
    ) -> ServiceAuthorityDecision:
        body = request.to_json_bytes()
        timestamp = str(int(time.time()))
        digest = hmac.new(
            self.config.secret.encode("utf-8"),
            timestamp.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "Authorization": f"Bearer {self.config.secret}",
            "Content-Type": "application/json",
            "X-Webhook-Signature": f"sha256={digest}",
            "X-Webhook-Timestamp": timestamp,
        }
        try:
            if self._client is None:
                async with httpx.AsyncClient(
                    timeout=self.config.timeout_seconds,
                ) as client:
                    response = await client.post(
                        self.config.url,
                        content=body,
                        headers=headers,
                    )
            else:
                response = await self._client.post(
                    self.config.url,
                    content=body,
                    headers=headers,
                    timeout=self.config.timeout_seconds,
                )
            if response.status_code < 200 or response.status_code >= 300:
                raise ValueError(
                    f"authority returned HTTP {response.status_code}"
                )
            decision = ServiceAuthorityDecision.from_wire(
                response.json(),
                request=request,
                now=datetime.now(timezone.utc),
                max_age_seconds=self.config.response_max_age_seconds,
                enforced=self.config.mode == "enforce",
            )
            return decision
        except ServiceAuthorityUnavailable:
            raise
        except Exception as exc:
            raise ServiceAuthorityUnavailable(
                "configured service authority is unavailable"
            ) from exc


def build_service_authority_from_env(
    env: Optional[Mapping[str, str]] = None,
) -> AllowAllServiceAuthority | HttpServiceAuthority:
    values = os.environ if env is None else env
    raw = (values.get("VEXA_SERVICE_AUTHORITY_CONFIG") or "").strip()
    if not raw:
        return AllowAllServiceAuthority()
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "VEXA_SERVICE_AUTHORITY_CONFIG must be valid JSON"
        ) from exc
    if not isinstance(body, dict):
        raise ValueError(
            "VEXA_SERVICE_AUTHORITY_CONFIG must be a JSON object"
        )
    allowed = {
        "url",
        "contract_version",
        "timeout_ms",
        "response_max_age_seconds",
        "failure_policy",
        "mode",
    }
    if set(body) - allowed:
        raise ValueError(
            "VEXA_SERVICE_AUTHORITY_CONFIG contains unknown fields"
        )
    secret = (values.get("VEXA_SERVICE_AUTHORITY_SECRET") or "").strip()
    timeout_ms = body.get("timeout_ms", 2_000)
    if (
        isinstance(timeout_ms, bool)
        or not isinstance(timeout_ms, (int, float))
    ):
        raise ValueError(
            "service-authority timeout_ms must be numeric"
        )
    config = ServiceAuthorityConfig(
        url=body.get("url"),
        secret=secret,
        timeout_seconds=timeout_ms / 1_000,
        response_max_age_seconds=body.get(
            "response_max_age_seconds",
            30,
        ),
        mode=body.get("mode", "enforce"),
        contract_version=body.get(
            "contract_version",
            AUTHORITY_VERSION,
        ),
        failure_policy=body.get("failure_policy", "closed"),
    )
    return HttpServiceAuthority(config)
