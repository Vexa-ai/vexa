"""Google Meet refusal → the next bot pod, on the same meeting (lifecycle/meet_respawn.py).

OFFLINE — the shipped ``request_bot``, the lifecycle and runtime callbacks, the stop route and the
respawn sweep over the in-memory fakes (no DB, no kernel).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn import MaxBotsExceeded, request_bot
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.lifecycle import meet_respawn
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher

SECRET = "test-admin-token"
USER = 7
NID = "abc-defg-hij"
KW = dict(redis_url="r", token_secret=SECRET, meeting_api_url="http://meeting-api:8080")
LIFECYCLE = "/bots/internal/callback/lifecycle"
REFUSAL = ("google_meet_join_refused: Google Meet refused the join on 5 of 5 knocks "
           "(CreateMeetingDevice answered HTTP 403); the host never received a request to admit the bot")


def _run(coro):
    return asyncio.run(coro)


def _spawn(repo, runtime, platform="google_meet", **kw):
    return _run(request_bot(repo, runtime, user_id=USER, platform=platform, native_meeting_id=NID,
                            bot_name="Notetaker", **KW, **kw))


def _session(repo, mid, i=-1):
    return [s["session_uid"] for s in repo.sessions if s["meeting_id"] == mid][i]


def _event(sid, status, **extra):
    return {"connection_id": sid, "status": status, "timestamp": "2026-11-02T10:00:00.000Z", **extra}


def _refused(sid, node="node-a"):
    return _event(
        sid, "failed", exit_code=1, failure_stage="joining",
        completion_reason="awaiting_admission_rejected", reason=REFUSAL,
        join_evidence={"reason": "awaiting_admission_rejected", "attribution": "platform_policy",
                       "source": "bot", "stage": "joining", "detail": REFUSAL, "node": node},
    )


def _due(repo, runtime, *, after_s=120):
    later = datetime.now(timezone.utc) + timedelta(seconds=after_s)
    return _run(meet_respawn.respawn_due(repo, runtime, token_secret=SECRET, redis_url="r", now=later))


@pytest.fixture
def world():
    repo, runtime, pub = InMemoryMeetingRepo(), FakeRuntimeClient(), InMemoryCommandPublisher()
    app = create_app(open_callbacks=True, meeting_repo=repo, runtime=runtime, command_publisher=pub)
    return repo, runtime, TestClient(app, raise_server_exceptions=False)


def _joining(world):
    repo, runtime, client = world
    m = _spawn(repo, runtime)
    sid = _session(repo, m["id"])
    assert client.post(LIFECYCLE, json=_event(sid, "joining")).status_code == 200
    return m["id"], sid


# ── the policy ──────────────────────────────────────────────────────────────────────────────────

def test_policy_defaults_and_bounds(monkeypatch):
    pol = meet_respawn.policy()
    assert (pol.pods, pol.delays_s, pol.window_s) == (3, (30.0, 60.0), 900.0)
    assert [pol.delay_before(n) for n in (2, 3, 4)] == [30.0, 60.0, 60.0]
    monkeypatch.setenv("VEXA_MEET_RESPAWN_PODS", "9")          # out of 1..5 → default
    monkeypatch.setenv("VEXA_MEET_RESPAWN_DELAYS_S", "10,x")   # unparsable → default
    monkeypatch.setenv("VEXA_MEET_RESPAWN_WINDOW_S", "5")      # out of 60..1800 → default
    pol = meet_respawn.policy()
    assert (pol.pods, pol.delays_s, pol.window_s) == (3, (30.0, 60.0), 900.0)
    monkeypatch.setenv("VEXA_MEET_RESPAWN_PODS", "1")
    assert not meet_respawn.policy().enabled


def test_only_a_meet_refusal_qualifies():
    assert meet_respawn.is_meet_refusal(_refused("s"))
    assert not meet_respawn.is_meet_refusal(_event("s", "failed", reason="host did not admit"))
    assert not meet_respawn.is_meet_refusal({**_refused("s"), "status": "completed"})


# ── the first spawn ─────────────────────────────────────────────────────────────────────────────

def test_an_anonymous_meet_spawn_carries_the_block_and_an_unchanged_spec(world):
    repo, runtime, _ = world
    m = _spawn(repo, runtime, language="de")
    block = repo._meetings[m["id"]]["data"]["meet_respawn"]
    assert block["state"] == "running" and block["attempts"] == []
    assert block["spawn"] == {"bot_name": "Notetaker", "language": "de", "transcription_tier": "realtime"}
    assert "avoidNodes" not in runtime.specs[0]


def test_other_platforms_and_a_disabled_policy_carry_no_block(world, monkeypatch):
    repo, runtime, _ = world
    z = _spawn(repo, runtime, platform="zoom", meeting_url="https://zoom.us/j/123456789")
    assert "meet_respawn" not in repo._meetings[z["id"]]["data"]
    monkeypatch.setenv("VEXA_MEET_RESPAWN_PODS", "1")
    repo2 = InMemoryMeetingRepo()
    g = _spawn(repo2, runtime)
    assert "meet_respawn" not in repo2._meetings[g["id"]]["data"]


# ── a refusal schedules the next pod; the meeting stays joining ─────────────────────────────────

def test_refusal_keeps_the_meeting_joining_and_the_next_pod_avoids_the_node(world):
    repo, runtime, client = world
    mid, sid1 = _joining(world)
    first_workload = repo._meetings[mid]["bot_container_id"]

    r = client.post(LIFECYCLE, json=_refused(sid1, node="node-a"))
    assert r.status_code == 200 and r.json()["respawn"] == "scheduled", r.text
    row = repo._meetings[mid]
    assert row["status"] == "joining", "nobody is told the meeting failed"
    block = row["data"]["meet_respawn"]
    assert block["state"] == "pending" and len(block["attempts"]) == 1
    assert block["attempts"][0]["node"] == "node-a"
    assert block["attempts"][0]["workload_id"] == first_workload

    # The refused pod exits; the runtime reports it destroyed. That is the attempt's end, not the meeting's.
    r = client.post("/runtime/callback", json={"workloadId": first_workload, "state": "destroyed"})
    assert r.status_code == 200
    assert repo._meetings[mid]["status"] == "joining"

    # Not due yet → nothing starts.
    assert _due(repo, runtime, after_s=0) == 0
    assert _due(repo, runtime) == 1
    assert len(runtime.specs) == 2
    assert runtime.specs[1]["avoidNodes"] == ["node-a"]
    row = repo._meetings[mid]
    assert row["status"] == "joining"
    assert row["data"]["meet_respawn"]["state"] == "running"
    sid2 = _session(repo, mid)
    assert sid2 != sid1 and row["bot_container_id"] != first_workload

    # The new bot reports in over the same row and is admitted.
    assert client.post(LIFECYCLE, json=_event(sid2, "joining")).status_code == 200
    r = client.post(LIFECYCLE, json=_event(sid2, "active"))
    assert r.status_code == 200, r.text
    assert repo._meetings[mid]["status"] == "active"


def test_the_last_pod_applies_the_refusal(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    for pod, node in enumerate(("node-a", "node-b"), start=2):
        assert client.post(LIFECYCLE, json=_refused(sid, node=node)).json().get("respawn") == "scheduled"
        assert _due(repo, runtime) == 1
        sid = _session(repo, mid)
        assert client.post(LIFECYCLE, json=_event(sid, "joining")).status_code == 200
    assert runtime.specs[2]["avoidNodes"] == ["node-a", "node-b"]

    r = client.post(LIFECYCLE, json=_refused(sid, node="node-c"))
    assert r.status_code == 200 and "respawn" not in r.json(), r.text
    row = repo._meetings[mid]
    assert row["status"] == "failed"
    assert row["data"]["completion_reason"] == "platform_join_refused"
    assert row["data"]["join_evidence"]["reason"] == "platform_join_refused"
    assert row["data"]["join_evidence"]["attribution"] == "system_fault"
    assert row["data"]["meet_respawn"]["state"] == "exhausted"
    assert len(row["data"]["meet_respawn"]["attempts"]) == 3
    assert len(runtime.specs) == 3, "three pods, no fourth"


def test_no_pod_starts_past_the_window(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    old = (datetime.now(timezone.utc) - timedelta(seconds=880)).isoformat()
    repo._meetings[mid]["data"]["meet_respawn"]["first_at"] = old   # +30 s would pass 900 s
    r = client.post(LIFECYCLE, json=_refused(sid))
    assert "respawn" not in r.json()
    assert repo._meetings[mid]["status"] == "failed"


def test_a_late_report_from_an_older_bot_changes_nothing(world):
    repo, runtime, client = world
    mid, sid1 = _joining(world)
    client.post(LIFECYCLE, json=_refused(sid1))
    _due(repo, runtime)
    before = repo._meetings[mid]["data"]["meet_respawn"]
    r = client.post(LIFECYCLE, json=_refused(sid1))   # the first bot's redelivered terminal
    assert "respawn" not in r.json()
    assert repo._meetings[mid]["data"]["meet_respawn"] == before


def test_a_refusal_after_the_lobby_is_not_respawned(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    client.post(LIFECYCLE, json=_event(sid, "awaiting_admission"))
    r = client.post(LIFECYCLE, json=_refused(sid))
    assert "respawn" not in r.json()
    assert repo._meetings[mid]["status"] == "failed"


def test_a_host_decision_is_not_respawned(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    r = client.post(LIFECYCLE, json=_event(sid, "failed", failure_stage="joining",
                                           completion_reason="join_failure", reason="navigation failed"))
    assert "respawn" not in r.json()
    assert repo._meetings[mid]["status"] == "failed"


# ── stop between pods ───────────────────────────────────────────────────────────────────────────

def test_stop_between_pods_ends_the_meeting_and_starts_nothing(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    client.post(LIFECYCLE, json=_refused(sid))
    r = client.delete(f"/bots/google_meet/{NID}", headers={"x-user-id": str(USER)})
    assert r.status_code in (200, 202), r.text
    row = repo._meetings[mid]
    assert row["status"] == "failed"
    assert row["data"]["completion_reason"] == "stopped"
    assert row["data"]["meet_respawn"]["state"] == "cancelled"
    assert row["data"]["join_evidence"]["reason"] == "stopped_while_joining"
    assert row["data"]["join_evidence"]["attribution"] == "user_action"
    assert _due(repo, runtime) == 0
    assert len(runtime.specs) == 1


def test_a_stop_flag_landing_before_the_spawn_fences_it(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    client.post(LIFECYCLE, json=_refused(sid))
    repo._meetings[mid]["data"]["stop_requested"] = True   # the stop's write, before the sweep
    _due(repo, runtime)
    assert len(runtime.specs) == 1, "the fence refused the workload"
    row = repo._meetings[mid]
    assert row["status"] == "failed" and row["data"]["completion_reason"] == "stopped"


def test_a_next_pod_that_cannot_start_fails_the_meeting(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    client.post(LIFECYCLE, json=_refused(sid))
    runtime._fail = True
    assert _due(repo, runtime) == 0
    assert repo._meetings[mid]["status"] == "failed"


# ── what the person sees and pays for between pods ──────────────────────────────────────────────

def _failed_envelopes(client):
    return [e for e in client.app.state.status_change_webhooks
            if e["data"]["status_change"]["new_status"] == "failed"]


def test_between_pods_no_failed_event_and_no_billable_time(world):
    """No meeting.status_change to `failed` until the last pod; the meeting never reaches `active`,
    so it never gets a start_time or an admission transition (the billable clock's start)."""
    repo, runtime, client = world
    mid, sid = _joining(world)
    for _ in range(2):
        client.post(LIFECYCLE, json=_refused(sid))
        assert _failed_envelopes(client) == [], "nobody is told the meeting failed between pods"
        assert repo._meetings[mid]["status"] == "joining"
        _due(repo, runtime)
        sid = _session(repo, mid)
        client.post(LIFECYCLE, json=_event(sid, "joining"))
    client.post(LIFECYCLE, json=_refused(sid))
    assert len(_failed_envelopes(client)) == 1, "exactly one failed event, at the end"
    row = repo._meetings[mid]
    assert row.get("start_time") is None
    trail = row["data"].get("status_transition") or []
    assert not [t for t in trail if t.get("to") == "active"], "no admission, so nothing billable"


def test_a_meeting_between_pods_holds_one_slot(world):
    """The respawn reuses the meeting's own slot (no cap check, one row); the slot stays taken, so a
    second meeting at a 1-bot ceiling is refused while pods come and go."""
    repo, runtime, client = world
    m = _spawn(repo, runtime, max_concurrent=1)
    sid = _session(repo, m["id"])
    client.post(LIFECYCLE, json=_event(sid, "joining"))
    client.post(LIFECYCLE, json=_refused(sid))
    assert _run(repo.count_active_bots(user_id=USER)) == 1
    with pytest.raises(MaxBotsExceeded):
        _run(request_bot(repo, runtime, user_id=USER, platform="google_meet",
                         native_meeting_id="other-room-xyz", max_concurrent=1, **KW))
    assert _due(repo, runtime) == 1, "the respawn is not refused by the ceiling it already holds"
    assert _run(repo.count_active_bots(user_id=USER)) == 1
    assert len([s for s in repo.sessions if s["meeting_id"] == m["id"]]) == 2


def test_a_refusal_with_no_respawn_is_still_typed_ours(world, monkeypatch):
    """A refusal that is not retried (respawn off) lands typed, graded ours — not a host decision."""
    monkeypatch.setenv("VEXA_MEET_RESPAWN_PODS", "1")
    repo, runtime, client = world
    mid, sid = _joining(world)
    r = client.post(LIFECYCLE, json=_refused(sid))
    assert "respawn" not in r.json()
    data = repo._meetings[mid]["data"]
    assert data["completion_reason"] == "platform_join_refused"
    assert data["join_evidence"]["attribution"] == "system_fault"


def test_a_host_rejection_keeps_its_reason(world):
    repo, runtime, client = world
    mid, sid = _joining(world)
    client.post(LIFECYCLE, json=_event(sid, "awaiting_admission"))
    client.post(LIFECYCLE, json=_event(sid, "failed", failure_stage="awaiting_admission",
                                       completion_reason="awaiting_admission_rejected",
                                       reason="the host denied the request to join"))
    assert repo._meetings[mid]["data"]["completion_reason"] == "awaiting_admission_rejected"
