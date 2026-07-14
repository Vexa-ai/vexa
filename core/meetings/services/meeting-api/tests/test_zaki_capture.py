"""ZAKI capture profile — authority intersection and visible consent evidence."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.capture import (
    CaptureAuthority,
    CaptureDenial,
    CaptureDenied,
    ZAKI_NOTETAKER_NAME,
    request_capture,
)
from meeting_api.retention import ScopeExpiries


USER = 7
SECRET = "test-admin-token"
ATTESTED_AT = datetime(2026, 7, 15, 8, 30, tzinfo=timezone.utc)
AUTHORIZED_AT = ATTESTED_AT + timedelta(minutes=1)
VALID_UNTIL = AUTHORIZED_AT + timedelta(minutes=5)
RETENTION_EXPIRIES = ScopeExpiries(
    audio=AUTHORIZED_AT + timedelta(days=1),
    transcript=AUTHORIZED_AT + timedelta(days=7),
    summary=AUTHORIZED_AT + timedelta(days=30),
)


def _allowed(native_meeting_id: str = "abc-defg-hij") -> CaptureAuthority:
    return CaptureAuthority(
        operator_enabled=True,
        tenant_enabled=True,
        tenant_attested=True,
        tenant_policy_version="capture-v1",
        tenant_attested_at=ATTESTED_AT,
        user_requested=True,
        quota_permitted=True,
        subject_user_id=USER,
        tenant_id="tenant-a",
        meeting_platform="google_meet",
        native_meeting_id=native_meeting_id,
        authorized_at=AUTHORIZED_AT,
        valid_until=VALID_UNTIL,
        scope_expiries=RETENTION_EXPIRIES,
    )


async def test_authorized_capture_forces_visible_bot_and_persists_content_free_evidence(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()

    meeting = await request_capture(
        repo,
        runtime,
        authority=_allowed(),
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        redis_url="redis://redis:6379/0",
        meeting_api_url="http://meeting-api:8080",
        token_secret=SECRET,
        evaluated_at=AUTHORIZED_AT,
    )

    invocation = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert invocation["botName"] == ZAKI_NOTETAKER_NAME
    assert invocation["recordingEnabled"] is True
    assert invocation["transcribeEnabled"] is True
    assert meeting["data"]["zaki_capture"] == {
        "bot_name": ZAKI_NOTETAKER_NAME,
        "tenant_id": "tenant-a",
        "state": "authorized",
        "tenant_attested": True,
        "tenant_policy_version": "capture-v1",
        "tenant_attested_at": "2026-07-15T08:30:00+00:00",
        "user_requested": True,
        "authorized_at": "2026-07-15T08:31:00+00:00",
        "authority_valid_until": "2026-07-15T08:36:00+00:00",
    }
    assert "transcript" not in json.dumps(meeting["data"]["zaki_capture"]).lower()


@pytest.mark.parametrize(
    ("authority", "denial"),
    [
        (replace(_allowed("blocked-meeting"), operator_enabled=False), CaptureDenial.OPERATOR_DISABLED),
        (replace(_allowed("blocked-meeting"), operator_enabled=None), CaptureDenial.OPERATOR_POLICY_INVALID),
        (replace(_allowed("blocked-meeting"), tenant_enabled=False), CaptureDenial.TENANT_DISABLED),
        (replace(_allowed("blocked-meeting"), tenant_attested=False), CaptureDenial.TENANT_ATTESTATION_REQUIRED),
        (replace(_allowed("blocked-meeting"), tenant_policy_version="  "), CaptureDenial.TENANT_POLICY_INVALID),
        (
            replace(_allowed("blocked-meeting"), tenant_attested_at=datetime(2026, 7, 15, 8, 30)),
            CaptureDenial.TENANT_POLICY_INVALID,
        ),
        (
            replace(
                _allowed("blocked-meeting"),
                tenant_attested_at=AUTHORIZED_AT + timedelta(seconds=1),
            ),
            CaptureDenial.TENANT_POLICY_INVALID,
        ),
        (replace(_allowed("blocked-meeting"), user_requested=False), CaptureDenial.USER_NOT_REQUESTED),
        (replace(_allowed("blocked-meeting"), quota_permitted=False), CaptureDenial.QUOTA_EXHAUSTED),
    ],
)
async def test_capture_denials_are_named_and_mutation_free(monkeypatch, authority, denial):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()

    with pytest.raises(CaptureDenied) as exc:
        await request_capture(
            repo,
            runtime,
            authority=authority,
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="blocked-meeting",
            token_secret=SECRET,
            evaluated_at=AUTHORIZED_AT,
        )

    assert exc.value.code is denial
    assert await repo.find_latest(USER, "google_meet", "blocked-meeting") is None
    assert runtime.specs == []


async def test_atomic_concurrency_quota_denial_is_translated_and_mutation_free(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()

    with pytest.raises(CaptureDenied) as exc:
        await request_capture(
            repo,
            runtime,
            authority=_allowed("quota-blocked"),
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="quota-blocked",
            max_concurrent=0,
            token_secret=SECRET,
            evaluated_at=AUTHORIZED_AT,
        )

    assert exc.value.code is CaptureDenial.QUOTA_EXHAUSTED
    assert await repo.find_latest(USER, "google_meet", "quota-blocked") is None
    assert runtime.specs == []


async def test_capture_authority_cannot_be_replayed_for_another_user(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()

    with pytest.raises(CaptureDenied) as exc:
        await request_capture(
            repo,
            runtime,
            authority=replace(_allowed(), subject_user_id=USER + 1),
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            token_secret=SECRET,
            evaluated_at=AUTHORIZED_AT,
        )

    assert exc.value.code is CaptureDenial.AUTHORITY_SCOPE_MISMATCH
    assert await repo.find_latest(USER, "google_meet", "abc-defg-hij") is None
    assert runtime.specs == []


async def test_capture_authority_missing_subject_binding_is_rejected(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()

    with pytest.raises(CaptureDenied) as exc:
        await request_capture(
            repo,
            runtime,
            authority=replace(_allowed(), subject_user_id=None),
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            token_secret=SECRET,
            evaluated_at=AUTHORIZED_AT,
        )

    assert exc.value.code is CaptureDenial.AUTHORITY_SCOPE_MISMATCH
    assert await repo.find_latest(USER, "google_meet", "abc-defg-hij") is None
    assert runtime.specs == []


async def test_capture_authority_is_bound_to_tenant_and_exact_meeting(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    authority = replace(
        _allowed(),
        subject_user_id=USER,
        tenant_id="tenant-a",
        meeting_platform="google_meet",
        native_meeting_id="allowed-meeting",
    )

    with pytest.raises(CaptureDenied) as exc:
        await request_capture(
            repo,
            runtime,
            authority=authority,
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="different-meeting",
            token_secret=SECRET,
            evaluated_at=AUTHORIZED_AT,
        )

    assert exc.value.code is CaptureDenial.AUTHORITY_SCOPE_MISMATCH
    assert await repo.find_latest(USER, "google_meet", "different-meeting") is None
    assert runtime.specs == []


async def test_capture_authority_missing_tenant_or_meeting_binding_is_rejected(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()

    with pytest.raises(CaptureDenied) as exc:
        await request_capture(
            repo,
            runtime,
            authority=replace(
                _allowed(),
                tenant_id=None,
                meeting_platform=None,
                native_meeting_id=None,
            ),
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            token_secret=SECRET,
            evaluated_at=AUTHORIZED_AT,
        )

    assert exc.value.code is CaptureDenial.AUTHORITY_SCOPE_MISMATCH
    assert await repo.find_latest(USER, "google_meet", "abc-defg-hij") is None
    assert runtime.specs == []


async def test_capture_authority_missing_validity_window_is_rejected(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()

    with pytest.raises(CaptureDenied) as exc:
        await request_capture(
            repo,
            runtime,
            authority=replace(_allowed(), authorized_at=None, valid_until=None),
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            evaluated_at=AUTHORIZED_AT,
            token_secret=SECRET,
        )

    assert exc.value.code is CaptureDenial.AUTHORITY_EXPIRED
    assert await repo.find_latest(USER, "google_meet", "abc-defg-hij") is None
    assert runtime.specs == []


async def test_expired_capture_authority_is_rejected_before_io(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    authority = replace(
        _allowed(),
        authorized_at=AUTHORIZED_AT,
        valid_until=VALID_UNTIL,
    )

    with pytest.raises(CaptureDenied) as exc:
        await request_capture(
            repo,
            runtime,
            authority=authority,
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            evaluated_at=VALID_UNTIL,
            token_secret=SECRET,
        )

    assert exc.value.code is CaptureDenial.AUTHORITY_EXPIRED
    assert await repo.find_latest(USER, "google_meet", "abc-defg-hij") is None
    assert runtime.specs == []


async def test_authorized_capture_materializes_per_scope_retention(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    expiries = ScopeExpiries(
        audio=AUTHORIZED_AT + timedelta(days=1),
        transcript=AUTHORIZED_AT + timedelta(days=7),
        summary=AUTHORIZED_AT + timedelta(days=30),
    )

    meeting = await request_capture(
        repo,
        runtime,
        authority=replace(_allowed(), scope_expiries=expiries),
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        evaluated_at=AUTHORIZED_AT,
        token_secret=SECRET,
    )

    assert meeting["data"]["zaki_retention"] == {
        "state": "open",
        "scope_expiries": {
            "audio": "2026-07-16T08:31:00+00:00",
            "transcript": "2026-07-22T08:31:00+00:00",
            "summary": "2026-08-14T08:31:00+00:00",
        },
        "expired_scopes": [],
    }


async def test_capture_without_retention_policy_is_rejected_before_io(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()

    with pytest.raises(CaptureDenied) as exc:
        await request_capture(
            repo,
            runtime,
            authority=replace(_allowed(), scope_expiries=None),
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            evaluated_at=AUTHORIZED_AT,
            token_secret=SECRET,
        )

    assert exc.value.code is CaptureDenial.RETENTION_POLICY_INVALID
    assert await repo.find_latest(USER, "google_meet", "abc-defg-hij") is None
    assert runtime.specs == []


async def test_capture_rejects_unsafe_meeting_url_before_io(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()

    with pytest.raises(CaptureDenied) as exc:
        await request_capture(
            repo,
            runtime,
            authority=_allowed(),
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            meeting_url="http://169.254.169.254/latest/meta-data",
            evaluated_at=AUTHORIZED_AT,
            token_secret=SECRET,
        )

    assert exc.value.code is CaptureDenial.MEETING_URL_INVALID
    assert await repo.find_latest(USER, "google_meet", "abc-defg-hij") is None
    assert runtime.specs == []


async def test_capture_authority_is_bound_to_exact_explicit_meeting_url(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    allowed_url = "https://meet.example.org/allowed-room"
    authority = replace(
        _allowed(),
        meeting_url_sha256=hashlib.sha256(allowed_url.encode()).hexdigest(),
    )

    with pytest.raises(CaptureDenied) as exc:
        await request_capture(
            repo,
            runtime,
            authority=authority,
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            meeting_url="https://meet.example.org/different-room",
            evaluated_at=AUTHORIZED_AT,
            token_secret=SECRET,
        )

    assert exc.value.code is CaptureDenial.AUTHORITY_SCOPE_MISMATCH
    assert await repo.find_latest(USER, "google_meet", "abc-defg-hij") is None
    assert runtime.specs == []


async def test_runtime_quota_rejection_is_a_named_terminal_non_capture(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient(quota_exceeded=True)

    with pytest.raises(CaptureDenied) as exc:
        await request_capture(
            repo,
            runtime,
            authority=_allowed("runtime-quota"),
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="runtime-quota",
            evaluated_at=AUTHORIZED_AT,
            token_secret=SECRET,
        )

    assert exc.value.code is CaptureDenial.QUOTA_EXHAUSTED
    meeting = await repo.find_latest(USER, "google_meet", "runtime-quota")
    assert meeting["status"] == "failed"
    assert meeting["data"]["zaki_capture"]["state"] == "denied"
    assert meeting["data"]["zaki_capture"]["denial"] == "quota_exhausted"
    assert repo.sessions == []
