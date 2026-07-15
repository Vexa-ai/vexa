"""Fail-closed ZAKI capture authorization and spawn composition."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import hmac
import json
from typing import Any, Optional, Protocol

from ..bot_spawn import (
    MaxBotsExceeded,
    MeetingRepo,
    QuotaExceeded,
    RuntimeClient,
    UnsafeMeetingUrl,
    request_bot,
    validate_meeting_url,
)
from ..retention import ScopeExpiries, materialize_scope_expiries
from ..lifecycle.stop import leave_command_channel, leave_command_payload
from ..obs import log_event


ZAKI_NOTETAKER_NAME = "ZAKI Notetaker"
MAX_AUTHORITY_LIFETIME = timedelta(minutes=5)


class CaptureDenial(str, Enum):
    """Stable, content-free reasons why capture did not start."""

    OPERATOR_DISABLED = "operator_disabled"
    OPERATOR_POLICY_INVALID = "operator_policy_invalid"
    TENANT_DISABLED = "tenant_disabled"
    TENANT_ATTESTATION_REQUIRED = "tenant_attestation_required"
    TENANT_POLICY_INVALID = "tenant_policy_invalid"
    USER_NOT_REQUESTED = "user_not_requested"
    USER_REQUEST_INVALID = "user_request_invalid"
    QUOTA_EXHAUSTED = "quota_exhausted"
    QUOTA_POLICY_INVALID = "quota_policy_invalid"
    AUTHORITY_SCOPE_MISMATCH = "authority_scope_mismatch"
    AUTHORITY_EXPIRED = "authority_expired"
    RETENTION_POLICY_INVALID = "retention_policy_invalid"
    MEETING_URL_INVALID = "meeting_url_invalid"


class CaptureDenied(Exception):
    """Capture did not start; the exception contains no meeting or participant data."""

    def __init__(self, code: CaptureDenial):
        self.code = code
        super().__init__(code.value)


class CaptureStopPublisher(Protocol):
    """The internal leave-command publisher used after withdrawal is durable."""

    async def publish(self, channel: str, message: str) -> Any:
        ...


@dataclass(frozen=True)
class CaptureAuthority:
    """The independently stored inputs to the effective capture decision.

    Optional annotations are intentional: deserializers can represent missing fields, and this
    boundary rejects them rather than allowing Python truthiness to invent an enabling default.
    """

    operator_enabled: Optional[bool]
    tenant_enabled: Optional[bool]
    tenant_attested: Optional[bool]
    tenant_policy_version: Optional[str]
    tenant_attested_at: Optional[datetime]
    user_requested: Optional[bool]
    quota_permitted: Optional[bool]
    subject_user_id: Optional[int]
    tenant_id: Optional[str]
    meeting_platform: Optional[str]
    native_meeting_id: Optional[str]
    authorized_at: Optional[datetime]
    valid_until: Optional[datetime]
    scope_expiries: Optional[ScopeExpiries]
    meeting_url_sha256: Optional[str] = None


def _require_strict_bool(value: object, invalid: CaptureDenial) -> bool:
    if type(value) is not bool:
        raise CaptureDenied(invalid)
    return value


def _capture_evidence(
    authority: CaptureAuthority,
    *,
    tenant_id: Optional[str],
    user_id: int,
    platform: str,
    native_meeting_id: str,
    evaluated_at: datetime,
) -> dict:
    if (
        isinstance(authority.subject_user_id, bool)
        or not isinstance(authority.subject_user_id, int)
        or authority.subject_user_id <= 0
        or authority.subject_user_id != user_id
    ):
        raise CaptureDenied(CaptureDenial.AUTHORITY_SCOPE_MISMATCH)
    if (
        not isinstance(tenant_id, str)
        or not tenant_id.strip()
        or len(tenant_id) > 128
        or any(ord(character) < 32 for character in tenant_id)
        or authority.tenant_id != tenant_id
    ):
        raise CaptureDenied(CaptureDenial.AUTHORITY_SCOPE_MISMATCH)
    if (
        not isinstance(platform, str)
        or not platform
        or authority.meeting_platform != platform
    ):
        raise CaptureDenied(CaptureDenial.AUTHORITY_SCOPE_MISMATCH)
    if (
        not isinstance(native_meeting_id, str)
        or not native_meeting_id
        or authority.native_meeting_id != native_meeting_id
    ):
        raise CaptureDenied(CaptureDenial.AUTHORITY_SCOPE_MISMATCH)
    if (
        not isinstance(evaluated_at, datetime)
        or evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
        or not isinstance(authority.authorized_at, datetime)
        or authority.authorized_at.tzinfo is None
        or authority.authorized_at.utcoffset() is None
        or not isinstance(authority.valid_until, datetime)
        or authority.valid_until.tzinfo is None
        or authority.valid_until.utcoffset() is None
        or not authority.authorized_at <= evaluated_at < authority.valid_until
        or not timedelta(0) < authority.valid_until - authority.authorized_at <= MAX_AUTHORITY_LIFETIME
    ):
        raise CaptureDenied(CaptureDenial.AUTHORITY_EXPIRED)
    operator_enabled = _require_strict_bool(
        authority.operator_enabled, CaptureDenial.OPERATOR_POLICY_INVALID
    )
    if not operator_enabled:
        raise CaptureDenied(CaptureDenial.OPERATOR_DISABLED)

    tenant_enabled = _require_strict_bool(
        authority.tenant_enabled, CaptureDenial.TENANT_POLICY_INVALID
    )
    if not tenant_enabled:
        raise CaptureDenied(CaptureDenial.TENANT_DISABLED)

    tenant_attested = _require_strict_bool(
        authority.tenant_attested, CaptureDenial.TENANT_POLICY_INVALID
    )
    if not tenant_attested:
        raise CaptureDenied(CaptureDenial.TENANT_ATTESTATION_REQUIRED)

    policy_version = authority.tenant_policy_version
    if (
        not isinstance(policy_version, str)
        or not policy_version.strip()
        or len(policy_version) > 128
        or any(ord(character) < 32 for character in policy_version)
    ):
        raise CaptureDenied(CaptureDenial.TENANT_POLICY_INVALID)
    policy_version = policy_version.strip()

    attested_at = authority.tenant_attested_at
    if (
        not isinstance(attested_at, datetime)
        or attested_at.tzinfo is None
        or attested_at.utcoffset() is None
        or attested_at > authority.authorized_at
    ):
        raise CaptureDenied(CaptureDenial.TENANT_POLICY_INVALID)

    user_requested = _require_strict_bool(
        authority.user_requested, CaptureDenial.USER_REQUEST_INVALID
    )
    if not user_requested:
        raise CaptureDenied(CaptureDenial.USER_NOT_REQUESTED)

    quota_permitted = _require_strict_bool(
        authority.quota_permitted, CaptureDenial.QUOTA_POLICY_INVALID
    )
    if not quota_permitted:
        raise CaptureDenied(CaptureDenial.QUOTA_EXHAUSTED)

    metadata = {
        "zaki_capture": {
            "bot_name": ZAKI_NOTETAKER_NAME,
            "tenant_id": tenant_id,
            "state": "authorized",
            "tenant_attested": True,
            "tenant_policy_version": policy_version,
            "tenant_attested_at": attested_at.isoformat(),
            "user_requested": True,
            "authorized_at": authority.authorized_at.isoformat(),
            "authority_valid_until": authority.valid_until.isoformat(),
        }
    }
    try:
        expiries = materialize_scope_expiries(authority.scope_expiries)
    except (TypeError, ValueError):
        raise CaptureDenied(CaptureDenial.RETENTION_POLICY_INVALID) from None
    if any(
        getattr(expiries, scope) <= evaluated_at
        for scope in ("audio", "transcript", "summary")
    ):
        raise CaptureDenied(CaptureDenial.RETENTION_POLICY_INVALID)
    metadata["zaki_retention"] = {
        "state": "open",
        "scope_expiries": {
            scope: getattr(expiries, scope).isoformat()
            for scope in ("audio", "transcript", "summary")
        },
        "expired_scopes": [],
    }
    return metadata


class _CaptureEvidenceRepo:
    """Narrow MeetingRepo decorator that adds validated evidence to a fresh spawn write."""

    def __init__(self, delegate: MeetingRepo, metadata: dict):
        self._delegate = delegate
        self._metadata = dict(metadata)

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    async def create_meeting_guarded(
        self,
        *,
        user_id: int,
        platform: str,
        native_meeting_id: str,
        data: dict,
        max_concurrent: Optional[int] = None,
        exclude_meeting_id: Optional[int] = None,
    ) -> dict:
        meeting_data = dict(data)
        meeting_data.update(self._metadata)
        return await self._delegate.create_meeting_guarded(
            user_id=user_id,
            platform=platform,
            native_meeting_id=native_meeting_id,
            data=meeting_data,
            max_concurrent=max_concurrent,
            exclude_meeting_id=exclude_meeting_id,
        )

    async def mark_spawn_rejected(
        self, *, meeting_id: int, reason: str, data: Optional[dict] = None
    ) -> Optional[dict]:
        capture = dict(self._metadata["zaki_capture"])
        capture["state"] = "denied"
        capture["denial"] = reason
        patch = dict(data or {})
        patch["zaki_capture"] = capture
        return await self._delegate.mark_spawn_rejected(
            meeting_id=meeting_id,
            reason=reason,
            data=patch,
        )


async def request_capture(
    repo: MeetingRepo,
    runtime: RuntimeClient,
    *,
    authority: CaptureAuthority,
    tenant_id: str,
    user_id: int,
    platform: str,
    native_meeting_id: str,
    passcode: Optional[str] = None,
    meeting_url: Optional[str] = None,
    language: Optional[str] = None,
    task: Optional[str] = None,
    max_concurrent: Optional[int] = None,
    redis_url: Optional[str] = None,
    meeting_api_url: Optional[str] = None,
    internal_secret: Optional[str] = None,
    token_secret: Optional[str] = None,
    evaluated_at: Optional[datetime] = None,
) -> dict:
    """Authorize and start one ZAKI-managed capture through the existing spawn pipeline.

    Authority validation completes before any repository or runtime call. The caller cannot choose
    a less visible bot identity, disable transcription, or supply its own consent evidence.
    """
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    if meeting_url is not None:
        try:
            meeting_url = validate_meeting_url(meeting_url)
        except UnsafeMeetingUrl:
            raise CaptureDenied(CaptureDenial.MEETING_URL_INVALID) from None
        expected_url_hash = authority.meeting_url_sha256
        actual_url_hash = hashlib.sha256(meeting_url.encode()).hexdigest()
        if (
            not isinstance(expected_url_hash, str)
            or len(expected_url_hash) != 64
            or not hmac.compare_digest(expected_url_hash, actual_url_hash)
        ):
            raise CaptureDenied(CaptureDenial.AUTHORITY_SCOPE_MISMATCH)
    elif authority.meeting_url_sha256 is not None:
        raise CaptureDenied(CaptureDenial.AUTHORITY_SCOPE_MISMATCH)
    metadata = _capture_evidence(
        authority,
        tenant_id=tenant_id,
        user_id=user_id,
        platform=platform,
        native_meeting_id=native_meeting_id,
        evaluated_at=evaluated_at,
    )
    capture_repo = _CaptureEvidenceRepo(repo, metadata)
    try:
        return await request_bot(
            capture_repo,
            runtime,
            user_id=user_id,
            platform=platform,
            native_meeting_id=native_meeting_id,
            bot_name=ZAKI_NOTETAKER_NAME,
            passcode=passcode,
            meeting_url=meeting_url,
            language=language,
            task=task,
            recording_enabled=True,
            transcribe_enabled=True,
            continue_meeting=False,
            max_concurrent=max_concurrent,
            redis_url=redis_url,
            meeting_api_url=meeting_api_url,
            internal_secret=internal_secret,
            token_secret=token_secret,
        )
    except (MaxBotsExceeded, QuotaExceeded) as error:
        raise CaptureDenied(CaptureDenial.QUOTA_EXHAUSTED) from error


async def withdraw_capture(
    repo: MeetingRepo,
    publisher: CaptureStopPublisher,
    *,
    tenant_id: str,
    user_id: int,
    platform: str,
    native_meeting_id: str,
    runtime: Optional[RuntimeClient] = None,
    withdrawn_at: Optional[datetime] = None,
) -> dict:
    """Durably withdraw capture, then ask the bot to leave.

    The repository takes the exclusive side of the meeting write barrier before storing the
    withdrawal. In-flight transcript/recording writes drain first; writers starting later observe
    ``zaki_capture.state=withdrawn`` and refuse. The returned receipt is content-free.
    """
    withdrawn_at = withdrawn_at if withdrawn_at is not None else datetime.now(timezone.utc)
    if (
        not isinstance(withdrawn_at, datetime)
        or withdrawn_at.tzinfo is None
        or withdrawn_at.utcoffset() is None
    ):
        raise CaptureDenied(CaptureDenial.USER_REQUEST_INVALID)
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= 0
        or not isinstance(tenant_id, str)
        or not tenant_id.strip()
        or len(tenant_id) > 128
        or any(ord(character) < 32 for character in tenant_id)
        or not isinstance(platform, str)
        or not platform
        or not isinstance(native_meeting_id, str)
        or not native_meeting_id
    ):
        raise CaptureDenied(CaptureDenial.AUTHORITY_SCOPE_MISMATCH)
    result = await repo.withdraw_capture(
        tenant_id=tenant_id,
        user_id=user_id,
        platform=platform,
        native_meeting_id=native_meeting_id,
        withdrawn_at=withdrawn_at.isoformat(),
    )
    if result is None:
        raise CaptureDenied(CaptureDenial.AUTHORITY_SCOPE_MISMATCH)
    meeting = result["meeting"]
    if result["should_stop"]:
        meeting_id = meeting["id"]
        try:
            await publisher.publish(
                leave_command_channel(meeting_id),
                json.dumps(leave_command_payload(meeting_id)),
            )
        finally:
            # A booting workload may not yet be subscribed to the leave channel. Attempt the
            # direct teardown even when Redis publication fails; the durable withdrawal already
            # makes all later transcript and recording writes fail closed.
            if (
                runtime is not None
                and result["prior_status"] in {"requested", "joining", "awaiting_admission"}
                and meeting.get("bot_container_id")
            ):
                try:
                    await runtime.delete_workload(meeting["bot_container_id"])
                except Exception as error:  # noqa: BLE001 — durable refusal already protects privacy
                    log_event(
                        "capture_withdraw_teardown_failed",
                        audience="system",
                        level="warning",
                        span="capture.withdraw",
                        user_id=user_id,
                        meeting_id=str(meeting_id),
                        fields={"error": str(error)},
                    )
    capture = meeting["data"]["zaki_capture"]
    return {
        "meeting_id": meeting["id"],
        "state": "withdrawn",
        "changed": result["changed"],
        "withdrawn_at": capture["withdrawn_at"],
    }
