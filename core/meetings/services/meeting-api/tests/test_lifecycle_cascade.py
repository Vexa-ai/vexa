"""DEEP INTEGRATION CASCADE — one meeting driven through the REAL meeting-api app, asserting the full
multi-module chain fires coherently end to end (not just each seam in isolation).

  POST /bots ─bot_spawn─► (workload spawned, session created)
            ─lifecycle callback: joining → active → completed─►
                ├─ sessions/repo: the DB status is durably persisted (rehydrate-safe)
                ├─ webhooks:      each advance delivers a sealed meeting.status_change (HMAC-signed) to the
                │                 per-user URL — through the SAME event-filter the gateway config drives
                └─ ws fan-out:    each advance publishes a 0.10.6 meeting.status frame to bm:{id}:status

Everything runs over ``meeting_api.create_app`` (the SHIPPED handlers of every module) via TestClient,
faked only at the transports (runtime / redis / webhook). This is the L3-lite seam that proves the
modules INTEGRATE — a regression in any hop (persist, deliver, publish) breaks the cascade, not just a
unit test. The recording leg is covered separately (test_recordings) since it carries its own repo.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.webhooks import WebhookSink

USER = 7
SECRET = "test-admin-token"
HOOK_URL = "https://hooks.example.test/vexa"


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)  # POST /bots mints a MeetingToken signed with this


class _RecordingRedis:
    """Captures every ws publish (channel, decoded frame)."""

    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    async def publish(self, channel: str, data: str):
        self.published.append((channel, json.loads(data)))
        return 1


class _CapturingWebhookTransport:
    """A WebhookSink transport that records each delivery (url, decoded body, headers) → 200."""

    def __init__(self):
        self.deliveries: list[dict] = []

    async def __call__(self, url: str, body: bytes, headers: dict):
        self.deliveries.append({
            "url": url,
            "body": json.loads(body),
            "headers": {k.lower(): v for k, v in headers.items()},
        })

        class _Resp:
            status_code = 200

        return _Resp()


def test_full_meeting_lifecycle_cascade():
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    redis = _RecordingRedis()
    webhook = _CapturingWebhookTransport()
    # Stub DNS to a PUBLIC ip so the SSRF guard (WH2: resolves + pins) passes for the test host without
    # any real DNS — the cascade is about delivery wiring, not the SSRF guard (covered in test_webhook_ssrf).
    client = TestClient(create_app(
        meeting_repo=repo, runtime=runtime, redis=redis,
        webhook_sink=WebhookSink(webhook, resolver=lambda host: ["93.184.216.34"]),
        token_secret=SECRET,
    ))

    # ── 1. spawn through the real front door, with a per-user webhook that opts in to status_change ──
    r = client.post(
        "/bots",
        headers={
            "x-user-id": str(USER),
            "x-user-webhook-url": HOOK_URL,
            "x-user-webhook-secret": "whsec",
            "x-user-webhook-events": json.dumps({"meeting.status_change": True}),
        },
        json={"platform": "google_meet", "native_meeting_id": "cascade-1"},
    )
    assert r.status_code == 201, r.text
    meeting_id = r.json()["id"]
    assert r.json()["status"] == "requested"
    assert len(runtime.specs) == 1, "the spawn must have created exactly one workload"

    conn = asyncio.run(repo.list_sessions(meeting_id=meeting_id))[-1]

    # ── 2. drive the bot lifecycle joining → active → completed ──
    for st in ("joining", "active", "completed"):
        ev = {"connection_id": conn, "status": st}
        if st == "completed":
            ev["exit_code"] = 0
            ev["completion_reason"] = "stopped"
        rr = client.post("/bots/internal/callback/lifecycle", json=ev)
        assert rr.status_code == 200, f"{st}: {rr.text}"

    # ── 3. durable persist (sessions/repo) — the FSM advance reached the DB row ──
    assert asyncio.run(repo.get_status_by_session(session_uid=conn)) == "completed"

    # ── 4. ws fan-out — a meeting.status frame per advance, in order, on the 0.10.6 channel ──
    frames = [p for ch, p in redis.published if ch == f"bm:meeting:{meeting_id}:status"]
    assert [f["payload"]["status"] for f in frames] == ["joining", "active", "completed"]
    assert all(f["type"] == "meeting.status" and f["meeting"]["id"] == meeting_id for f in frames)

    # ── 5. webhook delivery — each advance delivered a signed meeting.status_change to the per-user URL ──
    status_deliveries = [d for d in webhook.deliveries
                         if d["body"]["event_type"] == "meeting.status_change"]
    assert len(status_deliveries) == 3, \
        f"expected 3 status_change deliveries, got {len(status_deliveries)}"
    for d in status_deliveries:
        assert d["url"] == HOOK_URL
        assert "x-webhook-signature" in d["headers"], "delivery must be HMAC-signed"
    last = status_deliveries[-1]["body"]
    assert last["data"]["status_change"]["new_status"] == "completed" or \
        last["data"]["meeting"]["status"] == "completed", f"terminal payload: {last['data']}"

    # ── 5b. TYPED events ride alongside, per the user's event filter: this user opted into
    # meeting.status_change only, so meeting.started is SUPPRESSED by the filter, while
    # meeting.completed (the default-enabled event) delivers with the post-meeting envelope.
    typed_deliveries = [d for d in webhook.deliveries
                        if d["body"]["event_type"] != "meeting.status_change"]
    assert [d["body"]["event_type"] for d in typed_deliveries] == ["meeting.completed"]
    completed = typed_deliveries[0]["body"]
    assert completed["data"]["meeting"]["status"] == "completed"
    assert "status_change" not in completed["data"], "post-meeting envelope carries {meeting} only"
    assert "x-webhook-signature" in typed_deliveries[0]["headers"]

    # ── 6. the in-process envelope log mirrors the deliveries exactly (one per REAL advance) ──
    # (proves no double-count on the cascade — the no_op guard held)
    envelopes = client.app.state.status_change_webhooks
    assert len(envelopes) == 3


def test_stop_cascade_waits_for_durable_withdrawal_before_workload_delete():
    """Spawn → DELETE while the bot is still PRE-ACTIVE: publish leave and persist the withdrawal
    request, but do not race the bot's platform cancellation with direct workload deletion.

    The pre-#839 path fails this discriminator because the route calls ``delete_workload`` in the
    same request, before any bot callback can durably acknowledge that the pending knock was
    withdrawn.
    """
    from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher

    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    publisher = InMemoryCommandPublisher()
    client = TestClient(create_app(
        meeting_repo=repo, runtime=runtime, command_publisher=publisher, token_secret=SECRET,
    ))

    r = client.post("/bots", headers={"x-user-id": str(USER)},
                    json={"platform": "google_meet", "native_meeting_id": "stop-cascade"})
    assert r.status_code == 201, r.text
    workload_id = runtime.specs[0]["workloadId"]

    session_uid = repo.sessions[0]["session_uid"]
    for status in ("joining", "awaiting_admission"):
        progressed = client.post("/bots/internal/callback/lifecycle", json={
            "connection_id": session_uid, "container_id": workload_id, "status": status,
        })
        assert progressed.status_code == 200, progressed.text

    # `awaiting_admission` proves the bot subscribed before entering the lobby and that the host-side
    # knock may exist. This is the status that must handshake rather than delete immediately.
    d = client.delete("/bots/google_meet/stop-cascade", headers={"x-user-id": str(USER)})
    assert d.status_code == 200, d.text
    assert d.json()["status"] == "stopping"

    # Graceful path: the leave command was published…
    assert any("leave" in msg for _ch, msg in publisher.published), "leave command must be published"
    # The workload remains until a durable withdrawal acknowledgement (or the typed bounded timeout)
    # wins the outcome race. Direct deletion here abandons the host-visible knock.
    assert workload_id not in runtime.deleted, (
        "pre-active Stop must not delete the workload before withdrawal is durably acknowledged"
    )
    row = repo._meetings[1]
    assert row["data"]["withdrawal"]["status"] == "pending"


def test_pre_active_withdrawal_ack_is_persisted_before_exactly_one_delete():
    """A2: the bot's typed acknowledgement wins the durable CAS before runtime teardown."""
    from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher, enforce_withdrawal_timeout

    order: list[str] = []

    class OrderedRepo(InMemoryMeetingRepo):
        async def finalize_withdrawal(self, **kwargs):
            row = await super().finalize_withdrawal(**kwargs)
            if row is not None:
                order.append(f"persist:{kwargs['outcome']['status']}")
            return row

    class OrderedRuntime(FakeRuntimeClient):
        async def delete_workload(self, workload_id):
            order.append(f"delete:{workload_id}")
            await super().delete_workload(workload_id)

    repo = OrderedRepo()
    runtime = OrderedRuntime()
    client = TestClient(create_app(
        meeting_repo=repo,
        runtime=runtime,
        command_publisher=InMemoryCommandPublisher(),
        token_secret=SECRET,
    ))
    created = client.post(
        "/bots",
        headers={"x-user-id": str(USER)},
        json={"platform": "google_meet", "native_meeting_id": "withdraw-order"},
    )
    assert created.status_code == 201, created.text
    session_uid = repo.sessions[0]["session_uid"]
    workload_id = runtime.specs[0]["workloadId"]
    for status in ("joining", "awaiting_admission"):
        r = client.post("/bots/internal/callback/lifecycle", json={
            "connection_id": session_uid, "container_id": workload_id, "status": status,
        })
        assert r.status_code == 200, r.text

    stopped = client.delete(
        "/bots/google_meet/withdraw-order", headers={"x-user-id": str(USER)}
    )
    assert stopped.status_code == 200, stopped.text
    assert runtime.deleted == []

    ack = client.post("/bots/internal/callback/lifecycle", json={
        "connection_id": session_uid,
        "container_id": workload_id,
        "status": "failed",
        "failure_stage": "awaiting_admission",
        "completion_reason": "stopped",
        "reason": "stopped while awaiting admission (withdrew the join request)",
        "exit_code": 0,
        "withdrawal": {
            "status": "completed",
            "completed_at": "2026-07-24T08:45:00Z",
            "duration_ms": 413,
            "cancel_attempted": True,
            "page_closed": True,
        },
    })
    assert ack.status_code == 200, ack.text
    assert order == [f"persist:completed", f"delete:{workload_id}"]
    assert runtime.deleted == [workload_id]
    durable = repo._meetings[1]["data"]["withdrawal"]
    assert durable["status"] == "completed"
    assert durable["page_closed"] is True
    assert durable["duration_ms"] == 413
    assert durable["acknowledged_at"].endswith("Z")

    # A late timeout loses the same CAS and cannot overwrite success or delete twice.
    won = asyncio.run(enforce_withdrawal_timeout(
        repo,
        runtime,
        session_uid=session_uid,
        meeting_id=1,
        workload_id=workload_id,
    ))
    assert won is False
    assert runtime.deleted == [workload_id]
    assert repo._meetings[1]["data"]["withdrawal"]["status"] == "completed"


def test_missing_ack_persists_typed_timeout_before_bounded_fallback_delete():
    """A3: exercise the deadline action directly — deterministic clock, no sleep-based proof."""
    from datetime import datetime, timezone

    from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher, enforce_withdrawal_timeout

    order: list[str] = []

    class OrderedRepo(InMemoryMeetingRepo):
        async def finalize_withdrawal(self, **kwargs):
            row = await super().finalize_withdrawal(**kwargs)
            if row is not None:
                order.append(f"persist:{kwargs['outcome']['status']}")
            return row

    class OrderedRuntime(FakeRuntimeClient):
        async def delete_workload(self, workload_id):
            order.append(f"delete:{workload_id}")
            await super().delete_workload(workload_id)

    repo = OrderedRepo()
    runtime = OrderedRuntime()
    client = TestClient(create_app(
        meeting_repo=repo,
        runtime=runtime,
        command_publisher=InMemoryCommandPublisher(),
        token_secret=SECRET,
    ))
    created = client.post(
        "/bots",
        headers={"x-user-id": str(USER)},
        json={"platform": "google_meet", "native_meeting_id": "withdraw-timeout"},
    )
    session_uid = repo.sessions[0]["session_uid"]
    workload_id = runtime.specs[0]["workloadId"]
    for status in ("joining", "awaiting_admission"):
        progressed = client.post("/bots/internal/callback/lifecycle", json={
            "connection_id": session_uid, "container_id": workload_id, "status": status,
        })
        assert progressed.status_code == 200, progressed.text
    stopped = client.delete(
        "/bots/google_meet/withdraw-timeout", headers={"x-user-id": str(USER)}
    )
    assert stopped.status_code == 200, stopped.text
    assert runtime.deleted == []

    won = asyncio.run(enforce_withdrawal_timeout(
        repo,
        runtime,
        session_uid=session_uid,
        meeting_id=1,
        workload_id=workload_id,
        timed_out_at=datetime(2026, 7, 24, 8, 45, 25, tzinfo=timezone.utc),
    ))
    assert won is True
    assert order == [f"persist:timed_out", f"delete:{workload_id}"]
    timeout = repo._meetings[1]["data"]["withdrawal"]
    assert timeout["status"] == "timed_out"
    assert timeout["timed_out_at"] == "2026-07-24T08:45:25Z"
    assert "not persisted" in timeout["reason"]


def test_active_stop_keeps_existing_leave_finalize_path():
    """A4: an admitted bot gets no withdrawal carrier and no direct workload delete."""
    from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher

    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    publisher = InMemoryCommandPublisher()
    client = TestClient(create_app(
        meeting_repo=repo, runtime=runtime, command_publisher=publisher, token_secret=SECRET,
    ))
    created = client.post(
        "/bots",
        headers={"x-user-id": str(USER)},
        json={"platform": "google_meet", "native_meeting_id": "active-stop-control"},
    )
    session_uid = repo.sessions[0]["session_uid"]
    workload_id = runtime.specs[0]["workloadId"]
    for status in ("joining", "awaiting_admission", "active"):
        r = client.post("/bots/internal/callback/lifecycle", json={
            "connection_id": session_uid, "container_id": workload_id, "status": status,
        })
        assert r.status_code == 200, r.text

    stopped = client.delete(
        "/bots/google_meet/active-stop-control", headers={"x-user-id": str(USER)}
    )
    assert stopped.status_code == 200, stopped.text
    assert runtime.deleted == []
    assert "withdrawal" not in repo._meetings[1]["data"]
    assert repo._meetings[1]["status"] == "stopping"
    assert any("leave" in body for _channel, body in publisher.published)
