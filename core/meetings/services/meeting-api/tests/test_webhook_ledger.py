"""#841 — the per-user delivery ledger: real deliveries appear in Delivery History.

The bug (owner-witnessed, prod v0.12.15 walk): webhooks ARE delivered to the receiver, but the
dashboard's Delivery History showed none of them — the core reported every outcome (#815→#817) only
as a rotating ``logevent.v1`` system log, while the dashboard read a *different* store nothing wrote
real deliveries to. "Delivered 27 / Failed 0" was an artifact of stale rows.

The fix (point of introduction — the dispatcher): the lifecycle callback records each delivery
outcome into a per-user ledger, and ``GET /webhooks/deliveries`` serves it. These evals drive the
SAME app the production composition root builds (real ``WebhookSink`` → fake in-memory receiver, no
network), assert a real delivery lands in the ledger read surface with its #817 outcome, and hold
the P14 line (host only — never the URL or the secret).
"""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.webhooks import InMemoryDeliveryLedger, WebhookSink, build_delivery_record

# A resolver stub so the SSRF guard never touches DNS — hook.example resolves to a public IP.
_PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


def _seed(repo, *, session_uid, data, user_id=1):
    m = asyncio.run(repo.create_meeting(user_id=user_id, platform="google_meet",
                                        native_meeting_id="m1", data=data))
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=session_uid))
    return m


def _app(repo, receiver, ledger):
    sink = WebhookSink(transport=receiver, resolver=_PUBLIC)
    return create_app(meeting_repo=repo, webhook_sink=sink, delivery_ledger=ledger)


# ── the fix: a real delivery appears in the user-visible history ────────────────────────────────

def test_real_delivery_appears_in_delivery_history(goldens, receiver):
    """A real delivery (not the Test button) appears in GET /webhooks/deliveries, outcome+code."""
    repo, ledger = InMemoryMeetingRepo(), InMemoryDeliveryLedger()
    _seed(repo, session_uid="sess-uid", data={
        "webhook_url": "https://hook.example/x", "webhook_secret": "s3cr3t",
        "webhook_events": {"meeting.status_change": True},
    })
    client = TestClient(_app(repo, receiver, ledger))

    # Drive the FSM advance → the callback delivers meeting.status_change to the receiver (200)...
    r = client.post("/bots/internal/callback/lifecycle", json=goldens["joining"])
    assert r.status_code == 200, r.text
    assert len(receiver.received) == 1, "receiver should have gotten the real delivery"

    # ...and the SAME delivery is now queryable on the user-facing history surface.
    h = client.get("/webhooks/deliveries", headers={"X-User-Id": "1"})
    assert h.status_code == 200, h.text
    rows = h.json()["deliveries"]
    assert len(rows) == 1, f"the real delivery should be in Delivery History, got {rows}"
    row = rows[0]
    assert row["event_type"] == "meeting.status_change"
    assert row["outcome"] == "delivered"
    assert row["status_code"] == 200
    assert row["target_host"] == "hook.example"
    assert row["created_at"]

    # P14: host only — the URL and the secret NEVER land in a ledger row.
    blob = str(row).lower()
    assert "s3cr3t" not in blob
    assert "/x" not in row.get("target_host", "")
    assert "webhook_url" not in row and "webhook_secret" not in row


def test_suppressed_delivery_recorded_with_its_named_outcome(goldens, receiver):
    """A suppressed delivery (unsubscribed event) appears with its #817 outcome — no HTTP happened.

    The user asking "why didn't my webhook fire?" reads "suppressed", not silence."""
    repo, ledger = InMemoryMeetingRepo(), InMemoryDeliveryLedger()
    _seed(repo, session_uid="sess-uid", data={
        "webhook_url": "https://hook.example/x", "webhook_secret": "s3cr3t",
        # subscribes to completed only → meeting.status_change is SUPPRESSED
        "webhook_events": {"meeting.completed": True, "meeting.status_change": False},
    })
    client = TestClient(_app(repo, receiver, ledger))

    r = client.post("/bots/internal/callback/lifecycle", json=goldens["joining"])
    assert r.status_code == 200, r.text
    assert receiver.received == [], "a suppressed event must never reach the wire"

    h = client.get("/webhooks/deliveries", headers={"X-User-Id": "1"})
    rows = h.json()["deliveries"]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "suppressed"
    assert rows[0]["status_code"] is None


def test_delivery_history_is_owner_scoped(goldens, receiver):
    """The history is scoped to X-User-Id — another user never sees this user's deliveries."""
    repo, ledger = InMemoryMeetingRepo(), InMemoryDeliveryLedger()
    _seed(repo, session_uid="sess-uid", user_id=1, data={
        "webhook_url": "https://hook.example/x", "webhook_secret": "s3cr3t",
        "webhook_events": {"meeting.status_change": True},
    })
    client = TestClient(_app(repo, receiver, ledger))
    client.post("/bots/internal/callback/lifecycle", json=goldens["joining"])

    assert client.get("/webhooks/deliveries", headers={"X-User-Id": "1"}).json()["deliveries"]
    assert client.get("/webhooks/deliveries", headers={"X-User-Id": "2"}).json()["deliveries"] == []
    # No X-User-Id at all → empty (never leak another user's history to an unidentified caller).
    assert client.get("/webhooks/deliveries").json()["deliveries"] == []


# ── the ledger port itself (P14 guard is belt-and-braces, not only the caller's discipline) ──────

def test_ledger_sanitizes_url_and_secret_even_if_a_caller_passes_them():
    """Even a caller that shoves a url/secret into the record dict gets them stripped (P14)."""
    ledger = InMemoryDeliveryLedger()
    record = build_delivery_record(
        event_type="meeting.completed", event_id="evt_abc", target_host="hook.example",
        outcome="delivered", status_code=200, meeting_id=7,
    )
    record["webhook_url"] = "https://hook.example/secret-path?token=abc"  # a caller mistake
    record["webhook_secret"] = "s3cr3t"
    asyncio.run(ledger.record(1, record))
    rows = asyncio.run(ledger.list(1))
    assert len(rows) == 1
    assert "webhook_url" not in rows[0] and "webhook_secret" not in rows[0]
    assert rows[0]["target_host"] == "hook.example"


def test_ledger_is_newest_first_and_capped():
    ledger = InMemoryDeliveryLedger(max_per_user=3)
    for i in range(5):
        asyncio.run(ledger.record(1, build_delivery_record(
            event_type="meeting.status_change", event_id=f"evt_{i}",
            target_host="hook.example", outcome="delivered", status_code=200,
        )))
    rows = asyncio.run(ledger.list(1))
    assert len(rows) == 3  # capped
    assert [r["event_id"] for r in rows] == ["evt_4", "evt_3", "evt_2"]  # newest first
