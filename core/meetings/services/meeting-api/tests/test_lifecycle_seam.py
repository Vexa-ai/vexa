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

    def list_stale_stopping_sync(self) -> list[tuple[int, str]]:
        out: dict[int, str] = {}
        # latest session per meeting (mirror the SQL adapter's MeetingSession.id desc)
        for s in reversed(self.sessions):
            mid = s["meeting_id"]
            row = self._meetings.get(mid)
            if row is None or row["status"] != "stopping":
                continue
            if mid not in out:
                out[mid] = s["session_uid"]
        return list(out.items())

    async def list_stale_stopping(self, *, older_than_seconds: float) -> list[tuple[int, str]]:
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
    for meeting_id, session_uid in repo.list_stale_stopping_sync():
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
