"""Fail-closed ZAKI capture authorization and spawn composition."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import hmac
from typing import Optional

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
