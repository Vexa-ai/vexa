"""meetings→flows publish edge — F168/F181 (ADR-0037 / PRD 46 decision 42.2).

An ad hoc bot — started via the MCP `request_meeting_bot`, never through a calendar invite — ran no
`invite_intake` reaction, and that flow was the only thing that ever told flows a meeting started or
finished. This is the fix: `meeting_api/events.py` publishes `meeting.started` / `meeting.completed`
from the same lifecycle callback that already fires the operator webhook (`webhooks/system.py`), so
`live_meeting` / `post_meeting` now react to a bot however it was dispatched.

Mirrors `core/identity/services/admin-api/tests/test_onboarding_event.py` (the publish-edge shape:
fire-and-forget, swallowed, unset-is-a-profile) and `tests/test_system_webhook.py` (the lifecycle
callback wiring harness: `InMemoryMeetingRepo` + `POST /bots/internal/callback/lifecycle`) — no
docker, no network, no real meeting.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import socket
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api import events as events_mod
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo

REPO_ROOT = pathlib.Path(__file__).resolve().parents[5]


def _seed(repo, *, session_uid="sess-flows", user_id=17, native="flows-meeting", data=None):
    meeting = asyncio.run(repo.create_meeting(
        user_id=user_id, platform="google_meet", native_meeting_id=native, data=data or {}))
    asyncio.run(repo.create_session(meeting_id=meeting["id"], session_uid=session_uid))
    return meeting


def _post(client, body):
    r = client.post("/bots/internal/callback/lifecycle", json=body)
    assert r.status_code == 200, r.text
    return r


@pytest.fixture()
def published(monkeypatch):
    """Every fact meeting-api handed to `events_mod.publish`, recorded at the seam. No network."""
    sent = []

    async def fake(event_type, source_event_id, refs, **kw):
        sent.append({"event_type": event_type, "source_event_id": source_event_id, "refs": refs})
        return True

    monkeypatch.setattr(events_mod, "publish", fake)
    return sent


# ── the wire shape (F142's own test, run here against meeting-api's copy) ──────────────────────
def _record_transport(calls, status=202):
    """An httpx transport that records the request instead of sending it. Replaces the old
    `urlopen` monkeypatch: the publisher is `async` now precisely so it cannot block the loop."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url),
                      "headers": {k.lower(): v for k, v in request.headers.items()},
                      "body": json.loads(request.content.decode())})
        return httpx.Response(status)

    return httpx.MockTransport(handler)


@pytest.fixture()
def wire(monkeypatch):
    """Every request the publisher put on the wire, with no socket involved."""
    calls = []
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = _record_transport(calls)
        return real(*a, **kw)

    # `events.publish` does `import httpx` at call time, so patching the module attribute is what
    # the publisher will see — there is deliberately no module-level client to reach past.
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return calls


async def test_publish_sends_refs_not_subject_refs_and_the_operator_header(monkeypatch, wire):
    """`EventSubmission` is a plain pydantic model — an unknown key is IGNORED, not refused, which
    is exactly how a body spelled `subject_refs` was admitted 202 with `refs == {}` on the F142
    incident. Asserted directly against the wire body this copy of the publisher builds."""
    monkeypatch.setenv("VEXA_FLOWS_API_URL", "http://flows.example")
    monkeypatch.setenv("VEXA_FLOWS_API_KEY", "op-key")

    ok = await events_mod.publish("meeting.started", "live-9", {"uid": "1", "meeting_id": "9"})

    assert ok is True
    assert wire[0]["url"] == "http://flows.example/events"
    assert wire[0]["body"] == {
        "event_type": "meeting.started", "source_event_id": "live-9",
        "refs": {"uid": "1", "meeting_id": "9"},
    }
    assert wire[0]["headers"].get("x-flows-operator-key") == "op-key"


async def test_publish_makes_no_call_and_returns_false_with_no_flows_url(monkeypatch, wire):
    monkeypatch.delenv("VEXA_FLOWS_API_URL", raising=False)
    assert await events_mod.publish("meeting.started", "live-1", {"uid": "1"}) is False
    assert wire == []


def test_source_event_ids_match_invite_intakes_own_scheme():
    """live-<id> / done-<id> — NOT a meeting-api-flavoured id. flows admits on
    `(source_event_id, flow)`; matching invite_intake's own emit_started/emit_completed ids is what
    lets a calendar-intake meeting's second producer dedupe into one reaction instead of firing
    post_meeting/live_meeting twice."""
    assert events_mod.meeting_started_source_id(42) == "live-42"
    assert events_mod.meeting_completed_source_id(42) == "done-42"


def test_refs_shapes_carry_what_process_meeting_reads_without_a_default():
    started = events_mod.meeting_started_refs(9, "abc-defg", "google_meet", 17)
    assert started == {"uid": "17", "meeting_id": "9", "native": "abc-defg", "platform": "google_meet"}
    completed = events_mod.meeting_completed_refs(9, "abc-defg", "google_meet", 17, "user_stopped")
    assert completed == {"uid": "17", "meeting_id": "9", "native": "abc-defg",
                          "platform": "google_meet", "completion_reason": "user_stopped"}


# ── the lifecycle callback wiring (mirrors test_system_webhook.py's harness) ───────────────────
def test_started_publishes_once_on_the_active_transition(published):
    repo = InMemoryMeetingRepo()
    m = _seed(repo)
    client = TestClient(create_app(meeting_repo=repo))
    _post(client, {"connection_id": "sess-flows", "status": "joining", "timestamp": "2026-09-03T10:00:00Z"})
    _post(client, {"connection_id": "sess-flows", "status": "active", "timestamp": "2026-09-03T10:00:10Z"})

    started = [e for e in published if e["event_type"] == "meeting.started"]
    assert len(started) == 1
    assert started[0]["source_event_id"] == f"live-{m['id']}"
    refs = started[0]["refs"]
    assert refs["uid"] == "17"
    assert refs["meeting_id"] == str(m["id"])
    assert refs["native"] == "flows-meeting"


def test_completed_publishes_once_with_the_reason_and_a_replay_is_inert(published):
    """Mirrors `test_system_webhook.py::test_completed_is_sent_once_to_system_sink_and_replay_is_
    inert` — the SAME `change.no_op` guard this publish sits behind, so a duplicate callback
    (meeting-api's own retry path, or an upstream re-delivery) does not double-admit even before
    flows' own `source_event_id` dedup is reached."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo)
    client = TestClient(create_app(meeting_repo=repo))
    for body in (
        {"connection_id": "sess-flows", "status": "joining", "timestamp": "2026-09-03T10:00:00Z"},
        {"connection_id": "sess-flows", "status": "active", "timestamp": "2026-09-03T10:00:10Z"},
        {"connection_id": "sess-flows", "status": "completed", "completion_reason": "stopped",
         "timestamp": "2026-09-03T10:01:00Z"},
        {"connection_id": "sess-flows", "status": "completed", "completion_reason": "stopped",
         "timestamp": "2026-09-03T10:01:00Z"},  # replay of the same terminal callback
    ):
        _post(client, body)

    completed = [e for e in published if e["event_type"] == "meeting.completed"]
    assert len(completed) == 1, "a duplicate terminal callback published meeting.completed twice"
    assert completed[0]["source_event_id"] == f"done-{m['id']}"
    assert completed[0]["refs"]["completion_reason"] == "stopped"
    assert completed[0]["refs"]["meeting_id"] == str(m["id"])


def test_failed_terminal_does_not_publish_meeting_completed(published):
    """bot.failed has no flows consumer today (post_meeting triggers on meeting.completed only,
    and neither producer emits it on a failed run) — mirroring that scope exactly rather than
    inventing a third carrier this change was not asked to add."""
    repo = InMemoryMeetingRepo()
    _seed(repo)
    client = TestClient(create_app(meeting_repo=repo))
    _post(client, {"connection_id": "sess-flows", "status": "joining", "timestamp": "2026-09-03T10:00:00Z"})
    _post(client, {"connection_id": "sess-flows", "status": "failed", "failure_stage": "awaiting_admission",
                   "completion_reason": "awaiting_admission_rejected", "reason": "host denied admission",
                   "timestamp": "2026-09-03T10:00:10Z"})
    assert published == []


# ── it fires in every configuration ─────────────────────────────────────────────────────────────
def test_no_flows_configured_makes_no_http_call_and_the_callback_still_succeeds(monkeypatch):
    """THE POINT OF THIS EDGE. A deployment with no flows domain runs meetings exactly as one with
    a flows domain does — unset is a profile, never a boot refusal and never a failed callback."""
    monkeypatch.delenv("VEXA_FLOWS_API_URL", raising=False)
    monkeypatch.delenv("VEXA_FLOWS_API_KEY", raising=False)
    called = []

    def _no_client(*a, **kw):
        called.append(1)
        raise AssertionError("publish opened an HTTP client with no flows domain configured")

    monkeypatch.setattr(httpx, "AsyncClient", _no_client)
    repo = InMemoryMeetingRepo()
    _seed(repo)
    client = TestClient(create_app(meeting_repo=repo))
    _post(client, {"connection_id": "sess-flows", "status": "joining", "timestamp": "2026-09-03T10:00:00Z"})
    r = _post(client, {"connection_id": "sess-flows", "status": "active", "timestamp": "2026-09-03T10:00:10Z"})
    assert r.status_code == 200
    assert called == [], "publish attempted an HTTP call with no flows domain configured"


def test_a_publish_that_raises_never_fails_the_lifecycle_callback(monkeypatch):
    """A publish edge is not a dependency. meeting-api tells flows; it does not ask it."""
    def boom(*a, **kw):
        raise RuntimeError("flows is down")

    monkeypatch.setattr(events_mod, "publish", boom)
    repo = InMemoryMeetingRepo()
    _seed(repo)
    client = TestClient(create_app(meeting_repo=repo))
    _post(client, {"connection_id": "sess-flows", "status": "joining", "timestamp": "2026-09-03T10:00:00Z"})
    r = _post(client, {"connection_id": "sess-flows", "status": "active", "timestamp": "2026-09-03T10:00:10Z"})
    assert r.status_code == 200


async def test_the_publisher_itself_swallows_a_dead_flows_host(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_API_URL", "http://127.0.0.1:9")  # nothing listens
    monkeypatch.setenv("VEXA_FLOWS_API_KEY", "op-key")
    assert await events_mod.publish("meeting.started", "live-1", {"uid": "1"}) is False


# ── A8: the publish is AWAITED, so a hanging flows never stalls the process ─────────────────────
async def test_a_hanging_flows_target_does_not_block_the_event_loop(monkeypatch):
    """THE DEFECT THIS REPLACES. `publish` was `urllib.request.urlopen` called from inside
    `async def _apply_lifecycle_event` — a BLOCKING call on the one event loop that also runs the
    segment consumer, the db-writer, the live transcript reads and `/health`. The 2 s bound was
    per-request; the stall was per-PROCESS. (And urllib's `timeout` is a socket timeout, so an
    unresolvable host was not bounded by it at all.)

    A real socket that accepts and never answers: the publish must hang on ITS OWN timeout while
    every other coroutine keeps being scheduled. Pre-fix, the heartbeat below could not tick until
    the publish returned, so its elapsed time was the publish's timeout, not its own ~0.2 s."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)                      # accepts into the backlog, answers nothing, ever
    host, port = server.getsockname()
    monkeypatch.setenv("VEXA_FLOWS_API_URL", f"http://{host}:{port}")
    monkeypatch.delenv("VEXA_FLOWS_API_KEY", raising=False)
    try:
        ticks = []

        async def heartbeat():
            for _ in range(10):
                await asyncio.sleep(0.02)
                ticks.append(time.monotonic())

        publishing = asyncio.create_task(
            events_mod.publish("meeting.started", "live-1", {"uid": "1"}, timeout=1.5))
        started = time.monotonic()
        await heartbeat()
        heartbeat_took = time.monotonic() - started

        assert len(ticks) == 10
        assert heartbeat_took < 1.0, (
            f"the loop was blocked for {heartbeat_took:.2f}s by a hanging publish — "
            "10 × 20ms of other work should take ~0.2s")
        assert not publishing.done(), "the publish should still be in flight, bounded by ITS timeout"
        assert await publishing is False          # bounded, swallowed, no exception out
    finally:
        server.close()


async def test_the_hanging_publish_returns_within_its_own_timeout(monkeypatch):
    """The bound is honoured, and it covers connect (DNS included) as well as read — the half
    urllib's socket timeout never covered."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    host, port = server.getsockname()
    monkeypatch.setenv("VEXA_FLOWS_API_URL", f"http://{host}:{port}")
    try:
        started = time.monotonic()
        landed = await events_mod.publish("meeting.started", "live-1", {"uid": "1"}, timeout=0.25)
        elapsed = time.monotonic() - started
        assert landed is False
        assert elapsed < 3.0, f"publish took {elapsed:.2f}s against a 0.25s bound"
    finally:
        server.close()


def test_the_lifecycle_callback_is_not_delayed_by_a_hanging_flows(monkeypatch):
    """The callback the BOT is waiting on. With flows pointed at a socket that never answers, the
    transition still completes — bounded by the publisher's own timeout, never by the resolver."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    host, port = server.getsockname()
    monkeypatch.setenv("VEXA_FLOWS_API_URL", f"http://{host}:{port}")
    monkeypatch.setattr(events_mod, "TIMEOUT_S", 0.25)
    try:
        repo = InMemoryMeetingRepo()
        _seed(repo)
        client = TestClient(create_app(meeting_repo=repo))
        _post(client, {"connection_id": "sess-flows", "status": "joining",
                       "timestamp": "2026-09-03T10:00:00Z"})
        started = time.monotonic()
        r = _post(client, {"connection_id": "sess-flows", "status": "active",
                           "timestamp": "2026-09-03T10:00:10Z"})
        elapsed = time.monotonic() - started
        assert r.status_code == 200
        assert elapsed < 5.0, f"the lifecycle callback took {elapsed:.2f}s on a dead flows target"
    finally:
        server.close()


# ── declaration ↔ census agreement (mirrors flows' own carrier-census suite, run locally) ──────
def test_config_declares_the_publish_edge_for_both_events():
    decl = json.loads((REPO_ROOT / "core/meetings/services/meeting-api/src/meeting_api"
                        "/config.v1.json").read_text())
    edges = [k for k in decl["keys"] if k.get("class") == "publish-edge"]
    assert edges, "meeting-api declares no publish edge — meetings tells flows nothing"
    carried = set()
    for k in edges:
        assert "default" not in k, f"{k['key']} carries a default — a fallback address we invented"
        carried.update(k.get("publishes_events") or [])
    assert carried == {"meeting.started", "meeting.completed"}


def test_the_carriers_meeting_api_publishes_are_owned_by_meetings_in_the_census():
    census = json.loads((REPO_ROOT / "core/flows/contracts/flows.v1/carriers.json").read_text())
    owners = {c["event"]: c for c in census["carriers"]}
    for ev in ("meeting.started", "meeting.completed"):
        assert ev in owners, f"{ev} is published by meeting-api and registered nowhere"
        assert owners[ev]["owner"] == "meetings", (
            f"{ev} is owned by '{owners[ev]['owner']}' in the census; meeting-api's config.v1 "
            f"publish-edge declares it and gate:config-contract requires the domains to agree")
        assert set(owners[ev]["refs"]) <= {"uid", "meeting_id", "native", "platform", "completion_reason"}
