"""Seam tests for the sealed Hub-to-Minutes control plane."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher
from meeting_api.retention.fakes import InMemoryRetentionRepo, InMemoryRetentionStorage
from meeting_api.zaki_control.fakes import InMemoryControlStore
from meeting_api.zaki_control.ports import CallbackEvent, Subject
from meeting_api.zaki_control.router import ControlConfig, build_router


SECRET = "zaki-control-test-signing-secret-0123456789"
NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
SUBJECT = {"tenant_id": "tenant-1", "user_id": "42"}


class _SettlingDispatcher:
    """In-process Hub acknowledgement seam for control-router erasure tests."""

    def __init__(self, store):
        self.store = store

    async def _record(self, capture, state, failure_code=None):
        event = CallbackEvent(
            event_id=f"test-{capture.capture_id}-{state}",
            body={"event_id": f"test-{capture.capture_id}-{state}"},
            subject=capture.subject,
            capture_id=capture.capture_id,
            terminal=state in {"completed", "failed"},
        )
        await self.store.record_capture_transition(
            capture=capture, state=state, failure_code=failure_code, events=(event,)
        )

    async def record_capture_status(self, capture, *, state, failure_code=None):
        await self._record(capture, state, failure_code)

    async def record_capture_timeline(self, capture, *, state, failure_code=None):
        await self._record(capture, state, failure_code)

    async def reconcile_capture_lifecycle(self, _meeting):
        return None

    async def drain_capture_terminal(self, capture_id):
        for event in await self.store.pending_callbacks(limit=50, capture_id=capture_id):
            await self.store.mark_callback_delivered(event.event_id)
        return await self.store.terminal_callbacks_delivered(capture_id)


def _token(*, tenant_id="tenant-1", user_id="42", exp=60) -> str:
    claims = {
        "aud": "zaki-control.v1", "exp": int(NOW.timestamp()) + exp,
        "iat": int(NOW.timestamp()), "tenant_id": tenant_id, "user_id": user_id, "v": 1,
    }
    payload = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    signature = base64.urlsafe_b64encode(
        hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"{payload}.{signature}"


def _headers(request_id="request-1", idempotency_key="key-1", **overrides):
    headers = {
        "X-Zaki-Control-Token": _token(),
        "X-Zaki-Tenant-Id": "tenant-1",
        "X-Zaki-User-Id": "42",
        "X-Request-Id": request_id,
        "Idempotency-Key": idempotency_key,
    }
    headers.update(overrides)
    return headers


def _client(monkeypatch, *, store=None, repo=None, runtime=None, callback_dispatcher=None):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.example.test")
    monkeypatch.setenv("ADMIN_TOKEN", "meeting-token-test-secret-0123456789")
    app = FastAPI()
    store = store or InMemoryControlStore()
    repo = repo or InMemoryMeetingRepo()
    runtime = runtime or FakeRuntimeClient()
    publisher = InMemoryCommandPublisher()
    retention_repo = InMemoryRetentionRepo()
    retention_storage = InMemoryRetentionStorage()
    callback_dispatcher = callback_dispatcher or _SettlingDispatcher(store)
    app.include_router(build_router(
        store=store,
        config=ControlConfig(enabled=True, operator_enabled=True, signing_secret=SECRET),
        meeting_repo=repo, runtime=runtime,
        command_publisher=publisher,
        retention_repo=retention_repo, retention_storage=retention_storage,
        callback_dispatcher=callback_dispatcher,
        now=lambda: NOW,
    ))
    return TestClient(app), store, repo, runtime, publisher, retention_repo, retention_storage


def _ensure(request_id="request-1", idempotency_key="key-1"):
    return {
        "api_version": "zaki-control.v1", "request_id": request_id,
        "idempotency_key": idempotency_key, "subject": SUBJECT,
        "policy": {
            "capture_enabled": True, "agent_read_enabled": True,
            "capture_notice_policy_version": "notice-v1",
            "retention": {"audio_days": 7, "transcript_days": 30, "summary_days": 30},
        },
    }


def _capture(request_id="capture-request", idempotency_key="capture-key", *, reserved_units=60):
    return {
        "api_version": "zaki-control.v1", "request_id": request_id,
        "idempotency_key": idempotency_key, "subject": SUBJECT,
        "platform": "google_meet", "meeting_url": "https://meet.google.com/abc-defg-hij",
        "capture_attestation": {
            "bot_visible": True, "bot_display_name": "ZAKI Notetaker", "policy_version": "notice-v1",
            "attested_at": NOW.isoformat(), "attested_by_user_id": "42",
        },
        "metering": {"reservation_id": "reserve-1", "unit": "bot_minute", "reserved_units": reserved_units},
    }


def test_control_ready_endpoint_requires_the_mounted_operator_and_lifecycle_readiness(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.example.test")
    monkeypatch.setenv("ADMIN_TOKEN", "meeting-token-test-secret-0123456789")
    store = InMemoryControlStore()
    config = ControlConfig(
        enabled=True,
        operator_enabled=True,
        signing_secret=SECRET,
        max_capture_seconds=3_600,
    )
    app = create_app(
        meeting_repo=InMemoryMeetingRepo(),
        runtime=FakeRuntimeClient(),
        command_publisher=InMemoryCommandPublisher(),
        zaki_control_store=store,
        zaki_control_config=config,
        zaki_control_retention_repo=InMemoryRetentionRepo(),
        zaki_control_retention_storage=InMemoryRetentionStorage(),
        zaki_control_callback=_SettlingDispatcher(store),
    )
    client = TestClient(app)

    ready = client.get("/api/zaki/control/v1/ready")
    assert ready.status_code == 200
    assert ready.json() == {
        "api_version": "zaki-control.v1",
        "state": "ready",
        "operator_enabled": True,
        "max_capture_seconds": 3_600,
    }

    app.state.zaki_control_ready = False
    assert client.get("/api/zaki/control/v1/ready").status_code == 503


def _ready_app(monkeypatch, *, operator_enabled: bool, max_capture_seconds: int = 3_600):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.example.test")
    monkeypatch.setenv("ADMIN_TOKEN", "meeting-token-test-secret-0123456789")
    store = InMemoryControlStore()
    return create_app(
        meeting_repo=InMemoryMeetingRepo(),
        runtime=FakeRuntimeClient(),
        command_publisher=InMemoryCommandPublisher(),
        zaki_control_store=store,
        zaki_control_config=ControlConfig(
            enabled=True,
            operator_enabled=operator_enabled,
            signing_secret=SECRET,
            max_capture_seconds=max_capture_seconds,
        ),
        zaki_control_retention_repo=InMemoryRetentionRepo(),
        zaki_control_retention_storage=InMemoryRetentionStorage(),
        zaki_control_callback=_SettlingDispatcher(store),
    )


def test_ready_body_matches_the_infra_startup_gate_byte_for_byte(monkeypatch):
    """The zaki-api chart gates Hub rollout on `grep -F` of these EXACT substrings.

    charts/zaki-api/templates/deployment.yaml greps the raw body for
    `"api_version":"zaki-control.v1"`, `"state":"ready"`, `"operator_enabled":true` and
    `"max_capture_seconds":<N>` — with NO whitespace after the colons. A pretty-printed or
    space-separated encoder would leave the init container looping until its deadline, so the
    serialized bytes are the contract here, not the parsed object.
    """
    client = TestClient(_ready_app(monkeypatch, operator_enabled=True, max_capture_seconds=3_600))
    raw = client.get("/api/zaki/control/v1/ready").text

    for fragment in (
        '"api_version":"zaki-control.v1"',
        '"state":"ready"',
        '"operator_enabled":true',
        '"max_capture_seconds":3600',
    ):
        assert fragment in raw, f"startup gate substring {fragment!r} absent from {raw!r}"


def test_ready_reports_an_operator_disabled_engine_truthfully(monkeypatch):
    """A dark engine must FAIL the gate rather than assert a readiness it does not have."""
    client = TestClient(_ready_app(monkeypatch, operator_enabled=False))
    response = client.get("/api/zaki/control/v1/ready")

    assert response.status_code == 200
    assert response.json()["operator_enabled"] is False
    # The chart greps for the literal `true`; an operator-disabled engine must not satisfy it.
    assert '"operator_enabled":true' not in response.text


def test_control_capture_rejects_a_platform_outside_the_staging_egress_contract(monkeypatch):
    client, _store, *_ = _client(monkeypatch)
    body = _capture()
    body.update({"platform": "zoom", "meeting_url": "https://acme.zoom.us/j/12345678901"})

    response = client.post("/api/zaki/control/v1/42/captures", headers=_headers(), json=body)

    assert response.status_code == 422
    # The refusal must stay inside the sealed ErrorResponse vocabulary.
    assert response.json()["code"] == "invalid_request"


def test_ensure_binds_all_identities_and_replays_with_the_new_request_id(monkeypatch):
    client, _store, *_ = _client(monkeypatch)
    first = client.post("/api/zaki/control/v1/42/ensure", headers=_headers(), json=_ensure())
    assert first.status_code == 200
    assert first.json()["state"] == "ready"
    operation_id = first.json()["operation_id"]

    replay = client.post(
        "/api/zaki/control/v1/42/ensure",
        headers=_headers("request-2", "key-1"), json=_ensure("request-2", "key-1"),
    )
    assert replay.status_code == 200
    assert replay.json()["request_id"] == "request-2"
    assert replay.json()["operation_id"] == operation_id

    conflict = client.post(
        "/api/zaki/control/v1/42/ensure",
        headers=_headers("request-3", "key-1"),
        json={**_ensure("request-3", "key-1"), "policy": {**_ensure()["policy"], "capture_enabled": False}},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    assert "detail" not in conflict.json()


def test_control_rejects_a_valid_token_when_one_identity_copy_disagrees(monkeypatch):
    client, _store, *_ = _client(monkeypatch)
    response = client.post(
        "/api/zaki/control/v1/42/ensure",
        headers=_headers(**{"X-Zaki-User-Id": "43"}), json=_ensure(),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "subject_mismatch"
    assert response.headers["cache-control"] == "no-store"


def test_capture_uses_the_visible_notetaker_and_persists_read_policy(monkeypatch):
    client, store, _repo, runtime, *_ = _client(monkeypatch)
    assert client.post("/api/zaki/control/v1/42/ensure", headers=_headers(), json=_ensure()).status_code == 200
    body = _capture()
    response = client.post(
        "/api/zaki/control/v1/42/captures",
        headers=_headers("capture-request", "capture-key"), json=body,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "requested"
    capture = asyncio.run(store.get_capture(
        subject=Subject("tenant-1", "42"), capture_id=payload["capture_id"]
    ))
    assert capture is not None and capture.meeting_id == payload["meeting_id"]
    assert runtime.specs[-1]["maxLifetimeSec"] == 3600


def test_capture_rejects_a_reservation_larger_than_the_runtime_lifetime(monkeypatch):
    client, _store, _repo, runtime, *_ = _client(monkeypatch)
    assert client.post("/api/zaki/control/v1/42/ensure", headers=_headers(), json=_ensure()).status_code == 200

    response = client.post(
        "/api/zaki/control/v1/42/captures",
        headers=_headers("capture-request", "capture-key"),
        json=_capture(reserved_units=61),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert runtime.specs == []


class _FailOnceBindStore(InMemoryControlStore):
    def __init__(self):
        super().__init__()
        self.fail_bind = True

    async def bind_capture_meeting(self, *, capture_id, meeting_id):
        if self.fail_bind:
            self.fail_bind = False
            raise RuntimeError("simulated post-spawn control-store outage")
        await super().bind_capture_meeting(capture_id=capture_id, meeting_id=meeting_id)


def test_capture_retry_recovers_a_spawn_before_mapping_commit_without_a_second_bot(monkeypatch):
    store = _FailOnceBindStore()
    client, _store, repo, runtime, *_ = _client(monkeypatch, store=store)
    assert client.post("/api/zaki/control/v1/42/ensure", headers=_headers(), json=_ensure()).status_code == 200

    first = client.post(
        "/api/zaki/control/v1/42/captures",
        headers=_headers("capture-request", "capture-key"), json=_capture(),
    )
    assert first.status_code == 503
    assert len(runtime.specs) == 1
    assert len(repo._meetings) == 1

    retry = client.post(
        "/api/zaki/control/v1/42/captures",
        headers=_headers("capture-retry", "capture-key"),
        json=_capture("capture-retry", "capture-key"),
    )

    assert retry.status_code == 200
    assert retry.json()["state"] == "requested"
    assert len(runtime.specs) == 1
    assert len(repo._meetings) == 1


def test_meeting_erasure_withdraws_an_active_capture_before_deleting_retention(monkeypatch):
    client, _store, _repo, _runtime, publisher, retention_repo, *_ = _client(monkeypatch)
    assert client.post("/api/zaki/control/v1/42/ensure", headers=_headers(), json=_ensure()).status_code == 200
    created = client.post(
        "/api/zaki/control/v1/42/captures",
        headers=_headers("capture-request", "capture-key"), json=_capture(),
    )
    assert created.status_code == 200
    meeting_id = created.json()["meeting_id"]
    retention_repo.seed_meeting(
        user_id="42", meeting_id=meeting_id, transcript_rows=["segment"], summaries=["summary"],
        recording_prefixes=[], recording_objects=0,
    )

    body = {
        "api_version": "zaki-control.v1", "request_id": "erase-request", "idempotency_key": "erase-key",
        "subject": SUBJECT, "meeting_id": meeting_id,
    }
    erased = client.post(
        f"/api/zaki/control/v1/42/meetings/{meeting_id}/erase",
        headers=_headers("erase-request", "erase-key"), json=body,
    )

    assert erased.status_code == 200
    assert erased.json()["status"] == "completed"
    assert publisher.published
    assert retention_repo.snapshot(meeting_id) is None


def test_erasure_refuses_raw_deletion_until_terminal_settlement_is_acknowledged(monkeypatch):
    class BlockingDispatcher(_SettlingDispatcher):
        async def drain_capture_terminal(self, _capture_id):
            return False

    store = InMemoryControlStore()
    client, _store, _repo, _runtime, _publisher, retention_repo, *_ = _client(
        monkeypatch, store=store, callback_dispatcher=BlockingDispatcher(store)
    )
    assert client.post("/api/zaki/control/v1/42/ensure", headers=_headers(), json=_ensure()).status_code == 200
    created = client.post(
        "/api/zaki/control/v1/42/captures",
        headers=_headers("capture-request", "capture-key"), json=_capture(),
    ).json()
    meeting_id = created["meeting_id"]
    retention_repo.seed_meeting(
        user_id="42", meeting_id=meeting_id, transcript_rows=["segment"], summaries=[],
        recording_prefixes=[], recording_objects=0,
    )
    response = client.post(
        f"/api/zaki/control/v1/42/meetings/{meeting_id}/erase",
        headers=_headers("erase-request", "erase-key"),
        json={
            "api_version": "zaki-control.v1", "request_id": "erase-request", "idempotency_key": "erase-key",
            "subject": SUBJECT, "meeting_id": meeting_id,
        },
    )

    assert response.status_code == 503
    assert retention_repo.snapshot(meeting_id) is not None
    capture = asyncio.run(store.get_capture(
        subject=Subject("tenant-1", "42"), capture_id=created["capture_id"]
    ))
    assert capture is not None


def test_meeting_erase_does_not_delete_an_uncontrolled_legacy_row(monkeypatch):
    client, _store, _repo, _runtime, _publisher, retention_repo, *_ = _client(monkeypatch)
    assert client.post("/api/zaki/control/v1/42/ensure", headers=_headers(), json=_ensure()).status_code == 200
    retention_repo.seed_meeting(
        user_id="42", meeting_id="999", transcript_rows=["uncontrolled"], summaries=[],
        recording_prefixes=[], recording_objects=0,
    )
    response = client.post(
        "/api/zaki/control/v1/42/meetings/999/erase",
        headers=_headers("erase-request", "erase-key"),
        json={
            "api_version": "zaki-control.v1", "request_id": "erase-request", "idempotency_key": "erase-key",
            "subject": SUBJECT, "meeting_id": "999",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "already_absent"
    assert retention_repo.snapshot("999") is not None


def test_erasure_retry_keeps_the_same_prepared_receipt_after_response_crash(monkeypatch):
    class FailOnceEraseCompletionStore(InMemoryControlStore):
        def __init__(self):
            super().__init__()
            self.fail = True

        async def complete_operation(self, **kwargs):
            if kwargs["operation"] == "erase_meeting" and self.fail:
                self.fail = False
                raise RuntimeError("simulated response-write crash")
            await super().complete_operation(**kwargs)

    store = FailOnceEraseCompletionStore()
    client, _store, _repo, _runtime, _publisher, retention_repo, *_ = _client(monkeypatch, store=store)
    assert client.post("/api/zaki/control/v1/42/ensure", headers=_headers(), json=_ensure()).status_code == 200
    created = client.post(
        "/api/zaki/control/v1/42/captures",
        headers=_headers("capture-request", "capture-key"), json=_capture(),
    ).json()
    meeting_id = created["meeting_id"]
    retention_repo.seed_meeting(
        user_id="42", meeting_id=meeting_id, transcript_rows=["segment"], summaries=["summary"],
        recording_prefixes=[], recording_objects=0,
    )
    body = {
        "api_version": "zaki-control.v1", "request_id": "erase-request", "idempotency_key": "erase-key",
        "subject": SUBJECT, "meeting_id": meeting_id,
    }
    first = client.post(
        f"/api/zaki/control/v1/42/meetings/{meeting_id}/erase",
        headers=_headers("erase-request", "erase-key"), json=body,
    )
    assert first.status_code == 503
    assert retention_repo.snapshot(meeting_id) is None

    retry_body = {**body, "request_id": "erase-retry"}
    retry = client.post(
        f"/api/zaki/control/v1/42/meetings/{meeting_id}/erase",
        headers=_headers("erase-retry", "erase-key"), json=retry_body,
    )
    assert retry.status_code == 200
    assert retry.json()["receipt"]["counts"] == {
        "meeting_rows": 1, "transcript_rows": 1, "summary_rows": 1, "recording_objects": 0,
    }
    replay = client.post(
        f"/api/zaki/control/v1/42/meetings/{meeting_id}/erase",
        headers=_headers("erase-replay", "erase-key"),
        json={**body, "request_id": "erase-replay"},
    )
    assert replay.status_code == 200
    assert replay.json()["receipt"] == retry.json()["receipt"]


def test_subject_erasure_barrier_rejects_capture_admission(monkeypatch):
    client, store, *_ = _client(monkeypatch)
    assert client.post("/api/zaki/control/v1/42/ensure", headers=_headers(), json=_ensure()).status_code == 200
    assert asyncio.run(store.begin_subject_erasure(
        subject=Subject("tenant-1", "42"), operation_id="erase-barrier", fence=1
    ))

    response = client.post(
        "/api/zaki/control/v1/42/captures",
        headers=_headers("capture-request", "capture-key"), json=_capture(),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "minutes_disabled"


def test_completed_capture_replays_before_a_later_policy_change(monkeypatch):
    client, store, *_ = _client(monkeypatch)
    assert client.post("/api/zaki/control/v1/42/ensure", headers=_headers(), json=_ensure()).status_code == 200
    first = client.post(
        "/api/zaki/control/v1/42/captures",
        headers=_headers("capture-request", "capture-key"), json=_capture(),
    )
    assert first.status_code == 200
    policy = asyncio.run(store.get_policy(Subject("tenant-1", "42")))
    assert policy is not None
    asyncio.run(store.put_policy(Subject("tenant-1", "42"), policy.__class__(
        capture_enabled=False, agent_read_enabled=policy.agent_read_enabled,
        policy_version=policy.policy_version, audio_days=policy.audio_days,
        transcript_days=policy.transcript_days, summary_days=policy.summary_days,
    )))

    replay = client.post(
        "/api/zaki/control/v1/42/captures",
        headers=_headers("capture-replay", "capture-key"),
        json=_capture("capture-replay", "capture-key"),
    )
    assert replay.status_code == 200
    assert replay.json()["operation_id"] == first.json()["operation_id"]


def test_control_fence_rejects_a_reclaimed_executor():
    async def exercise():
        store = InMemoryControlStore()
        subject = Subject("tenant-1", "42")
        first = await store.claim_operation(
            subject=subject, operation="capture", idempotency_key="key", request_sha256="hash", operation_id="op-1"
        )
        reclaimed = await store.claim_operation(
            subject=subject, operation="capture", idempotency_key="key", request_sha256="hash", operation_id="op-2"
        )
        assert first.fence == 1 and reclaimed.fence == 2
        try:
            await store.assert_operation_fence(
                subject=subject, operation="capture", idempotency_key="key", fence=first.fence
            )
        except RuntimeError:
            return
        raise AssertionError("stale executor was not fenced")

    asyncio.run(exercise())
