"""Several meeting-api replicas over one meetings row.

Prod runs meeting-api with replicaCount 3, and each replica holds its own in-memory MeetingStore. A
replica whose cached record is behind the row must catch up from the row before it applies an event.
Otherwise a LEGAL edge from its stale state (joining -> failed) is applied, its own shorter trail is
written over the one another replica persisted, and failure_stage is derived from the wrong stage.

Hosted prod, 72 h to 4 Nov 2026: 37 of 148 Google Meet lobby timeouts lost their awaiting_admission
step this way (failure_stage read `joining` while the bot's own join_evidence said awaiting_admission).
The same hole can drop the producer-observed admission (-> active) entry, which is the billing clock.

Each test drives two apps (two stores) over ONE InMemoryMeetingRepo, through the shipped handler.
"""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo

ENDPOINT = "/bots/internal/callback/lifecycle"
SESSION = "sess-uid"


def _seed(repo: InMemoryMeetingRepo) -> dict:
    m = asyncio.run(
        repo.create_meeting(user_id=1, platform="google_meet", native_meeting_id="abc-defg-hij", data={})
    )
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=SESSION))
    repo.set_status(m["id"], "requested")
    return m


def _replica(repo: InMemoryMeetingRepo) -> TestClient:
    """One meeting-api process: its own app, so its own empty in-memory MeetingStore."""
    return TestClient(create_app(open_callbacks=True, meeting_repo=repo))


def _post(client: TestClient, ts: str, **body):
    return client.post(ENDPOINT, json={"connection_id": SESSION, "timestamp": ts, **body})


def _data(repo: InMemoryMeetingRepo, m: dict) -> dict:
    return repo._meetings[m["id"]]["data"]


def _hops(repo: InMemoryMeetingRepo, m: dict) -> list:
    return [(e.get("from"), e.get("to")) for e in _data(repo, m).get("status_transition", [])]


def test_lobby_timeout_on_a_stale_replica_keeps_the_lobby_step(goldens):
    """joining lands on R1, awaiting_admission on R2, the lobby-timeout `failed` back on R1.
    The row must keep the lobby step and say the bot failed while awaiting admission."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo)
    r1, r2 = _replica(repo), _replica(repo)

    assert _post(r1, "2026-11-04T10:00:00.000Z", status="joining").status_code == 200
    assert _post(r2, "2026-11-04T10:00:20.000Z", status="awaiting_admission").status_code == 200
    lobby_timeout = {**goldens["failed-join-evidence"], "timestamp": "2026-11-04T10:15:20.000Z"}
    r = r1.post(ENDPOINT, json=lobby_timeout)

    assert r.status_code == 200, r.text
    assert _hops(repo, m) == [
        (None, "joining"),
        ("joining", "awaiting_admission"),
        ("awaiting_admission", "failed"),
    ]
    assert _data(repo, m)["failure_stage"] == "awaiting_admission"


def test_terminal_on_a_stale_replica_keeps_the_admission_entry():
    """The billing clock: R1 saw only joining; R2 saw the lobby and R3 the admission (-> active);
    the bot's `failed` lands on R1. The admitted entry, with its producer timestamp, must survive."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo)
    r1, r2, r3 = _replica(repo), _replica(repo), _replica(repo)

    assert _post(r1, "2026-11-04T10:00:00.000Z", status="joining").status_code == 200
    assert _post(r2, "2026-11-04T10:00:20.000Z", status="awaiting_admission").status_code == 200
    assert _post(r3, "2026-11-04T10:01:10.000Z", status="active").status_code == 200
    r = _post(r1, "2026-11-04T10:30:00.000Z", status="failed", exit_code=1, reason="crash")

    assert r.status_code == 200, r.text
    trail = _data(repo, m)["status_transition"]
    assert [(e["from"], e["to"]) for e in trail] == [
        (None, "joining"),
        ("joining", "awaiting_admission"),
        ("awaiting_admission", "active"),
        ("active", "failed"),
    ]
    admitted = trail[2]
    assert admitted["timestamp"] == "2026-11-04T10:01:10.000Z"
    assert admitted["timestamp_source"] == "producer"
    assert _data(repo, m)["failure_stage"] == "active"


def test_non_terminal_event_on_a_stale_replica_keeps_the_lobby_step():
    """Not only terminals: `active` landing on the replica that saw only `joining` must not drop the
    awaiting_admission step another replica wrote."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo)
    r1, r2 = _replica(repo), _replica(repo)

    assert _post(r1, "2026-11-04T10:00:00.000Z", status="joining").status_code == 200
    assert _post(r2, "2026-11-04T10:00:20.000Z", status="awaiting_admission").status_code == 200
    assert _post(r1, "2026-11-04T10:01:10.000Z", status="active").status_code == 200

    assert _hops(repo, m) == [
        (None, "joining"),
        ("joining", "awaiting_admission"),
        ("awaiting_admission", "active"),
    ]


def test_a_lagging_row_never_regresses_a_replica_that_is_ahead():
    """The catch-up is monotonic: a row whose trail is SHORTER than the replica's own record (a write
    still landing, or a stale read) is ignored, and the replica's record stands."""
    repo = InMemoryMeetingRepo()
    m = _seed(repo)
    r1 = _replica(repo)

    assert _post(r1, "2026-11-04T10:00:00.000Z", status="joining").status_code == 200
    assert _post(r1, "2026-11-04T10:00:20.000Z", status="awaiting_admission").status_code == 200
    assert _post(r1, "2026-11-04T10:01:10.000Z", status="active").status_code == 200
    # The row lags: status and trail as they were after `joining` only.
    row = repo._meetings[m["id"]]
    row["status"] = "joining"
    row["data"]["status_transition"] = row["data"]["status_transition"][:1]

    r = _post(r1, "2026-11-04T10:30:00.000Z", status="completed", exit_code=0, completion_reason="stopped")

    assert r.status_code == 200, r.text
    assert _hops(repo, m) == [
        (None, "joining"),
        ("joining", "awaiting_admission"),
        ("awaiting_admission", "active"),
        ("active", "completed"),
    ]
