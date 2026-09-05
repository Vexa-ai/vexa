"""Language-neutral service-authority.v1 values at the Python boundary."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Optional
from urllib.parse import urlparse


_log = logging.getLogger("meeting_api.service_authority.models")

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


# The deciding service owns the WORDS. This module carries them; it never authors them and never
# reads them: no vocabulary here, no plan names, no URLs, nothing that has to change when a
# deployment changes what it sells. A refusal a caller cannot act on is the defect these two
# optional fields close — the reason code says WHICH gate closed, the message says what a person
# can DO about it, and only the deployment that decided knows the second one.
MESSAGE_MAX_CHARS = 512
ACTION_URL_MAX_CHARS = 2048


def clean_message(value: Any) -> Optional[str]:
    """A refusal message that may be shown to a caller, or None.

    Sanitising rather than rejecting is deliberate: a malformed courtesy field must never turn a
    decidable 403 into an opaque 503. The decision is the load-bearing part and the words are an
    improvement on it, so a bad one is dropped and the refusal still stands.
    """
    if not isinstance(value, str):
        return None
    # Control characters (newlines included) are stripped rather than escaped: this string is
    # inlined into an error line an agent reads, and a smuggled newline could forge a second line.
    text = "".join(ch for ch in value if ch.isprintable()).strip()
    if not text:
        return None
    return text[:MESSAGE_MAX_CHARS]


def clean_action_url(value: Any) -> Optional[str]:
    """An https URL a caller can open, or None.

    https only — never a scheme that executes in the reader (``javascript:``) or carries its own
    payload (``data:``). A non-https value is dropped, never rewritten.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > ACTION_URL_MAX_CHARS:
        return None
    if any(not ch.isprintable() for ch in text):
        return None
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return text


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
    # Optional, and OPTIONAL IS LOAD-BEARING: a deciding service that starts sending these must not
    # break a deployment that has not been rebuilt. Before they were listed here the strict
    # unknown-field check rejected the whole response, so a decision carrying plain words for the
    # caller became "authority unavailable" — a 503 in place of an actionable 403.
    "message",
    "action_url",
})
# THIS SET IS NOW THE CONTRACT'S CENSUS, NOT A GATE. `from_wire` ignores what it does not find here
# (see its docstring); only `from_wire(..., strict=True)` — the contract test — refuses. Adding a
# name here still teaches meeting-api to READ that field; leaving one out no longer costs the
# customer their meeting.

# Present-but-empty is the same as absent everywhere below, so the required set is spelled once.
_OPTIONAL_DECISION_KEYS = frozenset({"stop_scope", "message", "action_url"})


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
    # What a caller can read and act on. Authored by the deciding service, carried verbatim (after
    # sanitising) and never interpreted here.
    message: Optional[str] = None
    action_url: Optional[str] = None

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
        for field_name in ("message", "action_url"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise ValueError(
                    f"service-authority {field_name} must be a string when present"
                )
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
        strict: bool = False,
    ) -> "ServiceAuthorityDecision":
        """Parse one wire decision. UNKNOWN KEYS ARE IGNORED; every known key is still validated.

        WHY TOLERANT AT THE RUNTIME DOOR. This used to reject the whole response on any key it did
        not recognise, and that is how a decider adding a field became an OUTAGE: an actionable 403
        arrived, `from_wire` raised, the adapter mapped the raise to `ServiceAuthorityUnavailable`,
        and the caller got a 503. The release that added `message` / `action_url` fixed that
        instance by widening the allow-list by exactly two names — which leaves the CLASS intact and
        recreates the outage on the next optional field anybody adds. A decider and a meeting-api
        are separately deployed; requiring them to ship together is precisely the coupling this
        contract exists to avoid, and the failure is asymmetric — ignoring a field we do not
        understand costs us that field, while refusing the response costs the customer the service.

        ``strict=True`` keeps the old refusal, and it is for the CONTRACT TEST only: that is where
        an unexpected key means "our own fixture drifted from the contract" rather than "the other
        side is newer than us". No production path passes it.
        """
        if not isinstance(value, Mapping):
            raise ValueError("service-authority response must be an object")
        unknown = set(value) - _DECISION_KEYS
        if unknown and strict:
            raise ValueError("service-authority response contains unknown fields")
        if unknown:
            _log.debug(
                "service-authority response carried unknown field(s) %s — ignored; "
                "this meeting-api is older than the deciding service",
                ",".join(sorted(unknown)),
            )
        required = _DECISION_KEYS - _OPTIONAL_DECISION_KEYS
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
            message=clean_message(value.get("message")),
            action_url=clean_action_url(value.get("action_url")),
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

    def caller_fields(self) -> dict[str, Any]:
        """The optional caller-facing half of a decision, with ABSENT fields omitted.

        Omission, not a null: a caller that reads ``message`` off the body must be able to tell
        "this deployment said nothing" from "this deployment said an empty thing", and a null in a
        JSON error body reads as the latter.
        """
        fields: dict[str, Any] = {}
        if self.message is not None:
            fields["message"] = self.message
        if self.action_url is not None:
            fields["action_url"] = self.action_url
        return fields

    def to_record(self) -> dict[str, Any]:
        # Same omit-when-absent rule as the wire: this dict is merged into meeting metadata, and a
        # persisted `"message": null` is indistinguishable from a message that was blanked.
        return {
            **self.caller_fields(),
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
