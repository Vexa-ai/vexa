"""Adversarial lifecycle-seam coverage — probe EVERY edge of the meeting FSM + its callback.

Distinct from ``test_lifecycle_durable.py`` (which proves the happy rehydrate/idempotency/ws paths)
and ``test_lifecycle_http.py`` (which proves a few legal/illegal HTTP cases). This file is the
exhaustive adversarial sweep over the lifecycle seam:

  * EVERY illegal transition (each from-state → each disallowed to-state) → 409 with the correct
    ``from``/``to`` echoed.
  * Idempotency for BOTH terminals (completed AND failed redelivered) + same-status non-terminal
    replays.
  * Rehydration correctness for EACH persisted DB status (requested/joining/awaiting_admission/
    needs_help/active/stopping/completed/failed) — can the bot's next legal event proceed off it?
  * Malformed callbacks (missing connection_id, unknown connection_id, missing status, bad enum).
  * The stop-reconcile backstop (completes a stale ``stopping``; leaves a non-stopping alone; is
    idempotent; races a real bot callback) — exercised through the SAME HTTP callback path the loop
    POSTs to, with a fake repo that implements ``list_stale_stopping``.
  * The ``meeting.status_change`` webhook fires exactly once per REAL advance (and the discrepancy
    on a no-op replay).

Everything runs over the unified ``meeting_api.create_app`` via FastAPI ``TestClient`` (the shipped
handler) with in-process fakes — no DB, no redis, no bot.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.lifecycle.machine import (
    BotStatus,
    LifecycleSink,
    MeetingStore,
    can_transition,
)

ENDPOINT = "/bots/internal/callback/lifecycle"

# Every BotStatus value the bot can emit (the lifecycle.v1 BotStatus enum).
ALL_STATUSES = [s.value for s in BotStatus]  # joining, awaiting_admission, active, needs_help, completed, failed

# The persisted-DB statuses the FSM rehydrates from (superset of BotStatus — adds requested/stopping).
PERSISTED_STATUSES = [
    "requested", "joining", "awaiting_admission", "needs_help",
    "active", "stopping", "completed", "failed",
]


# ── shared fakes / helpers ────────────────────────────────────────────────────────────────────────


class _RecordingRedis:
    """Records every publish (channel, decoded payload)."""

    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    async def publish(self, channel: str, data: str):
        self.published.append((channel, json.loads(data)))
        return 1


class _ReconcileRepo(InMemoryMeetingRepo):
    """InMemoryMeetingRepo + the ``list_stale_stopping`` the stop-reconcile loop requires.

    The shipped ``InMemoryMeetingRepo`` does NOT implement ``list_stale_stopping`` (only the
    SqlAlchemy adapter does), so the production loop's ``hasattr`` guard makes it a no-op with the
    fake. We add a deterministic stand-in here so the reconcile CONTRACT (which (meeting,session)
    pairs the loop completes) is testable without a real clock: any meeting currently at ``stopping``
    is considered stale, paired with its latest session_uid.
    """

    def list_stale_stopping_sync(self) -> list[tuple[int, str, object]]:
        out: dict[int, tuple] = {}
        # latest session per meeting (mirror the SQL adapter's MeetingSession.id desc)
        for s in reversed(self.sessions):
            mid = s["meeting_id"]
            row = self._meetings.get(mid)
            if row is None or row["status"] != "stopping":
                continue
            if mid not in out:
                out[mid] = (s["session_uid"], row.get("bot_container_id"))
        return [(mid, sid, bcid) for mid, (sid, bcid) in out.items()]

    async def list_stale_stopping(self, *, older_than_seconds: float) -> list[tuple[int, str, object]]:
        return self.list_stale_stopping_sync()


def _seed(repo: InMemoryMeetingRepo, *, status: str, session_uid: str = "sess-uid") -> dict:
    """Create a meeting + session and force its persisted status (post-restart shape)."""
    m = asyncio.run(repo.create_meeting(user_id=1, platform="google_meet", native_meeting_id="m1", data={}))
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=session_uid))
    repo.set_status(m["id"], status)
    return m


def _post(client: TestClient, **event):
    return client.post(ENDPOINT, json=event)


def _drive_to(client: TestClient, target: str, *, connection_id: str = "c") -> None:
    """Drive a FRESH (empty-store) record up to ``target`` via the legal path, asserting 200 each hop."""
    path = {
        "joining": ["joining"],
        "awaiting_admission": ["joining", "awaiting_admission"],
        "active": ["joining", "active"],
        "needs_help": ["joining", "awaiting_admission", "needs_help"],
        "completed": ["joining", "active", "completed"],
        "failed": ["joining", "failed"],
    }[target]
    for st in path:
        ev = {"connection_id": connection_id, "status": st}
        if st in ("completed", "failed"):
            ev["exit_code"] = 0 if st == "completed" else 1
            if st == "completed":
                ev["completion_reason"] = "stopped"
        r = _post(client, **ev)
        assert r.status_code == 200, f"setup hop {st} failed: {r.text}"


# ╔══════════════════════════════════════════════════════════════════════════════════════════════╗
# ║ 1. EVERY ILLEGAL TRANSITION → 409 with correct from/to                                          ║
# ╚══════════════════════════════════════════════════════════════════════════════════════════════╝

# Build the matrix of illegal edges from the machine's own LEGAL_TRANSITIONS so it stays in sync.
_ILLEGAL_NONTERMINAL_EDGES: list[tuple[str, str]] = []
for _frm in ("joining", "awaiting_admission", "needs_help", "active"):
    _frm_status = BotStatus(_frm)
    for _to in ALL_STATUSES:
        _to_status = BotStatus(_to)
        if _to_status == _frm_status:
            continue  # same-status is the idempotent no-op, not illegal — covered in section 2
        if not can_transition(_frm_status, _to_status):
            _ILLEGAL_NONTERMINAL_EDGES.append((_frm, _to))


@pytest.mark.parametrize("frm,to", _ILLEGAL_NONTERMINAL_EDGES, ids=[f"{f}->{t}" for f, t in _ILLEGAL_NONTERMINAL_EDGES])
def test_illegal_nonterminal_transition_is_409(frm, to):
    """Every disallowed edge out of a non-terminal state → 409 echoing the exact from/to."""
    repo = InMemoryMeetingRepo()
    _seed(repo, status="requested")
    client = TestClient(create_app(meeting_repo=repo))
    _drive_to(client, frm, connection_id="sess-uid")

    ev = {"connection_id": "sess-uid", "status": to}
    if to in ("completed", "failed"):
        ev["exit_code"] = 0 if to == "completed" else 1
    r = _post(client, **ev)
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["status"] == "error"
    assert body["from"] == frm, f"expected from={frm}, got {body}"
    assert body["to"] == to, f"expected to={to}, got {body}"


@pytest.mark.parametrize("frm", ["completed", "failed"])
@pytest.mark.parametrize("to", ALL_STATUSES)
def test_transition_off_terminal_is_409_unless_same(frm, to):
    """A terminal record rejects every DIFFERENT to-status (409); the SAME terminal is the
    idempotent no-op (200) handled in section 2 — so here we only assert the DIFFERENT case."""
    if to == frm:
        pytest.skip("same-terminal redelivery is the idempotent no-op (section 2)")
    repo = InMemoryMeetingRepo()
    _seed(repo, status="requested")
    client = TestClient(create_app(meeting_repo=repo))
    _drive_to(client, frm, connection_id="sess-uid")

    ev = {"connection_id": "sess-uid", "status": to, "exit_code": 1}
    r = _post(client, **ev)
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["from"] == frm and body["to"] == to


def test_first_event_other_than_joining_is_409():
    """A record's FIRST event must be `joining`; any other first event → 409 from=None."""
    for to in ("awaiting_admission", "active", "needs_help", "completed", "failed"):
        repo = InMemoryMeetingRepo()
        # No persisted status that maps to a non-None BotStatus → rehydrate yields None.
        _seed(repo, status="requested")
        client = TestClient(create_app(meeting_repo=repo))
        ev = {"connection_id": "sess-uid", "status": to, "exit_code": 1}
        r = _post(client, **ev)
        assert r.status_code == 409, f"{to}: {r.text}"
        assert r.json()["from"] is None
        assert r.json()["to"] == to


# ╔══════════════════════════════════════════════════════════════════════════════════════════════╗
# ║ 2. IDEMPOTENCY — same-status replays (terminal + non-terminal) → 200 no-op                      ║
# ╚══════════════════════════════════════════════════════════════════════════════════════════════╝

@pytest.mark.parametrize("status", ["joining", "awaiting_admission", "active", "needs_help"])
def test_nonterminal_same_status_replay_is_200(status):
    """Redelivering the record's CURRENT non-terminal status is a 200 no-op (not a 409)."""
    repo = InMemoryMeetingRepo()
    _seed(repo, status="requested")
    client = TestClient(create_app(meeting_repo=repo))
    _drive_to(client, status, connection_id="sess-uid")

    r = _post(client, connection_id="sess-uid", status=status)
    assert r.status_code == 200, r.text
    assert r.json()["meeting_status"] == status


def test_completed_redelivery_is_200():
    """The bot retries its terminal up to 3x — a second `completed` must be 200, not 409."""
    repo = InMemoryMeetingRepo()
    _seed(repo, status="active")
    client = TestClient(create_app(meeting_repo=repo))
    ev = {"connection_id": "sess-uid", "status": "completed", "exit_code": 0, "completion_reason": "stopped"}
    r1 = _post(client, **ev)
    r2 = _post(client, **ev)
    r3 = _post(client, **ev)
    assert r1.status_code == r2.status_code == r3.status_code == 200, (r1.text, r2.text, r3.text)
    assert r3.json()["meeting_status"] == "completed"


def test_failed_redelivery_is_200():
    """The OTHER terminal (failed) must also be idempotent on redelivery — not only completed."""
    repo = InMemoryMeetingRepo()
    _seed(repo, status="active")
    client = TestClient(create_app(meeting_repo=repo))
    ev = {"connection_id": "sess-uid", "status": "failed", "exit_code": 1, "completion_reason": "join_failure"}
    r1 = _post(client, **ev)
    r2 = _post(client, **ev)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r2.json()["meeting_status"] == "failed"


# ╔══════════════════════════════════════════════════════════════════════════════════════════════╗
# ║ 3. REHYDRATION — every persisted DB status → can the next legal bot event proceed?              ║
# ╚══════════════════════════════════════════════════════════════════════════════════════════════╝

# For each persisted status, the next LEGAL bot event (chosen so it should 200) and the expected
# resulting meeting_status. `requested` rehydrates to None → first event must be `joining`.
_REHYDRATE_NEXT = {
    "requested": ("joining", "joining"),
    "joining": ("active", "active"),
    "awaiting_admission": ("active", "active"),
    "needs_help": ("active", "active"),
    "active": ("completed", "completed"),
    "stopping": ("completed", "completed"),   # stopping rehydrates to ACTIVE → completed legal
    "completed": ("completed", "completed"),  # terminal redelivery → idempotent 200
    "failed": ("failed", "failed"),           # terminal redelivery → idempotent 200
}


@pytest.mark.parametrize("persisted", PERSISTED_STATUSES, ids=PERSISTED_STATUSES)
def test_rehydration_allows_next_legal_event(persisted):
    """After a restart (empty in-memory store) a meeting persisted at `persisted` must let the bot's
    next legal event land as a 200 — the LIFECYCLE-409 durability guarantee, for EVERY status."""
    next_status, expect = _REHYDRATE_NEXT[persisted]
    repo = InMemoryMeetingRepo()
    _seed(repo, status=persisted)
    # Fresh app → empty MeetingStore (post-restart).
    client = TestClient(create_app(meeting_repo=repo))

    ev = {"connection_id": "sess-uid", "status": next_status}
    if next_status in ("completed", "failed"):
        ev["exit_code"] = 0 if next_status == "completed" else 1
        if next_status == "completed":
            ev["completion_reason"] = "stopped"
    r = _post(client, **ev)
    assert r.status_code == 200, f"persisted={persisted} next={next_status}: {r.text}"
    assert r.json()["meeting_status"] == expect


def test_rehydration_does_not_mask_real_illegality():
    """Rehydration seeds state but must NOT make a genuinely illegal edge succeed: a meeting
    persisted at `active` that receives `joining` (active→joining) still 409s after rehydration."""
    repo = InMemoryMeetingRepo()
    _seed(repo, status="active")
    client = TestClient(create_app(meeting_repo=repo))
    r = _post(client, connection_id="sess-uid", status="joining")
    assert r.status_code == 409, r.text
    assert r.json()["from"] == "active" and r.json()["to"] == "joining"


def test_rehydration_requested_then_skip_joining_is_409():
    """A `requested` DB row rehydrates to None; a bot event that skips `joining` (None→active) is
    still illegal → 409 (rehydration of `requested` is the pre-joining entry, not a free pass)."""
    repo = InMemoryMeetingRepo()
    _seed(repo, status="requested")
    client = TestClient(create_app(meeting_repo=repo))
    r = _post(client, connection_id="sess-uid", status="active")
    assert r.status_code == 409, r.text
    assert r.json()["from"] is None and r.json()["to"] == "active"


def test_rehydration_in_memory_record_wins_over_stale_db():
    """A live in-process record (already advanced) must NOT be overwritten by a staler DB read:
    drive joining→active in-process, then flip the DB BACK to `joining`; the next `completed` must
    still succeed (the in-memory ACTIVE is the source of truth, not the stale DB `joining`)."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="requested")
    client = TestClient(create_app(meeting_repo=repo))
    assert _post(client, connection_id="sess-uid", status="joining").status_code == 200
    assert _post(client, connection_id="sess-uid", status="active").status_code == 200
    # Simulate a stale DB read regressing to joining (it shouldn't reseed the live record).
    repo.set_status(m["id"], "joining")
    r = _post(client, connection_id="sess-uid", status="completed", exit_code=0, completion_reason="stopped")
    assert r.status_code == 200, r.text
    assert r.json()["meeting_status"] == "completed"


# ╔══════════════════════════════════════════════════════════════════════════════════════════════╗
# ║ 4. MALFORMED CALLBACKS                                                                          ║
# ╚══════════════════════════════════════════════════════════════════════════════════════════════╝

def test_missing_status_is_422():
    repo = InMemoryMeetingRepo()
    _seed(repo, status="requested")
    client = TestClient(create_app(meeting_repo=repo))
    r = client.post(ENDPOINT, json={"connection_id": "sess-uid"})
    assert r.status_code == 422, r.text
    assert "schema violation" in r.json()["detail"]


def test_missing_connection_id_is_422():
    repo = InMemoryMeetingRepo()
    client = TestClient(create_app(meeting_repo=repo))
    r = client.post(ENDPOINT, json={"status": "joining"})
    assert r.status_code == 422, r.text


def test_bad_status_enum_is_422():
    repo = InMemoryMeetingRepo()
    _seed(repo, status="requested")
    client = TestClient(create_app(meeting_repo=repo))
    r = client.post(ENDPOINT, json={"connection_id": "sess-uid", "status": "bogus"})
    assert r.status_code == 422, r.text


def test_unknown_connection_id_joining_is_accepted_but_not_persisted():
    """An UNKNOWN connection_id (no session row) with a legal first event: the FSM creates an
    in-memory record and returns 200, but the DB persist no-ops (unknown session). This DOCUMENTS
    the current behaviour — the callback does not 404 an unknown session."""
    repo = InMemoryMeetingRepo()  # no meeting/session seeded
    client = TestClient(create_app(meeting_repo=repo))
    r = client.post(ENDPOINT, json={"connection_id": "ghost", "status": "joining"})
    assert r.status_code == 200, r.text
    assert r.json()["meeting_status"] == "joining"
    # Nothing persisted (no such session) — get_status_by_session stays None.
    assert asyncio.run(repo.get_status_by_session(session_uid="ghost")) is None


def test_unknown_connection_id_terminal_is_409():
    """An unknown connection_id with a TERMINAL first event has nothing to rehydrate from (no
    session) → fresh status=None → None→completed is illegal → 409. The bot can't 'complete' a
    session the control plane never saw."""
    repo = InMemoryMeetingRepo()
    client = TestClient(create_app(meeting_repo=repo))
    r = client.post(ENDPOINT, json={"connection_id": "ghost", "status": "completed", "exit_code": 0})
    assert r.status_code == 409, r.text
    assert r.json()["from"] is None and r.json()["to"] == "completed"


# ╔══════════════════════════════════════════════════════════════════════════════════════════════╗
# ║ 5. STOP-RECONCILE BACKSTOP                                                                      ║
# ╚══════════════════════════════════════════════════════════════════════════════════════════════╝
# The loop POSTs a synthetic {status:'completed', completion_reason:'stopped'} for each stale
# (meeting, session) to THIS process's own callback. We reproduce that exact POST against the live
# TestClient — same rehydrate→persist→webhook→ws path — so the reconcile CONTRACT is tested without
# the production while/sleep wrapper.

def _reconcile_once(client: TestClient, repo: _ReconcileRepo) -> list[tuple[int, str, int]]:
    """Run ONE reconcile sweep exactly as ``_stop_reconcile_loop`` would: for each stale stopping,
    POST the synthetic completed callback. Returns [(meeting_id, session_uid, status_code), …]."""
    out = []
    for meeting_id, session_uid, _bot_container_id in repo.list_stale_stopping_sync():
        r = client.post(ENDPOINT, json={
            "connection_id": session_uid, "status": "completed", "completion_reason": "stopped",
        })
        out.append((meeting_id, session_uid, r.status_code))
    return out


def test_reconcile_completes_stale_stopping():
    """A meeting stuck at `stopping` is completed by one reconcile sweep (200) and its DB row
    advances to `completed`."""
    repo = _ReconcileRepo()
    m = _seed(repo, status="stopping")
    client = TestClient(create_app(meeting_repo=repo))

    results = _reconcile_once(client, repo)
    assert results == [(m["id"], "sess-uid", 200)], results
    assert asyncio.run(repo.get_status_by_session(session_uid="sess-uid")) == "completed"


@pytest.mark.parametrize("status", ["requested", "joining", "awaiting_admission", "active", "needs_help", "completed", "failed"])
def test_reconcile_leaves_non_stopping_alone(status):
    """The backstop ONLY touches `stopping` meetings — a meeting at any other status is never
    completed by the reconcile sweep."""
    repo = _ReconcileRepo()
    m = _seed(repo, status=status)
    client = TestClient(create_app(meeting_repo=repo))
    results = _reconcile_once(client, repo)
    assert results == [], f"reconcile touched a {status} meeting: {results}"
    assert repo._meetings[m["id"]]["status"] == status


def test_reconcile_is_idempotent_across_ticks():
    """Two reconcile sweeps in a row: the first completes the stale meeting, the second finds
    nothing stale (it's `completed` now) — no duplicate work, no 409."""
    repo = _ReconcileRepo()
    _seed(repo, status="stopping")
    client = TestClient(create_app(meeting_repo=repo))
    first = _reconcile_once(client, repo)
    second = _reconcile_once(client, repo)
    assert [c for *_ , c in first] == [200]
    assert second == [], second  # nothing stale on the second pass


def test_reconcile_then_late_bot_terminal_is_idempotent_200():
    """RACE: reconcile completes the meeting, THEN the bot's own (late) terminal callback arrives.
    The late completed must be an idempotent 200 no-op, not a 409 — and must not double-advance."""
    repo = _ReconcileRepo()
    _seed(repo, status="stopping")
    client = TestClient(create_app(meeting_repo=repo))
    assert [c for *_, c in _reconcile_once(client, repo)] == [200]

    # The bot finally sends its terminal (the one the reconcile pre-empted).
    r = client.post(ENDPOINT, json={
        "connection_id": "sess-uid", "status": "completed", "exit_code": 0, "completion_reason": "stopped",
    })
    assert r.status_code == 200, r.text
    assert r.json()["meeting_status"] == "completed"


def test_bot_terminal_then_reconcile_finds_nothing():
    """RACE (other order): the bot completes the meeting BEFORE the grace window fires. By the time
    reconcile would run, the DB is already `completed`, so the sweep finds nothing stale."""
    repo = _ReconcileRepo()
    _seed(repo, status="stopping")
    client = TestClient(create_app(meeting_repo=repo))
    # Bot's own terminal lands first (rehydrates stopping→active, completes).
    r = client.post(ENDPOINT, json={
        "connection_id": "sess-uid", "status": "completed", "exit_code": 0, "completion_reason": "stopped",
    })
    assert r.status_code == 200, r.text
    # Now reconcile: meeting is no longer `stopping`.
    assert _reconcile_once(client, repo) == []


def test_reconcile_late_bot_failed_after_completed_is_409():
    """If reconcile already completed the meeting and the bot then reports a DIFFERENT terminal
    (`failed`), that's a genuine contradiction → 409 (idempotency must not swallow a real conflict)."""
    repo = _ReconcileRepo()
    _seed(repo, status="stopping")
    client = TestClient(create_app(meeting_repo=repo))
    assert [c for *_, c in _reconcile_once(client, repo)] == [200]
    r = client.post(ENDPOINT, json={"connection_id": "sess-uid", "status": "failed", "exit_code": 1})
    assert r.status_code == 409, r.text
    assert r.json()["from"] == "completed" and r.json()["to"] == "failed"


# ╔══════════════════════════════════════════════════════════════════════════════════════════════╗
# ║ 6. WEBHOOK / WS — fire exactly once per REAL advance, never on a no-op                          ║
# ╚══════════════════════════════════════════════════════════════════════════════════════════════╝

def test_one_webhook_envelope_per_real_advance():
    """Each genuine FSM advance emits exactly one meeting.status_change envelope — N advances → N
    envelopes, in order."""
    repo = InMemoryMeetingRepo()
    _seed(repo, status="requested")
    app = create_app(meeting_repo=repo)
    client = TestClient(app)
    for st, ev in [
        ("joining", {"status": "joining"}),
        ("active", {"status": "active"}),
        ("completed", {"status": "completed", "exit_code": 0, "completion_reason": "stopped"}),
    ]:
        assert client.post(ENDPOINT, json={"connection_id": "sess-uid", **ev}).status_code == 200
    envs = app.state.status_change_webhooks
    assert len(envs) == 3, [e["data"]["status_change"] for e in envs]
    news = [e["data"]["status_change"]["new_status"] for e in envs]
    assert news == ["joining", "active", "completed"]


def test_no_ws_publish_on_idempotent_replay():
    """A redelivered terminal is a no-op: it publishes NO additional ws.v1 BotStatus frame."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="active")
    redis = _RecordingRedis()
    client = TestClient(create_app(meeting_repo=repo, redis=redis))
    ev = {"connection_id": "sess-uid", "status": "completed", "exit_code": 0, "completion_reason": "stopped"}
    client.post(ENDPOINT, json=ev)
    n = len(redis.published)
    client.post(ENDPOINT, json=ev)  # redelivery
    assert len(redis.published) == n, f"duplicate ws publish on no-op replay: {redis.published}"


def test_bot_advance_publishes_user_channel_frame():
    """Every genuine bot-FSM advance ALSO publishes a FLAT meeting.status frame to the user-scoped
    channel u:{user_id}:meetings (Track ②), alongside the existing bm:meeting:{id}:status frame."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="requested")  # user_id=1 per _seed
    redis = _RecordingRedis()
    client = TestClient(create_app(meeting_repo=repo, redis=redis))
    r = _post(client, connection_id="sess-uid", status="joining")
    assert r.status_code == 200, r.text

    channels = {c for c, _ in redis.published}
    assert f"bm:meeting:{m['id']}:status" in channels  # legacy per-meeting frame kept
    assert f"u:1:meetings" in channels  # NEW user-scoped frame

    user_frame = next(p for c, p in redis.published if c == "u:1:meetings")
    assert user_frame["type"] == "meeting.status"
    assert user_frame["meeting_id"] == m["id"]
    assert user_frame["native"] == "m1"
    assert user_frame["status"] == "joining"
    assert "when" in user_frame


# FIXED (L1): the status_change envelope build+append is now gated on `not change.no_op` (app.py),
# mirroring the persist + ws-publish guards — a no-op replay no longer double-counts. Regression guard.
def test_no_extra_webhook_envelope_on_idempotent_replay():
    """The idempotent redelivery (no_op) advances NOTHING, so it must NOT add another
    status_change envelope to app.state.status_change_webhooks.

    BUG: app._mount_lifecycle appends the envelope UNCONDITIONALLY (app.py L199-200), BEFORE the
    `change.no_op` guard that gates the persist + ws-publish. So the in-process envelope log
    double-counts a no-op replay even though no real advance (and no real webhook delivery / ws
    publish) occurred. The redis path (test_no_ws_publish_on_idempotent_replay) is correctly gated;
    the status_change_webhooks list is not. Expected: count unchanged on a no-op."""
    repo = InMemoryMeetingRepo()
    _seed(repo, status="active")
    app = create_app(meeting_repo=repo)
    client = TestClient(app)
    ev = {"connection_id": "sess-uid", "status": "completed", "exit_code": 0, "completion_reason": "stopped"}
    client.post(ENDPOINT, json=ev)
    n = len(app.state.status_change_webhooks)
    client.post(ENDPOINT, json=ev)  # redelivery — pure no-op
    assert len(app.state.status_change_webhooks) == n, (
        "no_op replay appended a duplicate status_change envelope to app.state.status_change_webhooks "
        f"(expected {n}, got {len(app.state.status_change_webhooks)})"
    )


def test_webhook_old_new_status_correct_across_full_path():
    """The status_change old/new pair is correct at every hop (no off-by-one in old_status)."""
    repo = InMemoryMeetingRepo()
    _seed(repo, status="requested")
    app = create_app(meeting_repo=repo)
    client = TestClient(app)
    for ev in [
        {"status": "joining"},
        {"status": "awaiting_admission"},
        {"status": "active"},
        {"status": "completed", "exit_code": 0, "completion_reason": "stopped"},
    ]:
        client.post(ENDPOINT, json={"connection_id": "sess-uid", **ev})
    pairs = [(e["data"]["status_change"]["old_status"], e["data"]["status_change"]["new_status"])
             for e in app.state.status_change_webhooks]
    assert pairs == [
        (None, "joining"),
        ("joining", "awaiting_admission"),
        ("awaiting_admission", "active"),
        ("active", "completed"),
    ], pairs


# ╔══════════════════════════════════════════════════════════════════════════════════════════════╗
# ║ 7. DIRECT-FSM cross-checks (no HTTP) — the sink-level invariants the seam relies on             ║
# ╚══════════════════════════════════════════════════════════════════════════════════════════════╝

def test_sink_no_op_flag_set_only_on_same_status():
    """apply_change(no_op=True) iff the event equals the record's current status."""
    sink = LifecycleSink(store=MeetingStore())
    c1 = sink.apply_change({"connection_id": "x", "status": "joining"})
    assert c1.no_op is False
    c2 = sink.apply_change({"connection_id": "x", "status": "joining"})
    assert c2.no_op is True
    c3 = sink.apply_change({"connection_id": "x", "status": "active"})
    assert c3.no_op is False


def test_sink_history_only_grows_on_real_advance():
    """A no-op replay must not append to the record's history trail."""
    sink = LifecycleSink(store=MeetingStore())
    sink.apply({"connection_id": "x", "status": "joining"})
    sink.apply({"connection_id": "x", "status": "active"})
    rec = sink.store.get("x")
    n = len(rec.history)
    sink.apply({"connection_id": "x", "status": "active"})  # no-op replay
    assert len(rec.history) == n, f"history grew on a no-op replay: {rec.history}"
    assert len(rec.status_transition) == n, "status_transition trail grew on a no-op replay"


# ╔══════════════════════════════════════════════════════════════════════════════════════════════╗
# ║ 7. GENERAL NON-TERMINAL RECONCILE — any hung status whose bot is gone converges to terminal     ║
# ╚══════════════════════════════════════════════════════════════════════════════════════════════╝
# Drives the SHIPPED ``reconcile_stale_nonterminal_sweep`` against the live TestClient (its
# ``post_lifecycle`` = the same /bots/internal/callback/lifecycle the loop POSTs to), so the FSM →
# persist → webhook → ws publish path is exercised end-to-end, no while/sleep wrapper.

from meeting_api.lifecycle.reconcile import reconcile_stale_nonterminal_sweep  # noqa: E402


def _set_updated_now(repo: InMemoryMeetingRepo, meeting_id: int) -> None:
    """Mark a row as JUST-active (recent heartbeat/segment) so the sweep's grace excludes it."""
    from datetime import datetime, timezone
    repo._meetings[meeting_id]["updated_at"] = datetime.now(timezone.utc).isoformat()


def _run_general_sweep(client: TestClient, repo: InMemoryMeetingRepo, *, stop_grace=45.0, active_grace=300.0):
    """Run ONE general sweep exactly as the loop does, posting through the live callback."""
    import logging

    async def _post(body: dict):
        return client.post(ENDPOINT, json=body).status_code

    return asyncio.run(reconcile_stale_nonterminal_sweep(
        repo, None, _post, stop_grace=stop_grace, active_grace=active_grace,
        log=logging.getLogger("test.reconcile"),
    ))


def test_general_reconcile_completes_stale_stopping_and_publishes():
    """A meeting stuck `stopping` with a dead/absent bot past the grace reconciles to `completed`
    AND publishes the bm: + user-scoped status frames (republish must not be bypassed). The
    seeded row carries `stop_requested`, so the completion preserves it for the UI's derived `stopped`."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="stopping")
    repo._meetings[m["id"]]["data"]["stop_requested"] = True  # the stop path set this
    redis = _RecordingRedis()
    client = TestClient(create_app(meeting_repo=repo, redis=redis))

    n = _run_general_sweep(client, repo)
    assert n == 1
    assert asyncio.run(repo.get_status_by_session(session_uid="sess-uid")) == "completed"
    # stop_requested preserved into meeting.data so the UI shows `stopped`.
    assert repo._meetings[m["id"]]["data"].get("stop_requested") is True
    assert repo._meetings[m["id"]]["data"].get("completion_reason") == "stopped"
    # The terminal status frame was published on BOTH channels.
    channels = {c for c, _ in redis.published}
    assert f"bm:meeting:{m['id']}:status" in channels
    assert "u:1:meetings" in channels
    bm = next(p for c, p in redis.published if c == f"bm:meeting:{m['id']}:status")
    assert bm["payload"]["status"] == "completed"


def test_general_reconcile_leaves_live_active_alone():
    """An `active` meeting with a live bot (recent updated_at = recent heartbeat/segment) is NOT
    touched — its age is inside the active grace window."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="active")
    _set_updated_now(repo, m["id"])
    client = TestClient(create_app(meeting_repo=repo))

    n = _run_general_sweep(client, repo)
    assert n == 0
    assert repo._meetings[m["id"]]["status"] == "active"


def test_general_reconcile_completes_stale_active():
    """An `active` meeting gone quiet PAST the active grace (bot exited, no terminal callback) →
    `completed` (the bot WAS live, so not a failure). Seeded row's updated_at is far in the past."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="active")  # default updated_at is 2026-06-20 — well past any grace
    client = TestClient(create_app(meeting_repo=repo))

    n = _run_general_sweep(client, repo)
    assert n == 1
    assert repo._meetings[m["id"]]["status"] == "completed"
    # No stop was requested → completion_reason left_alone, NOT stopped.
    assert repo._meetings[m["id"]]["data"].get("completion_reason") == "left_alone"


def test_general_reconcile_is_idempotent():
    """Re-running the sweep is a no-op on already-terminal rows: the first pass completes the stale
    meeting, the second finds nothing (it's terminal now → not listed) and posts nothing."""
    repo = InMemoryMeetingRepo()
    _seed(repo, status="stopping")
    client = TestClient(create_app(meeting_repo=repo))

    assert _run_general_sweep(client, repo) == 1
    assert _run_general_sweep(client, repo) == 0


def test_general_reconcile_noop_on_terminal_rows():
    """A row already `completed`/`failed` is never listed/touched by the sweep."""
    for status in ("completed", "failed"):
        repo = InMemoryMeetingRepo()
        m = _seed(repo, status=status)
        client = TestClient(create_app(meeting_repo=repo))
        assert _run_general_sweep(client, repo) == 0
        assert repo._meetings[m["id"]]["status"] == status


# ── liveness gate (the correctness fix): a quiet-but-LIVE bot must NOT be reaped on silence alone ────
# The active-reap is keyed on runtime WORKLOAD liveness (RuntimeClient.get_workload), NOT on segment/
# `updated_at` staleness. A live workload (starting/running/stopping) means the bot is in the room even
# if it has produced no segments; a 404 / terminal workload means the bot is gone.

from meeting_api.bot_spawn.fakes import FakeRuntimeClient  # noqa: E402


def _run_general_sweep_rt(client, repo, runtime, *, stop_grace=45.0, active_grace=300.0):
    """Run ONE general sweep with a real RuntimeClient injected (so the liveness gate is exercised)."""
    import logging

    async def _post(body: dict):
        return client.post(ENDPOINT, json=body).status_code

    return asyncio.run(reconcile_stale_nonterminal_sweep(
        repo, runtime, _post, stop_grace=stop_grace, active_grace=active_grace,
        log=logging.getLogger("test.reconcile"),
    ))


def test_quiet_but_live_active_not_reaped_even_past_grace():
    """THE BUG FIX: an `active` meeting gone quiet PAST the active grace (no segments) but whose bot
    WORKLOAD IS STILL ALIVE (runtime reports `running`) is NOT reaped. Silence alone must never kill a
    bot-present meeting."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="active")  # default updated_at far in the past — well past any grace
    repo._meetings[m["id"]]["bot_container_id"] = "wl-live"
    # The workload is alive and running in the runtime — the bot is still in the meeting.
    runtime = FakeRuntimeClient(workloads={"wl-live": {"workloadId": "wl-live", "state": "running"}})
    client = TestClient(create_app(meeting_repo=repo))

    n = _run_general_sweep_rt(client, repo, runtime)
    assert n == 0
    assert repo._meetings[m["id"]]["status"] == "active"  # untouched — bot is present
    assert "wl-live" not in runtime.deleted  # the live bot's workload was NOT torn down


def test_untracked_active_workload_404_is_never_reaped():
    """THE INCIDENT (defect B), FLIPPED FROM THE OLD ASSERTION: a runtime 404 is NOT evidence the
    bot is gone — a recreated runtime (in-memory registry lost) 404s over a LIVE, capturing bot.
    The old sweep completed the meeting on exactly this 404 (posted from 127.0.0.1, zero evidence:
    no exit code, no bot callback) and orphaned the container. Now: the meeting stays `active`,
    nothing is deleted, and the desync is loud in the logs. Evidence (a TRACKED terminal workload,
    or the bot's own callback) still reaps — see the neighbouring tests."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="active")  # past the grace
    repo._meetings[m["id"]]["bot_container_id"] = "wl-untracked"
    # Empty workload map → get_workload("wl-untracked") returns None (404: the kernel doesn't KNOW).
    runtime = FakeRuntimeClient(workloads={})
    client = TestClient(create_app(meeting_repo=repo))

    n = _run_general_sweep_rt(client, repo, runtime)
    assert n == 0, "a 404 must never advance the meeting to completed"
    assert repo._meetings[m["id"]]["status"] == "active"
    assert runtime.deleted == []


def test_dead_active_terminal_workload_state_is_reaped():
    """An `active` meeting whose workload reached a TERMINAL state (`stopped`/`destroyed`) — not just a
    404 — is also reaped: the bot exited without sending its terminal callback."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="active")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-exited"
    runtime = FakeRuntimeClient(workloads={"wl-exited": {"workloadId": "wl-exited", "state": "stopped"}})
    client = TestClient(create_app(meeting_repo=repo))

    n = _run_general_sweep_rt(client, repo, runtime)
    assert n == 1
    assert repo._meetings[m["id"]]["status"] == "completed"


def test_stopping_still_reaps_regardless_of_workload_liveness():
    """A `stopping` meeting (a stop WAS requested) past its short grace still reaps to `completed` even
    if its workload is reported alive — the bot SHOULD be leaving; the stop-reconcile guarantees the kill."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="stopping")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-stopping"
    repo._meetings[m["id"]]["data"]["stop_requested"] = True
    # Even though the workload still reports alive, `stopping` is exempt from the liveness gate.
    runtime = FakeRuntimeClient(
        workloads={"wl-stopping": {"workloadId": "wl-stopping", "state": "running"}}
    )
    client = TestClient(create_app(meeting_repo=repo))

    n = _run_general_sweep_rt(client, repo, runtime)
    assert n == 1
    assert repo._meetings[m["id"]]["status"] == "completed"
    assert repo._meetings[m["id"]]["data"].get("completion_reason") == "stopped"


def test_active_with_unknown_liveness_is_not_reaped():
    """Fail-safe: an `active` meeting with a bot_container_id but an UNRESOLVABLE liveness probe
    (runtime errors) is NOT reaped — never kill a possibly-live meeting on an inconclusive signal."""
    class _BrokenRuntime(FakeRuntimeClient):
        async def get_workload(self, workload_id):
            raise RuntimeError("runtime unreachable")

    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="active")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-x"
    runtime = _BrokenRuntime()
    client = TestClient(create_app(meeting_repo=repo))

    n = _run_general_sweep_rt(client, repo, runtime)
    assert n == 0
    assert repo._meetings[m["id"]]["status"] == "active"


def test_stopping_with_untracked_workload_stays_stopping():
    """Defect C in the general sweep: a `stopping` meeting whose workload the runtime 404s must NOT
    complete — termination is UNCONFIRMED (a live container may be orphaned). It stays `stopping`
    (truthful: the stop is not done), loud in the logs, retried next sweep — the re-adopting
    runtime answers truthfully once booted."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="stopping")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-untracked"
    repo._meetings[m["id"]]["data"]["stop_requested"] = True
    runtime = FakeRuntimeClient(workloads={})   # post-recreate registry: knows nothing
    client = TestClient(create_app(meeting_repo=repo))

    n = _run_general_sweep_rt(client, repo, runtime)
    assert n == 0
    assert repo._meetings[m["id"]]["status"] == "stopping"
    assert runtime.deleted == []


def test_bot_callback_evidence_still_completes_during_runtime_desync():
    """Evidence still advances the FSM even while the runtime registry is desynced: the sweep
    refuses the 404 (no reap), but the bot's OWN terminal lifecycle callback — the primary
    evidence — completes the meeting exactly as before."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="active")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-untracked"
    runtime = FakeRuntimeClient(workloads={})
    client = TestClient(create_app(meeting_repo=repo))

    assert _run_general_sweep_rt(client, repo, runtime) == 0        # 404 → no reap
    assert repo._meetings[m["id"]]["status"] == "active"

    r = client.post(ENDPOINT, json={                                 # the bot's own evidence
        "connection_id": "sess-uid", "status": "completed", "completion_reason": "left_alone",
    })
    assert r.status_code == 200
    assert repo._meetings[m["id"]]["status"] == "completed"


# ── bounded untracked escalation (the zombie-loop fix) ───────────────────────────────────────────
# "Untracked, never reap" is right as a reflex but wrong as a steady state: on the process backend a
# runtime restart kills the workers WITH the runtime (adopt() is a no-op, no callback will ever
# come), so every meeting live across the restart would loop `untracked` + a dead DELETE at error
# level every sweep, forever. The sweep now tracks CONTINUOUS untracked observations per meeting;
# past the window (MEETING_UNTRACKED_GRACE_SEC) with no recovery the meeting advances to `failed`
# carrying the evidence note. Recovery — runtime re-adoption OR a bot heartbeat/callback — resets
# the window, so the escalation only ever fires on a genuinely lost workload.

def _run_general_sweep_esc(client, repo, runtime, tracker, *, untracked_grace,
                           stop_grace=45.0, active_grace=300.0):
    """One general sweep with an INJECTED untracked-tracker + escalation window."""
    import logging

    async def _post_cb(body: dict):
        return client.post(ENDPOINT, json=body).status_code

    return asyncio.run(reconcile_stale_nonterminal_sweep(
        repo, runtime, _post_cb, stop_grace=stop_grace, active_grace=active_grace,
        log=logging.getLogger("test.reconcile"),
        untracked_grace=untracked_grace, untracked_since=tracker,
    ))


def _make_stale(repo, meeting_id) -> None:
    """Push a row's updated_at far into the past (quiet past any grace window)."""
    repo._meetings[meeting_id]["updated_at"] = "2026-06-20T00:00:00+00:00"


def test_untracked_blip_does_not_escalate():
    """A SHORT untracked blip (runtime restarting) must NOT escalate: the runtime re-adopts the
    workload, the probe answers alive again, and the window resets — even with a zero grace."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="active")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-blip"
    runtime = FakeRuntimeClient(workloads={})          # runtime just restarted: knows nothing
    client = TestClient(create_app(meeting_repo=repo))
    tracker: dict = {}

    assert _run_general_sweep_esc(client, repo, runtime, tracker, untracked_grace=0.0) == 0
    assert repo._meetings[m["id"]]["status"] == "active"   # first observation only opens the window
    assert m["id"] in tracker

    # The runtime finishes booting and RE-ADOPTS the live bot — the probe answers alive again.
    runtime._workloads["wl-blip"] = {"workloadId": "wl-blip", "state": "running"}
    assert _run_general_sweep_esc(client, repo, runtime, tracker, untracked_grace=0.0) == 0
    assert repo._meetings[m["id"]]["status"] == "active"
    assert tracker == {}, "recovery must reset the untracked window"

    # A LATER desync starts a FRESH window — the old blip never counts toward it.
    runtime._workloads.pop("wl-blip")
    assert _run_general_sweep_esc(client, repo, runtime, tracker, untracked_grace=0.0) == 0
    assert repo._meetings[m["id"]]["status"] == "active"


def test_continuous_untracked_past_window_escalates_once_with_evidence():
    """CONTINUOUS untracked past the window (runtime restart on the process backend: the workers
    died with it, no callback will ever come) escalates the meeting to `failed` EXACTLY ONCE, with
    the evidence note recorded on the transition — and the 15s error/DELETE loop stops (the
    terminal row leaves the sweep's listing)."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="active")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-lost"
    runtime = FakeRuntimeClient(workloads={})          # untracked forever — nothing will re-adopt
    client = TestClient(create_app(meeting_repo=repo))
    tracker: dict = {}

    assert _run_general_sweep_esc(client, repo, runtime, tracker, untracked_grace=0.0) == 0
    assert repo._meetings[m["id"]]["status"] == "active"   # window opened, not yet elapsed

    n = _run_general_sweep_esc(client, repo, runtime, tracker, untracked_grace=0.0)
    assert n == 1, "continuous untracked past the window must escalate"
    assert repo._meetings[m["id"]]["status"] == "failed"
    assert runtime.deleted == []                            # nothing was blindly torn down
    assert tracker == {}
    # The evidence note rides the transition: what was unaccountable, and the policy applied.
    data = repo._meetings[m["id"]]["data"]
    trail = data.get("status_transition", [])
    assert any("presumed lost" in (t.get("reason") or "") for t in trail), trail

    # Exactly once: the terminal row is no longer listed — the loop has converged.
    assert _run_general_sweep_esc(client, repo, runtime, tracker, untracked_grace=0.0) == 0
    assert repo._meetings[m["id"]]["status"] == "failed"


def test_stopping_untracked_past_window_escalates_too():
    """The `stopping` zombie (user pressed Stop, then the runtime restarted on the process backend):
    the teardown stays unconfirmable (404) forever — past the window it converges to `failed`
    instead of retrying the dead DELETE every sweep for eternity."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="stopping")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-gone"
    repo._meetings[m["id"]]["data"]["stop_requested"] = True
    runtime = FakeRuntimeClient(workloads={})
    client = TestClient(create_app(meeting_repo=repo))
    tracker: dict = {}

    assert _run_general_sweep_esc(client, repo, runtime, tracker, untracked_grace=0.0) == 0
    assert repo._meetings[m["id"]]["status"] == "stopping"  # window opened

    assert _run_general_sweep_esc(client, repo, runtime, tracker, untracked_grace=0.0) == 1
    assert repo._meetings[m["id"]]["status"] == "failed"
    assert runtime.deleted == []


def test_bot_callback_mid_window_cancels_escalation():
    """A sign of life mid-window — the bot's callback/heartbeat bumping the row — cancels the
    escalation: the row leaves the stale listing, the window resets, and a LATER quiet spell starts
    from zero. The meeting is never failed under a live bot."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="active")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-alive"
    runtime = FakeRuntimeClient(workloads={})
    client = TestClient(create_app(meeting_repo=repo))
    tracker: dict = {}

    assert _run_general_sweep_esc(client, repo, runtime, tracker, untracked_grace=0.0) == 0
    assert m["id"] in tracker                               # window opened

    # Mid-window the bot proves it is alive (its callback/heartbeat bumps the row's updated_at —
    # exactly what the receiver's persist does): the row is no longer stale.
    _set_updated_now(repo, m["id"])
    assert _run_general_sweep_esc(client, repo, runtime, tracker, untracked_grace=0.0) == 0
    assert tracker == {}, "a bot sign-of-life must cancel the pending escalation"
    assert repo._meetings[m["id"]]["status"] == "active"

    # The meeting goes quiet again LATER, still untracked: a fresh window — no instant escalation
    # from the stale pre-callback observation.
    _make_stale(repo, m["id"])
    assert _run_general_sweep_esc(client, repo, runtime, tracker, untracked_grace=0.0) == 0
    assert repo._meetings[m["id"]]["status"] == "active"


# ── Bug 1: runtime `destroyed` callback for a `stopping` meeting is TERMINAL EVIDENCE ────────────
# The reaper-loop incident: DELETE /bots → runtime deletes the workload (DELETE /workloads/{id} →
# 200) and posts a runtime callback with state=destroyed — but meeting-api NEVER advanced the meeting
# out of `stopping`. It re-logged `runtime_callback ... destroyed` and re-issued DELETE (now 404)
# every ~15s FOREVER. The runtime's confirmed destroy IS terminal evidence (#50's principle: real
# evidence, not a bare 404) → advance the meeting: `completed` if it ever reached active (`stopping`/
# `active`/`needs_help`), `failed` if it never did (pre-active). The /runtime/callback route consumes
# it via synthesize_terminal_for_dead_workload, driven through the bot's OWN lifecycle callback (POST
# to /bots/internal/callback/lifecycle — exercised in-process here, like _run_general_sweep_rt).

from meeting_api.lifecycle.reconcile import synthesize_terminal_for_dead_workload  # noqa: E402


def _consume_runtime_terminal(client, repo, workload_id, state):
    """Drive synthesize_terminal_for_dead_workload with the bot's OWN lifecycle callback wired to the
    in-process FSM endpoint (the prod _drive_terminal POSTs to 127.0.0.1:PORT, unreachable under the
    TestClient — so we inject the client.post here, exactly as the reconcile-sweep tests do)."""
    import logging

    async def _drive(body: dict):
        return client.post(ENDPOINT, json=body).status_code

    return asyncio.run(synthesize_terminal_for_dead_workload(
        repo, workload_id, state, _drive, log=logging.getLogger("test.runtime-cb"),
    ))


def test_runtime_destroyed_completes_stopping_meeting_and_stops_reaper():
    """A `stopping` meeting whose workload the runtime confirms `destroyed` (its bot never sent its own
    terminal callback — e.g. SIGKILLed at teardown) advances to `completed` on that evidence. On the
    PRE-FIX code this FAILED: the runtime callback only handled pre-active rows, so the meeting stayed
    `stopping` and the stop-reconcile sweep re-DELETEd forever. Once terminal it leaves the stale-
    stopping listing → the reaper loop stops."""
    repo = _ReconcileRepo()
    m = _seed(repo, status="stopping")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-stop"
    client = TestClient(create_app(meeting_repo=repo))

    # The reaper WOULD list this meeting while it is `stopping` …
    assert repo.list_stale_stopping_sync() == [(m["id"], "sess-uid", "wl-stop")]

    # … until the runtime's destroy evidence advances it out of `stopping`.
    assert _consume_runtime_terminal(client, repo, "wl-stop", "destroyed") is True
    assert repo._meetings[m["id"]]["status"] == "completed"
    assert repo._meetings[m["id"]]["data"].get("completion_reason") == "stopped"

    # Reaper loop stops: a completed row is no longer stale-stopping.
    assert repo.list_stale_stopping_sync() == []


def test_runtime_destroyed_completes_active_meeting():
    """An `active` meeting whose workload is runtime-confirmed `destroyed` WITHOUT its own terminal
    callback (killed before it could POST `completed`) also completes — it reached active, so
    `completed` (reason left_alone: the workload simply vanished while live)."""
    repo = _ReconcileRepo()
    m = _seed(repo, status="active")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-act"
    client = TestClient(create_app(meeting_repo=repo))

    assert _consume_runtime_terminal(client, repo, "wl-act", "destroyed") is True
    assert repo._meetings[m["id"]]["status"] == "completed"
    assert repo._meetings[m["id"]]["data"].get("completion_reason") == "left_alone"


def test_runtime_exited_also_completes_stopping():
    """`exited` is terminal evidence too (not just `destroyed`) — the runtime reports the workload gone."""
    repo = _ReconcileRepo()
    m = _seed(repo, status="stopping")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-exit"
    client = TestClient(create_app(meeting_repo=repo))

    assert _consume_runtime_terminal(client, repo, "wl-exit", "exited") is True
    assert repo._meetings[m["id"]]["status"] == "completed"


def test_runtime_destroyed_fails_pre_active_meeting():
    """Distinguish never-active from was-active: a PRE-ACTIVE row (`awaiting_admission` — killed in the
    waiting room before it could report active) → `failed`, not `completed` (CC5 preserved)."""
    repo = _ReconcileRepo()
    m = _seed(repo, status="awaiting_admission")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-wait"
    client = TestClient(create_app(meeting_repo=repo))

    assert _consume_runtime_terminal(client, repo, "wl-wait", "destroyed") is True
    assert repo._meetings[m["id"]]["status"] == "failed"
    assert repo._meetings[m["id"]]["data"].get("failure_stage") == "awaiting_admission"


def test_runtime_destroyed_noop_on_already_terminal_meeting():
    """A normal teardown destroys the workload AFTER the bot's own `completed` already landed — the
    runtime callback must be a no-op there (never re-open or re-fail a terminal meeting)."""
    repo = _ReconcileRepo()
    m = _seed(repo, status="active")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-done"
    client = TestClient(create_app(meeting_repo=repo))
    # The bot completes it itself first.
    client.post(ENDPOINT, json={"connection_id": "sess-uid", "status": "completed",
                                "completion_reason": "left_alone"})
    assert repo._meetings[m["id"]]["status"] == "completed"

    # The trailing runtime destroy is a no-op (stays completed, drives nothing).
    assert _consume_runtime_terminal(client, repo, "wl-done", "destroyed") is False
    assert repo._meetings[m["id"]]["status"] == "completed"


def test_runtime_nonterminal_state_does_not_advance_meeting():
    """A non-terminal runtime state (`running`) is NOT evidence of anything — the meeting is untouched."""
    repo = _ReconcileRepo()
    m = _seed(repo, status="stopping")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-run"
    client = TestClient(create_app(meeting_repo=repo))

    assert _consume_runtime_terminal(client, repo, "wl-run", "running") is False
    assert repo._meetings[m["id"]]["status"] == "stopping"


def test_runtime_destroyed_unknown_workload_is_noop():
    """A `destroyed` for a workload id no meeting owns → no-op (never fabricate a terminal)."""
    repo = _ReconcileRepo()
    m = _seed(repo, status="stopping")
    repo._meetings[m["id"]]["bot_container_id"] = "wl-known"
    client = TestClient(create_app(meeting_repo=repo))

    assert _consume_runtime_terminal(client, repo, "ghost-wl", "destroyed") is False
    assert repo._meetings[m["id"]]["status"] == "stopping"


# ── Bug 3: a TERMINAL meeting emits `session_end` on tc:meeting:{native} → the copilot worker reaps ─
# Six vexa-worker-meet-<native> copilot workers stayed up for HOURS after their meetings ended: the
# ONLY reap signal the worker honours is a `session_end` marker on its transcript feed
# `tc:meeting:{native}` (agent worker/meeting.py) OR its VEXA_IDLE_TIMEOUT_SEC (default 4h). When the
# bot never emitted `session_end` (SIGKILLed, or stopped in the waiting room — Bug 2), the worker sat
# idle for the full 4h. The lifecycle fix: on a terminal FSM advance, meeting-api emits the session_end
# marker onto the same feed, reaping the copilot immediately regardless of how the bot died.

class _StreamRecordingRedis:
    """A redis fake that records BOTH publishes and xadds (the copilot-reap path uses xadd)."""

    def __init__(self):
        self.published: list[tuple[str, dict]] = []
        self.streams: dict[str, list[dict]] = {}

    async def publish(self, channel: str, data: str):
        self.published.append((channel, json.loads(data)))
        return 1

    async def xadd(self, stream: str, payload: dict):
        self.streams.setdefault(stream, []).append(payload)
        return f"{len(self.streams[stream])}-0"


def _drive_terminal_seam(client, connection_id="sess-uid", *, terminal="completed"):
    """Drive a fresh record joining→active→terminal (or joining→failed) over the HTTP seam."""
    hops = (["joining", "active", "completed"] if terminal == "completed" else ["joining", "failed"])
    for st in hops:
        ev = {"connection_id": connection_id, "status": st}
        if st == "completed":
            ev["completion_reason"] = "stopped"
        if st == "failed":
            ev["completion_reason"] = "join_failure"
            ev["exit_code"] = 1
        r = _post(client, **ev)
        assert r.status_code == 200, r.text


def test_terminal_meeting_emits_session_end_to_reap_copilot():
    """On `completed`, meeting-api xadds a session_end marker to tc:meeting:{native} — the copilot
    worker's reap signal. Keyed by the meeting's native id (matches the collector + worker feed key)."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo, status="requested")   # native_meeting_id == "m1" (per _seed)
    redis = _StreamRecordingRedis()
    client = TestClient(create_app(meeting_repo=repo, redis=redis))

    _drive_terminal_seam(client, terminal="completed")

    stream = "tc:meeting:m1"
    assert stream in redis.streams, f"no session_end emitted; streams={list(redis.streams)}"
    markers = [p for p in redis.streams[stream] if p.get("type") == "session_end"]
    assert len(markers) == 1
    assert markers[0]["uid"] == "m1"


def test_failed_meeting_also_reaps_copilot():
    """A meeting that terminates `failed` (e.g. the bot never got admitted) ALSO emits session_end —
    a copilot armed for it must not linger for the idle window either."""
    repo = InMemoryMeetingRepo()
    _seed(repo, status="requested")
    redis = _StreamRecordingRedis()
    client = TestClient(create_app(meeting_repo=repo, redis=redis))

    _drive_terminal_seam(client, terminal="failed")

    markers = [p for p in redis.streams.get("tc:meeting:m1", []) if p.get("type") == "session_end"]
    assert len(markers) == 1


def test_non_terminal_advance_does_not_emit_session_end():
    """A non-terminal advance (joining/active) must NOT reap the copilot — the meeting is still live."""
    repo = InMemoryMeetingRepo()
    _seed(repo, status="requested")
    redis = _StreamRecordingRedis()
    client = TestClient(create_app(meeting_repo=repo, redis=redis))

    _post(client, connection_id="sess-uid", status="joining")
    _post(client, connection_id="sess-uid", status="active")

    assert "tc:meeting:m1" not in redis.streams, "session_end emitted while the meeting is still live"


def test_idempotent_terminal_replay_does_not_double_reap():
    """A redelivered terminal (no_op) must NOT xadd a second session_end (the reap is once-per-advance)."""
    repo = InMemoryMeetingRepo()
    _seed(repo, status="requested")
    redis = _StreamRecordingRedis()
    client = TestClient(create_app(meeting_repo=repo, redis=redis))

    _drive_terminal_seam(client, terminal="completed")
    # Redeliver the terminal (the bot retries its terminal callback up to 3x) — an idempotent 200 no-op.
    r = _post(client, connection_id="sess-uid", status="completed", completion_reason="stopped")
    assert r.status_code == 200

    markers = [p for p in redis.streams.get("tc:meeting:m1", []) if p.get("type") == "session_end"]
    assert len(markers) == 1, f"double reap on idempotent replay: {markers}"
