"""ZAKI capture profile — authority intersection and visible consent evidence."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn import QuotaExceeded
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher
from meeting_api.capture import (
    CaptureAuthority,
    CaptureDenial,
    CaptureDenied,
    ZAKI_NOTETAKER_NAME,
    request_capture,
    withdraw_capture,
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
WITHDRAWN_AT = AUTHORIZED_AT + timedelta(minutes=10)


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
        grant_id=f"grant-{native_meeting_id}",
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
        "grant_id_sha256": hashlib.sha256(b"grant-abc-defg-hij").hexdigest(),
    }
    assert "transcript" not in json.dumps(meeting["data"]["zaki_capture"]).lower()


async def test_withdrawal_persists_before_stopping_and_returns_content_free_receipt(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    publisher = InMemoryCommandPublisher()
    meeting = await request_capture(
        repo,
        runtime,
        authority=_allowed(),
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        token_secret=SECRET,
        evaluated_at=AUTHORIZED_AT,
    )

    receipt = await withdraw_capture(
        repo,
        publisher,
        runtime=runtime,
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        withdrawn_at=WITHDRAWN_AT,
    )

    stored = await repo.find_latest(USER, "google_meet", "abc-defg-hij")
    assert receipt == {
        "meeting_id": meeting["id"],
        "state": "withdrawn",
        "changed": True,
        "withdrawn_at": "2026-07-15T08:41:00+00:00",
    }
    assert stored["status"] == "stopping"
    assert stored["data"]["zaki_capture"]["state"] == "withdrawn"
    # The STOP path tombstones the AUTHORITY with its own reason — `capture_stopped` —
    # so the transcript write barrier keeps flushing what was captured under valid consent.
    assert stored["data"]["zaki_capture"]["withdrawal_reason"] == "capture_stopped"
    assert stored["data"]["stop_requested"] is True
    assert publisher.published == [
        (
            f"bot_commands:meeting:{meeting['id']}",
            json.dumps({"action": "leave", "meeting_id": meeting["id"]}),
        )
    ]
    assert runtime.deleted == [meeting["bot_container_id"]]
    assert "transcript" not in json.dumps(receipt).lower()


async def test_withdrawal_still_tears_down_booting_workload_when_leave_publish_fails(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()

    class FailingPublisher:
        async def publish(self, channel: str, message: str) -> None:
            raise RuntimeError("redis unavailable")

    meeting = await request_capture(
        repo,
        runtime,
        authority=_allowed(),
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        token_secret=SECRET,
        evaluated_at=AUTHORIZED_AT,
    )

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await withdraw_capture(
            repo,
            FailingPublisher(),
            runtime=runtime,
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            withdrawn_at=WITHDRAWN_AT,
        )

    stored = await repo.find_latest(USER, "google_meet", "abc-defg-hij")
    assert stored["data"]["zaki_capture"]["state"] == "withdrawn"
    assert runtime.deleted == [meeting["bot_container_id"]]


async def test_late_nonterminal_callback_cannot_resurrect_withdrawn_capture(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
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
        token_secret=SECRET,
        evaluated_at=AUTHORIZED_AT,
    )
    await withdraw_capture(
        repo,
        InMemoryCommandPublisher(),
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        withdrawn_at=WITHDRAWN_AT,
    )

    await repo.update_meeting_status(
        session_uid=repo.sessions[-1]["session_uid"],
        status="active",
    )

    stored = await repo.find_latest(USER, "google_meet", "abc-defg-hij")
    assert stored["id"] == meeting["id"]
    assert stored["status"] == "stopping"
    assert stored["data"]["zaki_capture"]["state"] == "withdrawn"


async def test_late_active_callback_is_not_emitted_after_withdrawal(monkeypatch, goldens):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    await request_capture(
        repo,
        runtime,
        authority=_allowed(),
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        token_secret=SECRET,
        evaluated_at=AUTHORIZED_AT,
    )
    connection_id = repo.sessions[-1]["session_uid"]
    app = create_app(meeting_repo=repo, runtime=runtime)
    client = TestClient(app)
    joining = {**goldens["joining"], "connection_id": connection_id}
    active = {**goldens["active"], "connection_id": connection_id}
    assert client.post("/bots/internal/callback/lifecycle", json=joining).status_code == 200
    await withdraw_capture(
        repo,
        InMemoryCommandPublisher(),
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        withdrawn_at=WITHDRAWN_AT,
    )

    response = client.post("/bots/internal/callback/lifecycle", json=active)

    assert response.status_code == 200
    assert response.json()["meeting_status"] == "stopping"
    assert [
        envelope["data"]["status_change"]["new_status"]
        for envelope in app.state.status_change_webhooks
    ] == ["joining"]
    assert app.state.typed_webhooks == []

    completed = {**goldens["completed-stopped"], "connection_id": connection_id}
    completed_response = client.post("/bots/internal/callback/lifecycle", json=completed)

    assert completed_response.status_code == 200
    assert completed_response.json()["meeting_status"] == "completed"
    stored = await repo.find_latest(USER, "google_meet", "abc-defg-hij")
    assert stored["status"] == "completed"
    assert stored["data"]["zaki_capture"]["state"] == "withdrawn"
    assert [
        (entry["from"], entry["to"])
        for entry in stored["data"]["status_transition"]
    ] == [(None, "joining"), ("joining", "completed")]
    assert [
        (
            envelope["data"]["status_change"]["old_status"],
            envelope["data"]["status_change"]["new_status"],
        )
        for envelope in app.state.status_change_webhooks
    ] == [(None, "joining"), ("joining", "completed")]
    assert [envelope["event_type"] for envelope in app.state.typed_webhooks] == [
        "meeting.completed"
    ]


async def test_direct_terminal_callback_is_accepted_after_withdrawal_while_joining(monkeypatch, goldens):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    await request_capture(
        repo,
        runtime,
        authority=_allowed(),
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        token_secret=SECRET,
        evaluated_at=AUTHORIZED_AT,
    )
    connection_id = repo.sessions[-1]["session_uid"]
    app = create_app(meeting_repo=repo, runtime=runtime)
    client = TestClient(app)
    joining = {**goldens["joining"], "connection_id": connection_id}
    completed = {**goldens["completed-stopped"], "connection_id": connection_id}
    assert client.post("/bots/internal/callback/lifecycle", json=joining).status_code == 200
    await withdraw_capture(
        repo,
        InMemoryCommandPublisher(),
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        withdrawn_at=WITHDRAWN_AT,
    )

    response = client.post("/bots/internal/callback/lifecycle", json=completed)

    assert response.status_code == 200
    assert response.json()["meeting_status"] == "completed"
    stored = await repo.find_latest(USER, "google_meet", "abc-defg-hij")
    assert stored["status"] == "completed"
    assert stored["data"]["zaki_capture"]["state"] == "withdrawn"
    assert [
        (entry["from"], entry["to"])
        for entry in stored["data"]["status_transition"]
    ] == [(None, "joining"), ("joining", "completed")]
    assert [
        (
            envelope["data"]["status_change"]["old_status"],
            envelope["data"]["status_change"]["new_status"],
        )
        for envelope in app.state.status_change_webhooks
    ] == [(None, "joining"), ("joining", "completed")]
    assert [envelope["event_type"] for envelope in app.state.typed_webhooks] == [
        "meeting.completed"
    ]


async def test_withdrawal_is_idempotent_and_preserves_first_timestamp(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    publisher = InMemoryCommandPublisher()
    await request_capture(
        repo,
        runtime,
        authority=_allowed(),
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        token_secret=SECRET,
        evaluated_at=AUTHORIZED_AT,
    )
    first = await withdraw_capture(
        repo,
        publisher,
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        withdrawn_at=WITHDRAWN_AT,
    )
    second = await withdraw_capture(
        repo,
        publisher,
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        withdrawn_at=WITHDRAWN_AT + timedelta(minutes=5),
    )

    assert first["changed"] is True
    assert second == {**first, "changed": False}
    assert len(publisher.published) == 2


async def test_withdrawn_capture_rejects_replay_of_the_same_consent_grant(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    authority = replace(
        _allowed(), grant_id="grant-20260715-0831-tenant-a-user-7"
    )
    first = await request_capture(
        repo,
        runtime,
        authority=authority,
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        token_secret=SECRET,
        evaluated_at=AUTHORIZED_AT,
    )
    await withdraw_capture(
        repo,
        InMemoryCommandPublisher(),
        runtime=runtime,
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        withdrawn_at=AUTHORIZED_AT + timedelta(minutes=1),
    )

    with pytest.raises(CaptureDenied, match="authority_replayed"):
        await request_capture(
            repo,
            runtime,
            authority=authority,
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            token_secret=SECRET,
            evaluated_at=AUTHORIZED_AT + timedelta(minutes=2),
        )

    latest = await repo.find_latest(USER, "google_meet", "abc-defg-hij")
    assert latest["id"] == first["id"]
    assert latest["data"]["zaki_capture"]["state"] == "withdrawn"
    assert len(runtime.specs) == 1


async def test_withdrawn_capture_rejects_a_different_grant_authorized_before_withdrawal(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    first_authority = replace(_allowed(), grant_id="grant-a")
    stale_authority = replace(_allowed(), grant_id="grant-b")
    await request_capture(
        repo,
        runtime,
        authority=first_authority,
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        token_secret=SECRET,
        evaluated_at=AUTHORIZED_AT,
    )
    await withdraw_capture(
        repo,
        InMemoryCommandPublisher(),
        runtime=runtime,
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        withdrawn_at=AUTHORIZED_AT + timedelta(minutes=1),
    )

    with pytest.raises(CaptureDenied, match="authority_replayed"):
        await request_capture(
            repo,
            runtime,
            authority=stale_authority,
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            token_secret=SECRET,
            evaluated_at=AUTHORIZED_AT + timedelta(minutes=2),
        )

    latest = await repo.find_latest(USER, "google_meet", "abc-defg-hij")
    assert latest["data"]["zaki_capture"]["state"] == "withdrawn"
    assert len(runtime.specs) == 1


async def test_withdrawal_tombstone_does_not_deny_another_tenant(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    await request_capture(
        repo,
        runtime,
        authority=replace(_allowed(), grant_id="grant-a"),
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        token_secret=SECRET,
        evaluated_at=AUTHORIZED_AT,
    )
    await withdraw_capture(
        repo,
        InMemoryCommandPublisher(),
        runtime=runtime,
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        withdrawn_at=AUTHORIZED_AT + timedelta(minutes=1),
    )
    tenant_b_authority = replace(
        _allowed(), tenant_id="tenant-b", grant_id="grant-a"
    )

    meeting = await request_capture(
        repo,
        runtime,
        authority=tenant_b_authority,
        tenant_id="tenant-b",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        token_secret=SECRET,
        evaluated_at=AUTHORIZED_AT + timedelta(minutes=2),
    )

    assert meeting["data"]["zaki_capture"]["tenant_id"] == "tenant-b"
    assert meeting["data"]["zaki_capture"]["state"] == "authorized"
    assert len(runtime.specs) == 2


async def test_withdrawal_is_tenant_scoped_and_mutation_free_on_mismatch(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    publisher = InMemoryCommandPublisher()
    await request_capture(
        repo,
        runtime,
        authority=_allowed(),
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        token_secret=SECRET,
        evaluated_at=AUTHORIZED_AT,
    )

    with pytest.raises(CaptureDenied) as exc:
        await withdraw_capture(
            repo,
            publisher,
            tenant_id="tenant-b",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            withdrawn_at=WITHDRAWN_AT,
        )

    stored = await repo.find_latest(USER, "google_meet", "abc-defg-hij")
    assert exc.value.code is CaptureDenial.AUTHORITY_SCOPE_MISMATCH
    assert stored["status"] == "requested"
    assert stored["data"]["zaki_capture"]["state"] == "authorized"
    assert publisher.published == []
    assert runtime.deleted == []


async def test_newer_other_tenant_row_does_not_shadow_withdrawal(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    tenant_a = await request_capture(
        repo,
        runtime,
        authority=_allowed(),
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        token_secret=SECRET,
        evaluated_at=AUTHORIZED_AT,
    )
    other = dict(repo._meetings[tenant_a["id"]])
    other["id"] = 50
    other["status"] = "completed"
    other["data"] = {
        **other["data"],
        "zaki_capture": {
            **other["data"]["zaki_capture"],
            "tenant_id": "tenant-b",
        },
    }
    repo._meetings[50] = other

    receipt = await withdraw_capture(
        repo,
        InMemoryCommandPublisher(),
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        withdrawn_at=WITHDRAWN_AT,
    )

    assert receipt["meeting_id"] == tenant_a["id"]
    assert repo._meetings[tenant_a["id"]]["data"]["zaki_capture"]["state"] == "withdrawn"
    assert repo._meetings[50]["data"]["zaki_capture"]["state"] == "authorized"


async def test_withdrawal_rejects_malformed_timestamp_without_io():
    repo = InMemoryMeetingRepo()
    publisher = InMemoryCommandPublisher()

    with pytest.raises(CaptureDenied) as exc:
        await withdraw_capture(
            repo,
            publisher,
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            withdrawn_at="2026-07-15T08:41:00Z",
        )

    assert exc.value.code is CaptureDenial.USER_REQUEST_INVALID
    assert publisher.published == []


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
    allowed_url = "https://meet.google.com/abc-defg-hij"
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
            meeting_url="https://meet.google.com/xyz-abcd-efg",
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


async def test_runtime_rejection_cannot_overwrite_concurrent_withdrawal(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    repo = InMemoryMeetingRepo()

    class DelayedQuotaRuntime(FakeRuntimeClient):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def create_workload(self, spec):
            self.specs.append(spec)
            self.started.set()
            await self.release.wait()
            raise QuotaExceeded("owner quota exceeded")

    runtime = DelayedQuotaRuntime()
    capture = asyncio.create_task(
        request_capture(
            repo,
            runtime,
            authority=_allowed("runtime-race"),
            tenant_id="tenant-a",
            user_id=USER,
            platform="google_meet",
            native_meeting_id="runtime-race",
            evaluated_at=AUTHORIZED_AT,
            token_secret=SECRET,
        )
    )
    await runtime.started.wait()
    await withdraw_capture(
        repo,
        InMemoryCommandPublisher(),
        runtime=runtime,
        tenant_id="tenant-a",
        user_id=USER,
        platform="google_meet",
        native_meeting_id="runtime-race",
        withdrawn_at=WITHDRAWN_AT,
    )
    runtime.release.set()

    with pytest.raises(CaptureDenied) as exc:
        await capture

    meeting = await repo.find_latest(USER, "google_meet", "runtime-race")
    assert exc.value.code is CaptureDenial.QUOTA_EXHAUSTED
    assert meeting["data"]["zaki_capture"]["state"] == "withdrawn"
    assert meeting["data"]["zaki_capture"]["withdrawn_at"] == WITHDRAWN_AT.isoformat()
