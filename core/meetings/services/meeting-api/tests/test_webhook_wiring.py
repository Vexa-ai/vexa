"""Per-user webhook wiring — config rides on meeting.data; the lifecycle callback delivers.

The principled 0.12 path (vs main's monolith users-table read): identity owns the config; the gateway
forwards it; bot_spawn persists it into meeting.data; the lifecycle callback delivers the sealed
``meeting.status_change`` envelope via the injected WebhookSink — meeting-api never reads the users table.
"""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.service import request_bot
from meeting_api.webhooks import DeliveryResult


class _CaptureSink:
    """A WebhookSink stand-in that records each deliver() call."""

    def __init__(self):
        self.calls = []

    async def deliver(self, url, envelope, webhook_secret=None, *, scope="per-client",
                      events_config=None, label="", metadata=None):
        self.calls.append({
            "url": url, "event_type": envelope.get("event_type"),
            "secret": webhook_secret, "events_config": events_config,
        })
        return DeliveryResult(status="delivered", status_code=200)


# ── config storage (bot_spawn → meeting.data) ────────────────────────────────────────────────────

def test_request_bot_stores_webhook_in_meeting_data():
    repo, rt = InMemoryMeetingRepo(), FakeRuntimeClient()
    asyncio.run(request_bot(
        repo, rt, user_id=1, platform="google_meet", native_meeting_id="m1",
        webhook_url="https://hook.example/x", webhook_secret="s3cr3t",
        webhook_events={"meeting.status_change": True},
        redis_url="redis://r", token_secret="secret",
    ))
    m = asyncio.run(repo.find_active(1, "google_meet", "m1"))
    assert m["data"]["webhook_url"] == "https://hook.example/x"
    assert m["data"]["webhook_secret"] == "s3cr3t"
    assert m["data"]["webhook_events"] == {"meeting.status_change": True}


def test_request_bot_omits_webhook_when_unset():
    repo, rt = InMemoryMeetingRepo(), FakeRuntimeClient()
    asyncio.run(request_bot(
        repo, rt, user_id=1, platform="google_meet", native_meeting_id="m2",
        redis_url="redis://r", token_secret="secret",
    ))
    m = asyncio.run(repo.find_active(1, "google_meet", "m2"))
    assert "webhook_url" not in m["data"]


# ── delivery (lifecycle callback → WebhookSink) ──────────────────────────────────────────────────

def _seed(repo, *, session_uid, data):
    m = asyncio.run(repo.create_meeting(user_id=1, platform="google_meet", native_meeting_id="m1", data=data))
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=session_uid))
    return m


def test_status_change_webhook_delivered(goldens):
    repo, sink = InMemoryMeetingRepo(), _CaptureSink()
    _seed(repo, session_uid="sess-uid", data={
        "webhook_url": "https://hook.example/x", "webhook_secret": "s3cr3t",
        "webhook_events": {"meeting.status_change": True},
    })
    client = TestClient(create_app(meeting_repo=repo, webhook_sink=sink))
    r = client.post("/bots/internal/callback/lifecycle", json=goldens["joining"])
    assert r.status_code == 200, r.text
    assert sink.calls, "no webhook delivered on FSM advance"
    c = sink.calls[0]
    assert c["url"] == "https://hook.example/x"
    assert c["event_type"] == "meeting.status_change"
    assert c["secret"] == "s3cr3t"
    assert c["events_config"] == {"meeting.status_change": True}


def test_no_webhook_when_url_unconfigured(goldens):
    repo, sink = InMemoryMeetingRepo(), _CaptureSink()
    _seed(repo, session_uid="sess-uid", data={})  # no webhook_url on the meeting
    client = TestClient(create_app(meeting_repo=repo, webhook_sink=sink))
    r = client.post("/bots/internal/callback/lifecycle", json=goldens["joining"])
    assert r.status_code == 200, r.text
    assert not sink.calls
