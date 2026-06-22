"""Lifecycle-callback durability + WS-status publish proofs.

Both behaviours live in the lifecycle-callback nexus (``meeting_api.app._mount_lifecycle``), driven
here over the unified ``create_app`` via FastAPI ``TestClient`` — the SAME shipped handler prod runs,
with in-process fakes (no DB, no real redis, no bot). Asserts:

① DURABILITY — a terminal ``completed`` event arriving at an EMPTY in-memory store rehydrates from the
DB's current status before applying, so it advances to ``completed`` (200, not a rejected transition),
and a same-status redelivery is an idempotent 200 no-op.

② WS-STATUS — each persisted advance publishes a ws.v1 ``BotStatus`` frame to
``bm:meeting:{id}:status`` (the gateway ``/ws`` forwards it), and a no-op replay publishes nothing.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo

ENDPOINT = "/bots/internal/callback/lifecycle"


class _RecordingRedis:
    """A minimal RedisBus stand-in that records every publish (channel, decoded payload)."""

    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    async def publish(self, channel: str, data: str):
        self.published.append((channel, json.loads(data)))
        return 1


def _seed_active_meeting(repo: InMemoryMeetingRepo, *, session_uid: str = "sess-uid") -> dict:
    """A meeting at status 'active' with a session — the state a restart would leave persisted while
    the in-memory FSM store is empty."""
    import asyncio

    m = asyncio.run(repo.create_meeting(user_id=1, platform="google_meet", native_meeting_id="m1", data={}))
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=session_uid))
    repo.set_status(m["id"], "active")  # the bot reached active before the restart wiped the store
    return m


# ── ① rehydration: empty store + DB at 'active' → terminal 'completed' is 200 (not 409) ───────────

def test_rehydration_terminal_after_restart_is_200(goldens):
    """Fresh empty LifecycleSink store + a meeting persisted at 'active' → POST 'completed' → 200,
    DB advances to 'completed', NO 409 (the LIFECYCLE-409 regression)."""
    repo = InMemoryMeetingRepo()
    m = _seed_active_meeting(repo)
    # Brand-new app → brand-new EMPTY MeetingStore (simulates the post-restart empty in-memory FSM).
    client = TestClient(create_app(meeting_repo=repo))

    r = client.post(ENDPOINT, json=goldens["completed-stopped"])

    assert r.status_code == 200, r.text  # NOT 409
    body = r.json()
    assert body["status"] == "accepted"
    assert body["meeting_status"] == "completed"
    # The DB row reflects the terminal advance — meeting no longer stuck 'active'.
    import asyncio

    assert asyncio.run(repo.get_status_by_session(session_uid="sess-uid")) == "completed"


def test_rehydration_emits_status_change_and_no_409(goldens):
    """The rehydrated advance still emits the meeting.status_change webhook envelope (the finalize
    signal that never fired while stuck at 409)."""
    repo = InMemoryMeetingRepo()
    _seed_active_meeting(repo)
    app = create_app(meeting_repo=repo)
    client = TestClient(app)

    r = client.post(ENDPOINT, json=goldens["completed-stopped"])
    assert r.status_code == 200, r.text
    envelopes = app.state.status_change_webhooks
    assert envelopes, "no status_change envelope emitted on the rehydrated terminal advance"
    sc = envelopes[-1]["data"]["status_change"]
    assert sc["old_status"] == "active" and sc["new_status"] == "completed"


def test_rehydration_seeds_stopping_as_active(goldens):
    """A meeting persisted at the server-side 'stopping' state rehydrates to ACTIVE, so the bot's
    terminal 'completed' stays legal (stopping is not a BotStatus)."""
    repo = InMemoryMeetingRepo()
    m = _seed_active_meeting(repo)
    repo.set_status(m["id"], "stopping")
    client = TestClient(create_app(meeting_repo=repo))

    r = client.post(ENDPOINT, json=goldens["completed-stopped"])
    assert r.status_code == 200, r.text
    assert r.json()["meeting_status"] == "completed"


# ── ① idempotency: POST 'completed' twice → both 200 ─────────────────────────────────────────────

def test_idempotent_terminal_redelivery_is_200(goldens):
    """POST 'completed' twice (the bot's 3x retry) → BOTH 200, not a 409 on the redelivery."""
    repo = InMemoryMeetingRepo()
    _seed_active_meeting(repo)
    client = TestClient(create_app(meeting_repo=repo))

    r1 = client.post(ENDPOINT, json=goldens["completed-stopped"])
    r2 = client.post(ENDPOINT, json=goldens["completed-stopped"])

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["meeting_status"] == "completed"
    assert r2.json()["meeting_status"] == "completed"


def test_idempotent_same_status_replay_is_200():
    """A non-terminal same-status replay (joining → joining) is a 200 no-op, not a 409."""
    repo = InMemoryMeetingRepo()
    import asyncio

    m = asyncio.run(repo.create_meeting(user_id=1, platform="google_meet", native_meeting_id="m1", data={}))
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid="sess-uid"))
    client = TestClient(create_app(meeting_repo=repo))

    assert client.post(ENDPOINT, json={"connection_id": "sess-uid", "status": "joining"}).status_code == 200
    assert client.post(ENDPOINT, json={"connection_id": "sess-uid", "status": "joining"}).status_code == 200


def test_genuinely_illegal_transition_still_409_after_reconcile():
    """A genuinely illegal edge (a DIFFERENT terminal on an already-terminal record) still 409s —
    rehydration/idempotency must NOT mask a real illegality."""
    repo = InMemoryMeetingRepo()
    m = _seed_active_meeting(repo)
    repo.set_status(m["id"], "completed")  # already terminal in the DB
    client = TestClient(create_app(meeting_repo=repo))

    # active→...→completed already; now a DIFFERENT terminal `failed` on a completed record.
    r = client.post(ENDPOINT, json={"connection_id": "sess-uid", "status": "failed", "exit_code": 1})
    assert r.status_code == 409, r.text
    assert r.json()["from"] == "completed" and r.json()["to"] == "failed"


# ── ② WS publish: a lifecycle advance lands a CLEAN ws.v1 BotStatus frame on bm:meeting:{id}:status ─

def test_ws_status_published_on_advance(goldens):
    """With a fake redis bus, POST a lifecycle event → a publish lands on bm:meeting:{id}:status
    in the canonical 0.10.6 WS shape: {type:'meeting.status', meeting:{id,platform,native_id},
    payload:{status}, user_id, ts}. `status` is the raw BotStatus value; clients adapt their own
    vocabulary on THEIR side (the core emits the contract, never a client's naming)."""
    repo = InMemoryMeetingRepo()
    m = _seed_active_meeting(repo)
    redis = _RecordingRedis()
    client = TestClient(create_app(meeting_repo=repo, redis=redis))

    r = client.post(ENDPOINT, json=goldens["completed-stopped"])
    assert r.status_code == 200, r.text

    chan = f"bm:meeting:{m['id']}:status"
    hits = [p for (c, p) in redis.published if c == chan]
    assert hits, f"no publish on {chan}; published={redis.published}"
    frame = hits[-1]
    assert frame["type"] == "meeting.status"
    assert frame["payload"]["status"] == "completed"
    assert frame["meeting"]["id"] == m["id"]


def test_ws_status_frame_matches_010_6_contract(goldens):
    """The published status frame matches the canonical 0.10.6 WS contract (vexa-0.11
    meetings.publish_meeting_status_change): top-level type/meeting/payload/user_id/ts, status under
    payload.status ∈ the meeting-status vocabulary. This is the source of truth the new dashboard
    consumes; ws.v1 is reconciled to it (lane:contract)."""
    repo = InMemoryMeetingRepo()
    _seed_active_meeting(repo)
    redis = _RecordingRedis()
    client = TestClient(create_app(meeting_repo=repo, redis=redis))
    client.post(ENDPOINT, json=goldens["completed-stopped"])

    frame = redis.published[-1][1]
    assert frame["type"] == "meeting.status"
    assert isinstance(frame.get("meeting"), dict) and "id" in frame["meeting"]
    assert isinstance(frame.get("payload"), dict)
    assert "user_id" in frame and "ts" in frame
    STATUS = {"requested", "joining", "awaiting_admission", "active",
              "needs_help", "stopping", "completed", "failed"}
    assert frame["payload"]["status"] in STATUS, f"status not in 0.10.6 vocabulary: {frame}"


def test_no_publish_on_idempotent_replay(goldens):
    """The idempotent terminal redelivery is a no-op — it must NOT publish a duplicate ws frame."""
    repo = InMemoryMeetingRepo()
    _seed_active_meeting(repo)
    redis = _RecordingRedis()
    client = TestClient(create_app(meeting_repo=repo, redis=redis))

    client.post(ENDPOINT, json=goldens["completed-stopped"])
    n_after_first = len(redis.published)
    client.post(ENDPOINT, json=goldens["completed-stopped"])  # redelivery
    assert len(redis.published) == n_after_first, "duplicate publish on idempotent replay"
