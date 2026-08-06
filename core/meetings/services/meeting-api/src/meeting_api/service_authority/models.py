"""Language-neutral service-authority.v1 values at the Python boundary."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Optional


AUTHORITY_VERSION = "service-authority.v1"
LIFECYCLE_CONTRACT_VERSION = "2026-07-28"
Action = Literal["admit", "continue"]
TranscriptionProvider = Literal["vexa", "customer", "none"]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("service-authority timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _wire_time(value: datetime) -> str:
    return _utc(value).isoformat()


def parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    return _utc(parsed)


@dataclass(frozen=True)
class ServiceAuthorityRequest:
    user_id: int
    action: Action
    request_id: str
    service_identity: str
    service_mode: Literal["bot"]
    transcription_provider: TranscriptionProvider
    lifecycle_contract_version: str
    active_concurrency: int
    admitted_at: Optional[datetime] = None
    boundary_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.user_id, bool)
            or not isinstance(self.user_id, int)
            or self.user_id <= 0
        ):
            raise ValueError("user_id must be a positive integer")
        if self.action not in ("admit", "continue"):
            raise ValueError("unsupported service-authority action")
        if (
            not isinstance(self.request_id, str)
            or not self.request_id.strip()
            or not isinstance(self.service_identity, str)
            or not self.service_identity.strip()
        ):
            raise ValueError("service-authority request identity is required")
        if self.service_mode != "bot":
            raise ValueError("unsupported service mode")
        if self.transcription_provider not in ("vexa", "customer", "none"):
            raise ValueError("unsupported transcription provider")
        if self.lifecycle_contract_version != LIFECYCLE_CONTRACT_VERSION:
            raise ValueError("unsupported lifecycle contract version")
        if (
            isinstance(self.active_concurrency, bool)
            or not isinstance(self.active_concurrency, int)
            or self.active_concurrency < 0
        ):
            raise ValueError("active_concurrency must be a non-negative integer")
        if self.action == "admit" and (
            self.admitted_at is not None or self.boundary_at is not None
        ):
            raise ValueError("admission cannot carry active-service timestamps")
        if self.action == "continue" and (
            self.admitted_at is None or self.boundary_at is None
        ):
            raise ValueError("continuation requires admitted_at and boundary_at")
        if self.admitted_at is not None:
            _utc(self.admitted_at)
        if self.boundary_at is not None:
            _utc(self.boundary_at)
        if (
            self.admitted_at is not None
            and self.boundary_at is not None
            and self.boundary_at < self.admitted_at
        ):
            raise ValueError("service boundary precedes admission")

    @classmethod
    def admit(
        cls,
        *,
        user_id: int,
        request_id: str,
        service_identity: str,
        transcription_provider: TranscriptionProvider,
        active_concurrency: int,
    ) -> "ServiceAuthorityRequest":
        return cls(
            user_id=user_id,
            action="admit",
            request_id=request_id,
            service_identity=service_identity,
            service_mode="bot",
            transcription_provider=transcription_provider,
            lifecycle_contract_version=LIFECYCLE_CONTRACT_VERSION,
            active_concurrency=active_concurrency,
        )

    @classmethod
    def continuation(
        cls,
        *,
        user_id: int,
        request_id: str,
        service_identity: str,
        transcription_provider: TranscriptionProvider,
        active_concurrency: int,
        admitted_at: datetime,
        boundary_at: datetime,
    ) -> "ServiceAuthorityRequest":
        return cls(
            user_id=user_id,
            action="continue",
            request_id=request_id,
            service_identity=service_identity,
            service_mode="bot",
            transcription_provider=transcription_provider,
            lifecycle_contract_version=LIFECYCLE_CONTRACT_VERSION,
            active_concurrency=active_concurrency,
            admitted_at=admitted_at,
            boundary_at=boundary_at,
        )

    def to_wire(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "user_id": self.user_id,
            "action": self.action,
            "request_id": self.request_id,
            "service_identity": self.service_identity,
            "service_mode": self.service_mode,
            "transcription_provider": self.transcription_provider,
            "lifecycle_contract_version": self.lifecycle_contract_version,
            "active_concurrency": self.active_concurrency,
        }
        if self.action == "continue":
            body["admitted_at"] = _wire_time(self.admitted_at)  # type: ignore[arg-type]
            body["boundary_at"] = _wire_time(self.boundary_at)  # type: ignore[arg-type]
        return body

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_wire(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


_DECISION_KEYS = frozenset({
    "authority_version",
    "decision_id",
    "request_id",
    "service_identity",
    "allow",
    "reason",
    "decided_at",
    "stop_scope",
})


@dataclass(frozen=True)
class ServiceAuthorityDecision:
    authority_version: str
    decision_id: str
    request_id: str
    service_identity: str
    allow: bool
    reason: str
    decided_at: datetime
    stop_scope: Optional[Literal["billable_service"]] = None
    enforced: bool = True

    def __post_init__(self) -> None:
        if self.authority_version != AUTHORITY_VERSION:
            raise ValueError("unsupported service-authority response version")
        identity_fields = (
            self.decision_id,
            self.request_id,
            self.service_identity,
            self.reason,
        )
        if any(
            not isinstance(value, str) or not value.strip()
            for value in identity_fields
        ):
            raise ValueError("service-authority decision identity is incomplete")
        if not isinstance(self.allow, bool):
            raise ValueError("service-authority allow must be boolean")
        if self.stop_scope not in (None, "billable_service"):
            raise ValueError("unsupported service-authority stop scope")
        if self.allow and self.stop_scope is not None:
            raise ValueError("an allowed decision cannot carry a stop scope")
        _utc(self.decided_at)

    @classmethod
    def from_wire(
        cls,
        value: Any,
        *,
        request: ServiceAuthorityRequest,
        now: datetime,
        max_age_seconds: float,
        enforced: bool = True,
    ) -> "ServiceAuthorityDecision":
        if not isinstance(value, Mapping):
            raise ValueError("service-authority response must be an object")
        if set(value) - _DECISION_KEYS:
            raise ValueError("service-authority response contains unknown fields")
        required = _DECISION_KEYS - {"stop_scope"}
        if any(key not in value for key in required):
            raise ValueError("service-authority response is incomplete")
        decided_at = parse_time(value["decided_at"], "decided_at")
        observed = _utc(now)
        if abs((observed - decided_at).total_seconds()) > max_age_seconds:
            raise ValueError("service-authority response is stale")
        decision = cls(
            authority_version=value["authority_version"],
            decision_id=value["decision_id"],
            request_id=value["request_id"],
            service_identity=value["service_identity"],
            allow=value["allow"],
            reason=value["reason"],
            decided_at=decided_at,
            stop_scope=value.get("stop_scope"),
            enforced=enforced,
        )
        if (
            decision.request_id != request.request_id
            or decision.service_identity != request.service_identity
        ):
            raise ValueError(
                "service-authority response is bound to another request"
            )
        if request.action == "admit" and decision.stop_scope is not None:
            raise ValueError(
                "an admission decision cannot carry a stop scope"
            )
        if (
            request.action == "continue"
            and not decision.allow
            and decision.stop_scope != "billable_service"
        ):
            raise ValueError(
                "a denied continuation must stop billable service"
            )
        return decision

    def to_record(self) -> dict[str, Any]:
        return {
            "authority_version": self.authority_version,
            "decision_id": self.decision_id,
            "request_id": self.request_id,
            "service_identity": self.service_identity,
            "allow": self.allow,
            "reason": self.reason,
            "decided_at": _wire_time(self.decided_at),
            "stop_scope": self.stop_scope,
            "enforced": self.enforced,
        }
