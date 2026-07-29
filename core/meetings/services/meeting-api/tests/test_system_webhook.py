"""Operator-owned terminal webhook — private composition without weakening customer SSRF.

The system destination is frozen at boot and receives only terminal typed lifecycle
events.  It is a different trust boundary from a user-configured webhook URL.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.webhooks import (
    BACKOFF_SCHEDULE,
    RETRY_QUEUE_KEY,
    SYSTEM_RETRY_QUEUE_KEY,
    SystemWebhookSink,
    WebhookSink,
    build_envelope,
    build_system_webhook_from_env,
    verify_signature,
)


SECRET = "system-callback-secret"
SYSTEM_URL = "http://webapp:3000/api/hooks/meeting-completed"


class _SystemCapture:
    def __init__(self):
        self.calls = []

    async def deliver(self, envelope, *, label=""):
        self.calls.append({"envelope": envelope, "label": label})


def _seed(repo, *, session_uid="sess-system", data=None):
    meeting = asyncio.run(
        repo.create_meeting(
            user_id=17,
            platform="google_meet",
            native_meeting_id="system-callback-meeting",
            data=data or {},
        )
    )
    asyncio.run(
        repo.create_session(
            meeting_id=meeting["id"],
            session_uid=session_uid,
        )
    )
    return meeting


def _post(client, body):
    response = client.post("/bots/internal/callback/lifecycle", json=body)
    assert response.status_code == 200, response.text


def test_completed_is_sent_once_to_system_sink_and_replay_is_inert():
    repo = InMemoryMeetingRepo()
    _seed(repo)
    sink = _SystemCapture()
    client = TestClient(
        create_app(meeting_repo=repo, system_webhook_sink=sink),
    )

    joining = {
        "connection_id": "sess-system",
        "status": "joining",
        "timestamp": "2026-07-29T10:00:00.000Z",
    }
    active = {
        "connection_id": "sess-system",
        "status": "active",
        "timestamp": "2026-07-29T10:01:00.000Z",
    }
    completed = {
        "connection_id": "sess-system",
        "status": "completed",
        "completion_reason": "stopped",
        "timestamp": "2026-07-29T10:02:00.000Z",
    }
    for event in (joining, active, completed, completed):
        _post(client, event)

    assert len(sink.calls) == 1
    delivered = sink.calls[0]["envelope"]
    assert delivered["event_type"] == "meeting.completed"
    assert delivered["data"]["meeting"]["user_id"] == 17
    assert sink.calls[0]["label"].startswith("meeting:")


def test_failed_terminal_is_sent_but_intermediate_statuses_are_not():
    repo = InMemoryMeetingRepo()
    _seed(repo)
    sink = _SystemCapture()
    client = TestClient(
        create_app(meeting_repo=repo, system_webhook_sink=sink),
    )

    _post(
        client,
        {
            "connection_id": "sess-system",
            "status": "joining",
            "timestamp": "2026-07-29T10:00:00.000Z",
        },
    )
    assert sink.calls == []
    _post(
        client,
        {
            "connection_id": "sess-system",
            "status": "failed",
            "failure_stage": "awaiting_admission",
            "completion_reason": "awaiting_admission_rejected",
            "reason": "host denied admission",
            "timestamp": "2026-07-29T10:00:10.000Z",
        },
    )
    assert [call["envelope"]["event_type"] for call in sink.calls] == [
        "bot.failed",
    ]


@pytest.mark.asyncio
async def test_private_system_destination_is_signed_while_customer_path_stays_blocked(
    receiver,
):
    customer = WebhookSink(
        transport=receiver,
        resolver=lambda _host: ["127.0.0.1"],
    )
    envelope = build_envelope(
        "meeting.completed",
        {"meeting": {"id": 42}},
        event_id="evt_00000000000000000000000000000042",
    )
    customer_result = await customer.deliver(
        SYSTEM_URL,
        envelope,
        SECRET,
    )
    assert customer_result.status == "blocked"
    assert receiver.received == []

    system = SystemWebhookSink(
        url=SYSTEM_URL,
        secret=SECRET,
        transport=receiver,
        allow_private_http=True,
    )
    system_result = await system.deliver(envelope, label="meeting:42")
    assert system_result.status == "delivered"
    assert len(receiver.received) == 1
    observed = receiver.received[0]
    assert verify_signature(observed["body"], observed["headers"], SECRET)


def test_private_http_requires_explicit_operator_opt_in(receiver):
    with pytest.raises(ValueError, match="private HTTP"):
        SystemWebhookSink(
            url=SYSTEM_URL,
            secret=SECRET,
            transport=receiver,
            allow_private_http=False,
        )


def test_public_http_is_rejected_even_with_private_opt_in(receiver):
    with pytest.raises(ValueError, match="in-cluster service name"):
        SystemWebhookSink(
            url="http://billing.example.com/hooks/meeting-completed",
            secret=SECRET,
            transport=receiver,
            allow_private_http=True,
        )


@pytest.mark.asyncio
async def test_retry_uses_system_queue_and_same_event_identity(
    receiver,
    fake_redis,
):
    system = SystemWebhookSink(
        url=SYSTEM_URL,
        secret=SECRET,
        transport=receiver,
        redis=fake_redis,
        allow_private_http=True,
    )
    envelope = build_envelope(
        "meeting.completed",
        {"meeting": {"id": 43}},
        event_id="evt_00000000000000000000000000000043",
    )

    receiver.default_code = 500
    result = await system.deliver(envelope, label="meeting:43")
    assert result.status == "queued"
    assert await system.retry_depth() == 1
    assert await fake_redis.llen(SYSTEM_RETRY_QUEUE_KEY) == 1
    assert await fake_redis.llen(RETRY_QUEUE_KEY) == 0

    receiver.default_code = 200
    processed = await system.drain(
        now=10_000_000_000 + BACKOFF_SCHEDULE[0] + 1,
    )
    assert processed == 1
    assert await system.retry_depth() == 0
    bodies = [item["body"] for item in receiver.received]
    assert all(b'"event_id": "evt_00000000000000000000000000000043"' in body for body in bodies)


@pytest.mark.asyncio
async def test_environment_requires_url_and_secret_as_one_operator_tuple(
    monkeypatch,
    receiver,
    fake_redis,
):
    monkeypatch.setenv("VEXA_SYSTEM_WEBHOOK_URL", SYSTEM_URL)
    monkeypatch.delenv("VEXA_SYSTEM_WEBHOOK_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="configured together"):
        build_system_webhook_from_env(fake_redis, transport=receiver)


@pytest.mark.asyncio
async def test_environment_rejects_nonfinite_timeout(
    monkeypatch,
    receiver,
    fake_redis,
):
    monkeypatch.setenv("VEXA_SYSTEM_WEBHOOK_URL", SYSTEM_URL)
    monkeypatch.setenv("VEXA_SYSTEM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("VEXA_SYSTEM_WEBHOOK_TIMEOUT_S", "NaN")
    with pytest.raises(RuntimeError, match="finite number"):
        build_system_webhook_from_env(fake_redis, transport=receiver)


@pytest.mark.asyncio
async def test_environment_freezes_the_operator_destination(
    monkeypatch,
    receiver,
    fake_redis,
):
    monkeypatch.setenv("VEXA_SYSTEM_WEBHOOK_URL", SYSTEM_URL)
    monkeypatch.setenv("VEXA_SYSTEM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("VEXA_SYSTEM_WEBHOOK_ALLOW_PRIVATE_HTTP", "true")
    sink = build_system_webhook_from_env(fake_redis, transport=receiver)
    assert sink is not None
    assert sink.url == SYSTEM_URL


def test_operator_url_rejects_credentials_query_and_fragment(receiver):
    for url in (
        "https://user:pass@system.example/hook",
        "https://system.example/hook?token=forbidden",
        "https://system.example/hook#fragment",
    ):
        with pytest.raises(ValueError, match="credentials, query or fragment"):
            SystemWebhookSink(
                url=url,
                secret=SECRET,
                transport=receiver,
            )


def test_user_payload_cannot_redirect_the_boot_frozen_system_destination(
    receiver,
):
    system = SystemWebhookSink(
        url=SYSTEM_URL,
        secret=SECRET,
        transport=receiver,
        allow_private_http=True,
    )
    envelope = build_envelope(
        "meeting.completed",
        {
            "meeting": {
                "id": 44,
                "data": {
                    "webhook_url": "https://attacker.example/redirect",
                },
            },
        },
        event_id="evt_00000000000000000000000000000044",
    )
    asyncio.run(system.deliver(envelope, label="meeting:44"))
    assert receiver.received[0]["url"] == SYSTEM_URL
