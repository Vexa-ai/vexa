"""O-MTG-2 eval (delivery + retry) — the WebhookSink port against a fake receiver.

Asserts: 200 → delivered; 500 → enqueued → retry-worker sweep drains → delivered; an
unsubscribed per-client event is suppressed (no HTTP); a system-scope event ignores the
per-client filter; the delivered body verifies under the receiver's recomputed HMAC; the
retry queue respects `next_retry_at` (not-due entries are left in place).
"""
from __future__ import annotations

import pytest

from meeting_api.webhooks import (
    BACKOFF_SCHEDULE,
    RetryQueue,
    WebhookSink,
    build_envelope,
    drain_retry_queue,
    verify_signature,
)

SECRET = "whsec_demo_secret"
URL = "https://hooks.example.com/vexa"

# A per-client config that subscribes to meeting.completed but NOT meeting.status_change.
SUBSCRIBED = {"meeting.completed": True, "meeting.status_change": False}

# A resolver stub so the SSRF guard never touches DNS for example.com (public IP).
_PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


async def test_200_is_delivered(receiver):
    sink = WebhookSink(transport=receiver, resolver=_PUBLIC)
    env = build_envelope("meeting.completed", {"meeting": {"id": 1}})
    result = await sink.deliver(URL, env, SECRET, events_config=SUBSCRIBED)
    assert result.status == "delivered"
    assert result.status_code == 200
    assert len(receiver.received) == 1
    # The receiver can verify the delivered body under the shared secret.
    rec = receiver.received[0]
    assert verify_signature(rec["body"], rec["headers"], SECRET)


async def test_unsubscribed_event_suppressed(receiver):
    """A per-client event the subscriber didn't opt into never reaches the wire."""
    sink = WebhookSink(transport=receiver, resolver=_PUBLIC)
    env = build_envelope("meeting.status_change", {"meeting": {"id": 1}})
    result = await sink.deliver(URL, env, SECRET, events_config=SUBSCRIBED)
    assert result.status == "suppressed"
    assert receiver.received == []  # no HTTP happened


async def test_system_scope_ignores_event_filter(receiver):
    """System hooks (billing/analytics) bypass the per-client subscription filter."""
    sink = WebhookSink(transport=receiver, resolver=_PUBLIC)
    env = build_envelope("meeting.status_change", {"meeting": {"id": 1}})
    result = await sink.deliver(URL, env, SECRET, scope="system", events_config=None)
    assert result.status == "delivered"


async def test_500_enqueues_then_worker_drains(receiver, fake_redis):
    """500 → enqueued → a single worker sweep (clock advanced) drains → delivered."""
    queue = RetryQueue(fake_redis)
    sink = WebhookSink(transport=receiver, queue=queue, resolver=_PUBLIC)

    # The first delivery attempt fails 500 → routed to the retry queue.
    receiver.default_code = 500
    env = build_envelope("meeting.completed", {"meeting": {"id": 7}})
    result = await sink.deliver(URL, env, SECRET, events_config=SUBSCRIBED, label="m=7")
    assert result.status == "queued" and result.queued
    assert await queue.depth() == 1

    # The receiver now recovers (200). A worker sweep with the clock past the first
    # backoff drains the queue and delivers.
    receiver.default_code = 200
    later = 10_000_000_000 + BACKOFF_SCHEDULE[0] + 1  # well past next_retry_at
    processed = await drain_retry_queue(fake_redis, receiver, now=later)
    assert processed == 1
    assert await queue.depth() == 0
    # The redelivered body still verifies (headers rebuilt with a fresh ts).
    redelivered = receiver.received[-1]
    assert verify_signature(redelivered["body"], redelivered["headers"], SECRET)


async def test_retry_entry_not_due_is_left_in_place(receiver, fake_redis):
    """An entry whose next_retry_at hasn't arrived is re-queued untouched (no delivery)."""
    queue = RetryQueue(fake_redis)
    sink = WebhookSink(transport=receiver, queue=queue, resolver=_PUBLIC)
    receiver.default_code = 500
    env = build_envelope("meeting.completed", {"meeting": {"id": 9}})
    await sink.deliver(URL, env, SECRET, events_config=SUBSCRIBED)

    receiver.received.clear()
    receiver.default_code = 200
    # Sweep at a clock BEFORE next_retry_at → nothing delivered, still queued.
    processed = await drain_retry_queue(fake_redis, receiver, now=0.0)
    assert processed == 0
    assert receiver.received == []
    assert await queue.depth() == 1


async def test_permanent_failure_exhausts_schedule(receiver, fake_redis):
    """A target that 500s forever drops out of the queue after the backoff schedule."""
    queue = RetryQueue(fake_redis)
    sink = WebhookSink(transport=receiver, queue=queue, resolver=_PUBLIC)
    receiver.default_code = 500
    env = build_envelope("meeting.completed", {"meeting": {"id": 11}})
    await sink.deliver(URL, env, SECRET, events_config=SUBSCRIBED)

    base = 10_000_000_000
    # Sweep repeatedly, each time advancing well past the next backoff, until drained.
    clock = base + 100_000
    for _ in range(len(BACKOFF_SCHEDULE) + 3):
        if await queue.depth() == 0:
            break
        clock += 100_000  # always past next_retry_at
        await drain_retry_queue(fake_redis, receiver, now=clock)
    assert await queue.depth() == 0  # exhausted → dropped, not stuck forever
