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


# ── delivery OUTCOME is reported (#815) ──────────────────────────────────────────────────────────
# `WebhookSink.deliver` never raises: it RETURNS delivered|suppressed|blocked|failed|queued. That
# outcome used to be discarded, so a webhook the subscriber never received (unsubscribed event type,
# SSRF-blocked target, 4xx endpoint) looked exactly like one that arrived — "my webhooks stopped"
# was undiagnosable in production. Every outcome now emits one `webhook_delivery` logevent.

class _OutcomeSink:
    """A WebhookSink stand-in that returns a chosen DeliveryResult."""

    def __init__(self, result):
        self._result = result

    async def deliver(self, url, envelope, webhook_secret=None, *, scope="per-client",
                      events_config=None, label="", metadata=None):
        return self._result


def _delivery_logs(capsys):
    import json as _json

    out = []
    for line in capsys.readouterr().out.splitlines():
        try:
            rec = _json.loads(line)
        except ValueError:
            continue
        if rec.get("event") == "webhook_delivery":
            out.append(rec)
    return out


def _run_advance(repo, sink, goldens):
    client = TestClient(create_app(meeting_repo=repo, webhook_sink=sink))
    return client.post("/bots/internal/callback/lifecycle", json=goldens["joining"])


def test_delivered_outcome_is_logged(goldens, capsys):
    repo = InMemoryMeetingRepo()
    _seed(repo, session_uid="sess-uid", data={
        "webhook_url": "https://hook.example/x?token=SECRET-IN-URL",
        "webhook_events": {"meeting.status_change": True},
    })
    r = _run_advance(repo, _OutcomeSink(DeliveryResult(status="delivered", status_code=200)), goldens)
    assert r.status_code == 200, r.text
    logs = _delivery_logs(capsys)
    assert logs, "a delivered webhook emitted no webhook_delivery logevent"
    rec = logs[0]
    assert rec["fields"]["outcome"] == "delivered"
    assert rec["fields"]["event_type"] == "meeting.status_change"
    assert rec["fields"]["status_code"] == 200
    # The target is reported as HOST ONLY — a webhook URL can carry a secret in its path/query.
    assert rec["fields"]["target_host"] == "hook.example"
    assert "SECRET-IN-URL" not in _json_dumps(rec)


def test_silent_non_delivery_outcomes_are_logged_as_warnings(goldens, capsys):
    """suppressed (unsubscribed event) and blocked (SSRF) are the two silent killers in production."""
    for outcome, result in (
        ("suppressed", DeliveryResult(status="suppressed")),
        ("blocked", DeliveryResult(status="blocked", error="Webhook URL cannot target internal or private networks")),
        ("failed", DeliveryResult(status="failed", status_code=400, error="HTTP 400")),
    ):
        repo = InMemoryMeetingRepo()
        _seed(repo, session_uid="sess-uid", data={
            "webhook_url": "https://hook.example/x",
            "webhook_events": {"meeting.status_change": True},
        })
        r = _run_advance(repo, _OutcomeSink(result), goldens)
        assert r.status_code == 200, r.text
        logs = _delivery_logs(capsys)
        assert logs, f"{outcome} webhook emitted no webhook_delivery logevent — silent non-delivery"
        rec = logs[0]
        assert rec["fields"]["outcome"] == outcome
        assert rec["level"] == "warning", f"{outcome} must not be logged as a success"


def _json_dumps(rec):
    import json as _json

    return _json.dumps(rec)
