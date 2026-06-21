"""O-RT-2 retry-callback eval — durable RuntimeEvent delivery. A receiver that 500s twice then 200s is
retried (across sweeps) until it acks. Replaces the old fire-once POST.

Two layers:
  • CallbackQueue directly — a fake poster returns 500, 500, 200; the event stays queued until acked.
  • over the API — a FastAPI TestClient receiver that 500s twice then 200s receives the lifecycle
    event after enough sweeps, and the queue drains.
"""
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from runtime_kernel import CallbackQueue, Runtime
from runtime_kernel.api import create_app


def test_queue_retries_until_ack():
    codes = iter([500, 500, 200])
    posted = []

    def poster(url, payload, headers):
        posted.append((url, payload))
        return next(codes)

    q = CallbackQueue(poster=poster)
    q.enqueue("http://receiver/cb", {"workloadId": "w1", "state": "stopped"})

    # First attempt (in enqueue) got 500 → still pending.
    assert q.pending_count() == 1
    # Sweep → 500 again → still pending.
    assert q.sweep() == 1
    # Sweep → 200 → drained.
    assert q.sweep() == 0
    assert len(posted) == 3
    assert q.pending_count() == 0


def test_queue_gives_up_at_max_attempts():
    def always_500(url, payload, headers):
        return 500

    q = CallbackQueue(poster=always_500, max_attempts=2)
    q.enqueue("http://receiver/cb", {"workloadId": "w1"})  # attempt 1 → 500
    assert q.pending_count() == 1
    assert q.sweep() == 0  # attempt 2 → 500 → cap reached → dropped
    assert q.pending_count() == 0


def test_api_delivers_lifecycle_event_durably():
    # A receiver app that 500s its first two calls, then 200s.
    receiver = FastAPI()
    state = {"calls": 0, "received": []}

    @receiver.post("/runtime/callback")
    async def cb(req: Request):
        state["calls"] += 1
        body = await req.json()
        if state["calls"] <= 2:
            from fastapi.responses import JSONResponse
            return JSONResponse({"ok": False}, status_code=500)
        state["received"].append(body)
        return {"ok": True}

    receiver_client = TestClient(receiver)

    # Poster routes through the in-process receiver TestClient.
    def poster(url, payload, headers):
        return receiver_client.post("/runtime/callback", json=payload).status_code

    queue = CallbackQueue(poster=poster)
    rt = Runtime(profiles={"test": ["sleep", "30"]}, grace_sec=2.0)
    app = create_app(rt, callback_queue=queue)
    client = TestClient(app)

    # Create with a callbackUrl → emits starting+running events; deliveries 500 (attempts 1,2).
    r = client.post(
        "/workloads",
        json={"workloadId": "w1", "profile": "test", "env": {}, "callbackUrl": "http://receiver/runtime/callback"},
    )
    assert r.status_code == 201

    # At least one event is still pending (the 500s).
    assert queue.pending_count() >= 1
    assert state["received"] == []  # nothing acked yet

    # Sweep until the receiver starts acking (3rd call onward → 200) and the queue drains.
    for _ in range(10):
        if queue.sweep() == 0:
            break
    assert queue.pending_count() == 0
    assert len(state["received"]) >= 1  # the lifecycle event was durably delivered

    client.post("/workloads/w1/stop")  # cleanup child process
