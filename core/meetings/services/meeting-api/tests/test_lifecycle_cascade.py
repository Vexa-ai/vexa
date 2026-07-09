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


def test_stop_cascade_tears_down_a_booting_bot_no_orphan():
    """Spawn → DELETE while the bot is still BOOTING (status `requested`): the stop route cascade marks
    `stopping`, publishes the graceful leave AND directly tears the workload down (ORPH1/B1), so a
    not-yet-subscribed bot can't orphan. Exercises bot_spawn → stop_router → runtime end to end."""
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

    # The meeting is `requested` (booting) — the bot has NOT subscribed to its leave channel yet.
    d = client.delete("/bots/google_meet/stop-cascade", headers={"x-user-id": str(USER)})
    assert d.status_code == 200, d.text
    assert d.json()["status"] == "stopping"

    # Graceful path: the leave command was published…
    assert any("leave" in msg for _ch, msg in publisher.published), "leave command must be published"
    # …AND the guarantee: a booting bot's workload is torn down directly → no orphan.
    assert workload_id in runtime.deleted, "a booting bot's workload must be directly torn down (ORPH1/B1)"
