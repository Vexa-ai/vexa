"""ZAKI capture profile — authority intersection and visible consent evidence."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
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


USER = 7
SECRET = "test-admin-token"
ATTESTED_AT = datetime(2026, 7, 15, 8, 30, tzinfo=timezone.utc)


def _allowed() -> CaptureAuthority:
    return CaptureAuthority(
        operator_enabled=True,
        tenant_enabled=True,
        tenant_attested=True,
        tenant_policy_version="capture-v1",
        tenant_attested_at=ATTESTED_AT,
        user_requested=True,
        quota_permitted=True,
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
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        redis_url="redis://redis:6379/0",
        meeting_api_url="http://meeting-api:8080",
        token_secret=SECRET,
    )

    invocation = json.loads(runtime.specs[0]["env"]["BOT_CONFIG"])
    assert invocation["botName"] == ZAKI_NOTETAKER_NAME
    assert invocation["recordingEnabled"] is True
    assert invocation["transcribeEnabled"] is True
    assert meeting["data"]["zaki_capture"] == {
        "bot_name": ZAKI_NOTETAKER_NAME,
        "tenant_attested": True,
        "tenant_policy_version": "capture-v1",
        "tenant_attested_at": "2026-07-15T08:30:00+00:00",
        "user_requested": True,
    }
    assert "transcript" not in json.dumps(meeting["data"]).lower()


@pytest.mark.parametrize(
    ("authority", "denial"),
    [
        (replace(_allowed(), operator_enabled=False), CaptureDenial.OPERATOR_DISABLED),
        (replace(_allowed(), operator_enabled=None), CaptureDenial.OPERATOR_POLICY_INVALID),
        (replace(_allowed(), tenant_enabled=False), CaptureDenial.TENANT_DISABLED),
        (replace(_allowed(), tenant_attested=False), CaptureDenial.TENANT_ATTESTATION_REQUIRED),
        (replace(_allowed(), tenant_policy_version="  "), CaptureDenial.TENANT_POLICY_INVALID),
        (
            replace(_allowed(), tenant_attested_at=datetime(2026, 7, 15, 8, 30)),
            CaptureDenial.TENANT_POLICY_INVALID,
        ),
        (replace(_allowed(), user_requested=False), CaptureDenial.USER_NOT_REQUESTED),
        (replace(_allowed(), quota_permitted=False), CaptureDenial.QUOTA_EXHAUSTED),
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
            user_id=USER,
            platform="google_meet",
            native_meeting_id="blocked-meeting",
            token_secret=SECRET,
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
            authority=_allowed(),
            user_id=USER,
            platform="google_meet",
            native_meeting_id="quota-blocked",
            max_concurrent=0,
            token_secret=SECRET,
        )

    assert exc.value.code is CaptureDenial.QUOTA_EXHAUSTED
    assert await repo.find_latest(USER, "google_meet", "quota-blocked") is None
    assert runtime.specs == []
