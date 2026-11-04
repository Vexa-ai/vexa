"""Meetings run without Flows (ADR-0052 §3, revised 4 Nov 2026: Flows is a dependency for scheduling
only). With Flows stopped — nothing listens, every connection is refused — and with Flows hung — it
accepts the connection and never answers — an ad hoc bot is still spawned (``POST /bots``, fake
runtime), every lifecycle callback a bot makes completes without waiting on Flows (well under the
2 s publish bound, ``events.TIMEOUT_S``), its webhooks still fire, and the live transcript still
reads. The ``meeting.started`` / ``meeting.completed`` facts are handed to Flows in the background
(``events.publish_in_background``), best-effort: post-meeting work waits for Flows, the meeting does
not. No docker, no network beyond loopback sockets this file opens itself.
"""
from __future__ import annotations

import asyncio
import socket
import time

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api import events as events_mod
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.webhooks import DeliveryResult

USER = 7
HEADERS = {"x-user-id": str(USER)}
#: "Well under" the publish bound: a callback that waited on a hung Flows would take TIMEOUT_S.
FAST_S = 0.5
EVENTS = {"meeting.status_change": True, "meeting.started": True, "meeting.completed": True}


class _CaptureSink:
    def __init__(self):
        self.calls = []

    async def deliver(self, url, envelope, webhook_secret=None, *, scope="per-client",
                      events_config=None, label="", metadata=None):
        self.calls.append(envelope.get("event_type"))
        return DeliveryResult(status="delivered", status_code=200)


@pytest.fixture(params=["stopped", "hung"])
def flows_down(request, monkeypatch):
    """``VEXA_FLOWS_API_URL`` pointed at a Flows that is down, in one of two ways."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    if request.param == "stopped":
        sock.close()                    # the port is free again: a connection is refused
    else:
        sock.listen(8)                  # accepted into the backlog, never answered
    monkeypatch.setenv("VEXA_FLOWS_API_URL", f"http://{host}:{port}")
    monkeypatch.setenv("VEXA_FLOWS_API_KEY", "op-key")
    monkeypatch.setattr(events_mod, "TIMEOUT_S", 2.0)
    yield request.param
    if request.param == "hung":
        sock.close()


def _timed_post(client, path, **kw):
    started = time.monotonic()
    r = client.post(path, **kw)
    return r, time.monotonic() - started


def test_post_bots_spawns_with_flows_down(flows_down, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    client = TestClient(create_app(meeting_repo=repo, runtime=runtime))
    r, took = _timed_post(client, "/bots", headers=HEADERS,
                          json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "requested"
    assert len(runtime.specs) == 1, "the bot's workload was not started"
    assert took < FAST_S, f"POST /bots took {took:.2f}s with Flows {flows_down}"


def test_lifecycle_callbacks_complete_and_webhooks_fire_with_flows_down(flows_down):
    repo, sink = InMemoryMeetingRepo(), _CaptureSink()
    m = asyncio.run(repo.create_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="flows-down",
        data={"webhook_url": "https://hook.example/x", "webhook_secret": "s3cr3t",
              "webhook_events": dict(EVENTS)}))
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid="sess-down"))
    client = TestClient(create_app(open_callbacks=True, meeting_repo=repo, webhook_sink=sink))

    for body in (
        {"connection_id": "sess-down", "status": "joining", "timestamp": "2026-11-04T10:00:00Z"},
        {"connection_id": "sess-down", "status": "active", "timestamp": "2026-11-04T10:00:10Z"},
        {"connection_id": "sess-down", "status": "completed", "completion_reason": "stopped",
         "timestamp": "2026-11-04T10:30:00Z"},
    ):
        r, took = _timed_post(client, "/bots/internal/callback/lifecycle", json=body)
        assert r.status_code == 200, r.text
        assert took < FAST_S, (f"the {body['status']} callback took {took:.2f}s with Flows "
                               f"{flows_down}: it waited on the publish")

    assert asyncio.run(repo.get_meeting(m["id"]))["status"] == "completed"
    assert sink.calls.count("meeting.status_change") == 3
    assert "meeting.started" in sink.calls and "meeting.completed" in sink.calls


def test_the_live_transcript_reads_with_flows_down(flows_down):
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij")
    client = TestClient(create_app(transcript_store=store))
    started = time.monotonic()
    r = client.get("/transcripts/google_meet/abc-defg-hij", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert time.monotonic() - started < FAST_S


# ── the background publish itself ───────────────────────────────────────────────────────────────
@pytest.fixture(autouse=False)
def fresh_backlog(monkeypatch):
    """This test's own in-flight set and drop counters, so a task another test's event loop left
    behind is not counted."""
    monkeypatch.setattr(events_mod, "_in_flight", set())
    monkeypatch.setattr(events_mod, "_dropped_total", 0)
    monkeypatch.setattr(events_mod, "_dropped_unreported", 0)
    monkeypatch.setattr(events_mod, "_last_report", float("-inf"))


async def test_a_background_publish_is_held_until_it_ends_and_the_caller_does_not_wait(
        fresh_backlog):
    gate = asyncio.Event()
    landed = []

    async def slow_publish(*args):
        await gate.wait()
        landed.append(args)
        return True

    started = time.monotonic()
    task = events_mod.publish_in_background(slow_publish, 1, "native", "google_meet", 7)
    assert time.monotonic() - started < 0.05, "publish_in_background waited on the publish"
    assert task in events_mod._in_flight, "the task is not held: it could be garbage-collected"
    gate.set()
    await task
    assert landed == [(1, "native", "google_meet", 7)]
    assert task not in events_mod._in_flight, "an ended publish is still held"


async def test_a_background_publish_is_bounded_and_swallows_its_failure(monkeypatch, fresh_backlog):
    monkeypatch.setattr(events_mod, "TIMEOUT_S", 0.05)
    monkeypatch.setattr(events_mod, "_GRACE_S", 0.05)

    async def never_ends(*args):
        await asyncio.Event().wait()

    async def raises(*args):
        raise RuntimeError("flows is down")

    hung = events_mod.publish_in_background(never_ends)
    failed = events_mod.publish_in_background(raises)
    await asyncio.wait_for(asyncio.gather(hung, failed), timeout=2.0)
    assert hung.exception() is None and failed.exception() is None
    assert not events_mod._in_flight


def _drop_lines(capsys) -> list:
    import json

    lines = []
    for line in capsys.readouterr().out.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("event") == "flows_publish_dropped":
            lines.append(entry["fields"])
    return lines


async def test_past_the_in_flight_bound_drops_are_counted_as_a_metric(monkeypatch, fresh_backlog,
                                                                      capsys):
    """Every drop is counted (``publish_backlog``), and the count is reported as a metric line
    (``flows_publish_dropped``) at most once per ``DROP_REPORT_S`` — not one warning per episode."""
    monkeypatch.setattr(events_mod, "MAX_IN_FLIGHT", 2)
    clock = [1000.0]
    monkeypatch.setattr(events_mod, "_now", lambda: clock[0])
    gate = asyncio.Event()

    async def waits(*args):
        await gate.wait()

    held = [events_mod.publish_in_background(waits) for _ in range(2)]
    assert all(t is not None for t in held)
    assert [events_mod.publish_in_background(waits) for _ in range(5)] == [None] * 5
    assert events_mod.publish_backlog() == {"in_flight": 2, "max_in_flight": 2, "dropped_total": 5}
    assert [f["dropped"] for f in _drop_lines(capsys)] == [1], "one line per window, not per drop"

    clock[0] += events_mod.DROP_REPORT_S + 1
    gate.set()
    await asyncio.gather(*held)
    again = events_mod.publish_in_background(waits)
    assert again is not None, "the bound did not free up once the backlog drained"
    await again
    (line,) = _drop_lines(capsys)
    assert (line["dropped"], line["dropped_total"]) == (4, 5), "the window's drops are reported"
    assert events_mod.publish_backlog()["dropped_total"] == 5
