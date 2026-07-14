"""Fail-closed ZAKI capture authorization and spawn composition."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from ..bot_spawn import MaxBotsExceeded, MeetingRepo, RuntimeClient, request_bot


ZAKI_NOTETAKER_NAME = "ZAKI Notetaker"


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


class CaptureDenied(Exception):
    """Capture was denied before spawn; the exception contains no meeting or participant data."""

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


def _require_strict_bool(value: object, invalid: CaptureDenial) -> bool:
    if type(value) is not bool:
        raise CaptureDenied(invalid)
    return value


def _capture_evidence(authority: CaptureAuthority) -> dict:
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

    return {
        "bot_name": ZAKI_NOTETAKER_NAME,
        "tenant_attested": True,
        "tenant_policy_version": policy_version,
        "tenant_attested_at": attested_at.isoformat(),
        "user_requested": True,
    }


class _CaptureEvidenceRepo:
    """Narrow MeetingRepo decorator that adds validated evidence to a fresh spawn write."""

    def __init__(self, delegate: MeetingRepo, evidence: dict):
        self._delegate = delegate
        self._evidence = dict(evidence)

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
        meeting_data["zaki_capture"] = dict(self._evidence)
        return await self._delegate.create_meeting_guarded(
            user_id=user_id,
            platform=platform,
            native_meeting_id=native_meeting_id,
            data=meeting_data,
            max_concurrent=max_concurrent,
            exclude_meeting_id=exclude_meeting_id,
        )


async def request_capture(
    repo: MeetingRepo,
    runtime: RuntimeClient,
    *,
    authority: CaptureAuthority,
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
) -> dict:
    """Authorize and start one ZAKI-managed capture through the existing spawn pipeline.

    Authority validation completes before any repository or runtime call. The caller cannot choose
    a less visible bot identity, disable transcription, or supply its own consent evidence.
    """
    evidence = _capture_evidence(authority)
    capture_repo = _CaptureEvidenceRepo(repo, evidence)
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
    except MaxBotsExceeded as error:
        raise CaptureDenied(CaptureDenial.QUOTA_EXHAUSTED) from error
