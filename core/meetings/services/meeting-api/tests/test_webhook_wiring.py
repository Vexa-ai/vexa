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
            "envelope": envelope,
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


# ── typed events (webhook.v1 EventType parity) ───────────────────────────────────────────────────
# Each lifecycle transition still emits meeting.status_change; the mapped transitions ALSO emit the
# typed event the contract declares: active → meeting.started, completed → meeting.completed (the
# post-meeting `{meeting}` envelope), failed → bot.failed.

_ALL_EVENTS = {
    "meeting.status_change": True, "meeting.started": True,
    "meeting.completed": True, "bot.failed": True,
}


def _wired_client():
    repo, sink = InMemoryMeetingRepo(), _CaptureSink()
    _seed(repo, session_uid="sess-uid", data={
        "webhook_url": "https://hook.example/x", "webhook_secret": "s3cr3t",
        "webhook_events": dict(_ALL_EVENTS),
    })
    return TestClient(create_app(meeting_repo=repo, webhook_sink=sink)), sink


def _post(client, event):
    r = client.post("/bots/internal/callback/lifecycle", json=event)
    assert r.status_code == 200, r.text


def test_meeting_started_emitted_on_active(goldens):
    client, sink = _wired_client()
    _post(client, goldens["joining"])
    _post(client, goldens["active"])
    types = [c["event_type"] for c in sink.calls]
    assert types == ["meeting.status_change", "meeting.status_change", "meeting.started"]
    started = sink.calls[-1]["envelope"]
    sc = started["data"]["status_change"]
    assert sc["from"] == "joining" and sc["to"] == "active"
    assert sc["transition_source"] == "bot_callback"
    assert started["data"]["meeting"]["status"] == "active"


def test_meeting_completed_emitted_with_post_meeting_envelope(goldens):
    client, sink = _wired_client()
    _post(client, goldens["joining"])
    _post(client, goldens["active"])
    _post(client, goldens["completed-stopped"])
    types = [c["event_type"] for c in sink.calls]
    assert types[-2:] == ["meeting.status_change", "meeting.completed"]
    completed = sink.calls[-1]["envelope"]
    # The post-meeting envelope: data = {meeting} only (golden Envelope.meeting-completed.json) —
    # no status_change block, completion_reason hoisted, internal keys (webhook_secret) stripped.
    assert set(completed["data"].keys()) == {"meeting"}
    m = completed["data"]["meeting"]
    assert m["status"] == "completed"
    assert m["completion_reason"] == "stopped"
    assert "webhook_secret" not in m["data"]
    assert "webhook_url" not in m["data"]


def test_bot_failed_emitted_on_terminal_failure(goldens):
    client, sink = _wired_client()
    _post(client, goldens["joining"])
    _post(client, goldens["failed-join"])
    types = [c["event_type"] for c in sink.calls]
    assert types[-2:] == ["meeting.status_change", "bot.failed"]
    failed = sink.calls[-1]["envelope"]
    assert failed["data"]["meeting"]["status"] == "failed"
    sc = failed["data"]["status_change"]
    assert sc["to"] == "failed"
    assert sc["reason"] == "host denied admission"


def test_no_typed_event_on_intermediate_transition(goldens):
    """joining has no typed mapping — only meeting.status_change fires."""
    client, sink = _wired_client()
    _post(client, goldens["joining"])
    assert [c["event_type"] for c in sink.calls] == ["meeting.status_change"]


def test_typed_event_suppressed_by_real_event_filter(goldens):
    """With the REAL WebhookSink filter semantics, an unsubscribed typed event is suppressed —
    the emitter passes events_config through, and delivery.is_event_enabled opts in per type."""
    from meeting_api.webhooks import is_event_enabled

    cfg = {"meeting.status_change": True}  # user did NOT opt into meeting.started
    assert is_event_enabled(cfg, "meeting.status_change") is True
    assert is_event_enabled(cfg, "meeting.started") is False
    assert is_event_enabled(None, "meeting.completed") is True  # default-enabled set


def test_typed_builder_validates_against_sealed_schema(goldens):
    """build_typed_envelope conforms every envelope to webhook.v1#/$defs/Envelope at the seam;
    intermediate transitions return None."""
    from meeting_api.lifecycle import LifecycleSink, MeetingStore, TransitionSource
    from meeting_api.lifecycle.webhook import build_typed_envelope, typed_event_type

    sink = LifecycleSink(store=MeetingStore())
    ch = sink.apply_change(goldens["joining"], transition_source=TransitionSource.BOT_CALLBACK)
    assert typed_event_type(ch) is None and build_typed_envelope(ch) is None
    ch = sink.apply_change(goldens["active"], transition_source=TransitionSource.BOT_CALLBACK)
    env = build_typed_envelope(ch)  # raises if it does not conform to the sealed Envelope shape
    assert env["event_type"] == "meeting.started"
    ch = sink.apply_change(goldens["completed-stopped"], transition_source=TransitionSource.BOT_CALLBACK)
    env = build_typed_envelope(ch)
    assert env["event_type"] == "meeting.completed"
    assert set(env["data"].keys()) == {"meeting"}


# ── SYSTEM post-meeting hooks (POST_MEETING_HOOKS) ───────────────────────────────────────────────

def _system_wired_client(*, with_user_hook=False):
    """A stack with system hooks configured (the hosted billing wiring) and a per-user hook
    only when asked — proving system delivery is independent of any user webhook config."""
    repo = InMemoryMeetingRepo()
    user_sink, system_sink = _CaptureSink(), _CaptureSink()
    data = {}
    if with_user_hook:
        data = {"webhook_url": "https://hook.example/x", "webhook_secret": "s3cr3t",
                "webhook_events": dict(_ALL_EVENTS)}
    _seed(repo, session_uid="sess-uid", data=data)
    app = create_app(
        meeting_repo=repo, webhook_sink=user_sink,
        system_hooks=["http://webapp:3000/api/hooks/meeting-completed"],
        system_hook_secret="internal-secret",
        system_webhook_sink=system_sink,
    )
    return TestClient(app), user_sink, system_sink


def test_system_hook_fires_on_completion_only(goldens):
    client, _user, system = _system_wired_client()
    _post(client, goldens["joining"])
    _post(client, goldens["active"])
    assert system.calls == []                       # nothing before completion
    _post(client, goldens["completed-stopped"])
    assert len(system.calls) == 1
    c = system.calls[0]
    assert c["url"] == "http://webapp:3000/api/hooks/meeting-completed"
    assert c["event_type"] == "meeting.completed"
    assert c["secret"] == "internal-secret"         # signed with INTERNAL_API_SECRET
    m = c["envelope"]["data"]["meeting"]
    assert m["status"] == "completed"
    assert m["user_id"] == 1                        # billing resolves the email from this
    assert m["start_time"] and m["end_time"]        # billing derives duration from these


def test_system_hook_independent_of_user_hook(goldens):
    """The system hook fires even when the user configured their own webhook, and the user's
    endpoint never receives the system delivery (separate sinks, separate scopes)."""
    client, user, system = _system_wired_client(with_user_hook=True)
    _post(client, goldens["joining"])
    _post(client, goldens["active"])
    _post(client, goldens["completed-stopped"])
    assert len(system.calls) == 1
    assert all(c["url"] != "http://webapp:3000/api/hooks/meeting-completed" for c in user.calls)
    assert any(c["event_type"] == "meeting.completed" for c in user.calls)


def test_system_scope_skips_ssrf_guard():
    """scope='system' targets are operator config — cluster-internal URLs must deliver, not be
    blocked by the SSRF guard that protects the per-client path."""
    from meeting_api.webhooks import WebhookSink

    delivered = []

    async def transport(url, body, headers):
        delivered.append((url, body, headers))
        class R:  # noqa: N801 — minimal response stub
            status_code = 200
        return R()

    sink = WebhookSink(transport)
    env = {"event_id": "evt_x", "event_type": "meeting.completed",
           "api_version": "2026-03-01", "created_at": "2026-01-01T00:00:00Z",
           "data": {"meeting": {"id": 1}}}
    per_client = asyncio.run(sink.deliver("http://10.0.0.5:3000/hook", env, "s"))
    assert per_client.status == "blocked"           # the guard still protects user URLs
    system = asyncio.run(sink.deliver("http://10.0.0.5:3000/hook", env, "s", scope="system"))
    assert system.status == "delivered"
    assert delivered and delivered[0][2]["X-Webhook-Signature"].startswith("sha256=")
