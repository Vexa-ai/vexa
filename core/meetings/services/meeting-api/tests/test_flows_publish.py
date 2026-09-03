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

    def fake(event_type, source_event_id, refs, **kw):
        sent.append({"event_type": event_type, "source_event_id": source_event_id, "refs": refs})
        return True

    monkeypatch.setattr(events_mod, "publish", fake)
    return sent


# ── the wire shape (F142's own test, run here against meeting-api's copy) ──────────────────────
def test_publish_sends_refs_not_subject_refs_and_the_operator_header(monkeypatch):
    """`EventSubmission` is a plain pydantic model — an unknown key is IGNORED, not refused, which
    is exactly how a body spelled `subject_refs` was admitted 202 with `refs == {}` on the F142
    incident. Asserted directly against the wire body this copy of the publisher builds."""
    calls = []

    class _Resp:
        status = 202
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        calls.append({"headers": {k.lower(): v for k, v in req.header_items()},
                      "body": json.loads(req.data.decode())})
        return _Resp()

    monkeypatch.setenv("VEXA_FLOWS_API_URL", "http://flows.example")
    monkeypatch.setenv("VEXA_FLOWS_API_KEY", "op-key")
    monkeypatch.setattr(events_mod.urllib.request, "urlopen", fake_urlopen)

    ok = events_mod.publish("meeting.started", "live-9", {"uid": "1", "meeting_id": "9"})

    assert ok is True
    assert calls[0]["body"] == {
        "event_type": "meeting.started", "source_event_id": "live-9",
        "refs": {"uid": "1", "meeting_id": "9"},
    }
    assert calls[0]["headers"].get("x-flows-operator-key") == "op-key"


def test_publish_makes_no_call_and_returns_false_with_no_flows_url(monkeypatch):
    monkeypatch.delenv("VEXA_FLOWS_API_URL", raising=False)
    called = []
    monkeypatch.setattr(events_mod.urllib.request, "urlopen", lambda *a, **kw: called.append(1))
    assert events_mod.publish("meeting.started", "live-1", {"uid": "1"}) is False
    assert called == []


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
    monkeypatch.setattr(events_mod.urllib.request, "urlopen", lambda *a, **kw: called.append(1))
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


def test_the_publisher_itself_swallows_a_dead_flows_host(monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_API_URL", "http://127.0.0.1:9")  # nothing listens
    monkeypatch.setenv("VEXA_FLOWS_API_KEY", "op-key")
    assert events_mod.publish("meeting.started", "live-1", {"uid": "1"}) is False


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
