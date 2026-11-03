"""ADR-0052 s1 — the bot waits for its join time.

The spawn path carries an optional ``join_at`` into invocation.v1 ``joinAt`` and the row's
``data.join_at``, and refuses one later than now + ``BOOKED_POD_LEAD_S`` + 60 s before any row or
workload exists. The reconciler measures a waiting ``requested`` row's pre-active window from its
join time, so a pod that is quiet by design is not reaped as stuck.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from meeting_api.bot_spawn import request_bot
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.invocation import conforms_invocation
from meeting_api.bot_spawn.ports import JoinAtRefused, reconcile_reference_time
from meeting_api.bot_spawn.service import _checked_join_at, booked_pod_lead_s

SECRET = "test-admin-token"
USER = 7


@pytest.fixture(autouse=True)
def _stt(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.vexa.ai")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    monkeypatch.delenv("BOOKED_POD_LEAD_S", raising=False)


async def _spawn(repo, runtime, **kw):
    return await request_bot(
        repo, runtime, user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
        bot_name="VexaBot", redis_url="redis://redis:6379/0",
        meeting_api_url="http://meeting-api:8080", token_secret=SECRET, **kw,
    )


def _invocation(runtime) -> dict:
    return json.loads(runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])


# ── spawn: join_at rides the invocation and the row ─────────────────────────────────────────────

async def test_join_at_rides_the_invocation_and_the_row():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    at = datetime.now(timezone.utc) + timedelta(seconds=480)
    meeting = await _spawn(repo, runtime, join_at=at)
    inv = _invocation(runtime)
    conforms_invocation(inv)
    expected = at.isoformat().replace("+00:00", "Z")
    assert inv["joinAt"] == expected
    assert meeting["status"] == "requested", "a waiting bot's row stays requested"
    assert repo._meetings[meeting["id"]]["data"]["join_at"] == expected


async def test_no_join_at_means_no_field():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    meeting = await _spawn(repo, runtime)
    assert "joinAt" not in _invocation(runtime)
    assert "join_at" not in repo._meetings[meeting["id"]]["data"]


async def test_a_past_join_at_is_kept_and_the_bot_joins_at_once():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    at = datetime.now(timezone.utc) - timedelta(seconds=30)
    await _spawn(repo, runtime, join_at=at)
    assert _invocation(runtime)["joinAt"] == at.isoformat().replace("+00:00", "Z")


async def test_a_far_join_at_is_refused_before_any_row_or_workload():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    at = datetime.now(timezone.utc) + timedelta(seconds=600 + 60 + 30)
    with pytest.raises(JoinAtRefused, match="too far ahead"):
        await _spawn(repo, runtime, join_at=at)
    assert repo._meetings == {} and runtime.specs == [], "refused before any row or workload"


async def test_the_bound_follows_booked_pod_lead_s(monkeypatch):
    monkeypatch.setenv("BOOKED_POD_LEAD_S", "120")
    assert booked_pod_lead_s() == 120
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    with pytest.raises(JoinAtRefused):
        await _spawn(repo, runtime, join_at=datetime.now(timezone.utc) + timedelta(seconds=240))
    await _spawn(repo, runtime, join_at=datetime.now(timezone.utc) + timedelta(seconds=170))
    assert "joinAt" in _invocation(runtime)


@pytest.mark.parametrize("raw", ["", "soon", "-5"])
def test_unusable_lead_values_fall_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("BOOKED_POD_LEAD_S", raw)
    assert booked_pod_lead_s() == 600


def test_the_bound_is_lead_plus_sixty_seconds():
    now = datetime(2026, 11, 3, 9, 48, tzinfo=timezone.utc)
    assert _checked_join_at(now + timedelta(seconds=660), no_page=False, now=now) == "2026-11-03T09:59:00Z"
    with pytest.raises(JoinAtRefused):
        _checked_join_at(now + timedelta(seconds=661), no_page=False, now=now)


def test_a_naive_join_at_is_refused():
    with pytest.raises(JoinAtRefused, match="time zone"):
        _checked_join_at(datetime(2026, 11, 3, 9, 58), no_page=False)


def test_a_bot_with_no_page_never_waits():
    with pytest.raises(JoinAtRefused, match="no meeting"):
        _checked_join_at(datetime.now(timezone.utc), no_page=True)


def test_a_non_utc_join_at_is_normalised_to_utc():
    now = datetime(2026, 11, 3, 9, 48, tzinfo=timezone.utc)
    cet = timezone(timedelta(hours=1))
    at = datetime(2026, 11, 3, 10, 55, tzinfo=cet)
    assert _checked_join_at(at, no_page=False, now=now) == "2026-11-03T09:55:00Z"


async def test_a_continued_run_without_join_at_drops_the_prior_one():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    first = await _spawn(repo, runtime, join_at=datetime.now(timezone.utc) + timedelta(seconds=60))
    repo.set_status(first["id"], "completed")
    second = await _spawn(repo, runtime, continue_meeting=True)
    assert second["id"] == first["id"]
    assert "join_at" not in repo._meetings[first["id"]]["data"]
    assert "joinAt" not in _invocation(runtime)


# ── POST /bots: a refused join time is a 422 with its reason, never a 500 ───────────────────────

@pytest.mark.parametrize("join_at,reason", [
    (lambda: datetime.now(timezone.utc) + timedelta(hours=1), "join_at is too far ahead"),
    (lambda: datetime.now() + timedelta(seconds=60), "join_at must carry a time zone"),
])
def test_post_bots_answers_a_refused_join_at_with_422(monkeypatch, join_at, reason):
    # POST /bots takes no join_at of its own (the planned-row start route sets it), so the router's
    # request_bot is wrapped to pass one: the refusal travels the real spawn path to the HTTP edge.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import meeting_api.bot_spawn.router as router_mod
    from meeting_api.bot_spawn import build_router

    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    real = router_mod.request_bot

    async def with_join_at(*args, **kw):
        return await real(*args, join_at=join_at(), **kw)

    monkeypatch.setattr(router_mod, "request_bot", with_join_at)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    app = FastAPI()
    app.include_router(build_router(repo, runtime))
    r = TestClient(app, raise_server_exceptions=False).post(
        "/bots", headers={"x-user-id": str(USER)},
        json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"},
    )
    assert r.status_code == 422, r.text
    assert reason in r.json()["detail"]
    assert repo._meetings == {} and runtime.specs == [], "refused before any row or workload"


# ── lifecycle seam: a waiting bot's terminal is the row's FIRST event ───────────────────────────
# The two events below are what the bot emits, captured from the orchestrator (createOrchestrator
# with connectionId sess-uid, container mtg-abc123-bot, nodeName lke-pool-b7, a fixed clock): a stop
# while it waits for joinAt, and the refusal of a joinAt Date.parse cannot read. Neither follows a
# `joining`, because the bot never opened the meeting.

_STOPPED_WHILE_WAITING = {
    "connection_id": "sess-uid", "container_id": "mtg-abc123-bot", "status": "failed",
    "timestamp": "2026-11-03T09:50:00.000Z", "failure_stage": "requested", "completion_reason": "stopped",
    "reason": "stopped while waiting for joinAt 2026-11-03T09:58:00Z (the meeting was never opened)",
    "exit_code": 0,
    "join_evidence": {
        "reason": "stopped_while_joining", "attribution": "user_action", "source": "bot",
        "stage": "requested",
        "detail": "stopped while waiting for joinAt 2026-11-03T09:58:00Z (the meeting was never opened)",
        "node": "lke-pool-b7",
    },
}
_UNREADABLE_JOIN_AT = {
    "connection_id": "sess-uid", "container_id": "mtg-abc123-bot", "status": "failed",
    "timestamp": "2026-11-03T09:50:00.000Z", "failure_stage": "requested",
    "completion_reason": "validation_error",
    "reason": "invocation.v1: joinAt '2016-12-31T23:59:60Z' is not a readable date-time", "exit_code": 1,
}
LIFECYCLE_CALLBACK = "/bots/internal/callback/lifecycle"


def _waiting_row(repo):
    """A booked bot's row while its pod waits: `requested`, with its join time, no event yet."""
    import asyncio

    m = asyncio.run(repo.create_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
        data={"join_at": "2026-11-03T09:58:00Z"},
    ))
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid="sess-uid"))
    return m


@pytest.mark.parametrize("event", [_STOPPED_WHILE_WAITING, _UNREADABLE_JOIN_AT],
                         ids=["stopped_while_waiting", "unreadable_join_at"])
def test_a_waiting_bots_terminal_is_accepted_as_the_rows_first_event(event):
    from fastapi.testclient import TestClient

    from meeting_api import create_app

    repo = InMemoryMeetingRepo()
    m = _waiting_row(repo)
    client = TestClient(create_app(open_callbacks=True, meeting_repo=repo))
    r = client.post(LIFECYCLE_CALLBACK, json=event)
    assert r.status_code == 200, r.text
    row = repo._meetings[m["id"]]
    assert row["status"] == "failed", "the row closes on the bot's own terminal"
    data = row["data"]
    assert data["failure_stage"] == "requested"
    assert data["completion_reason"] == event["completion_reason"]
    assert data["reason"] == event["reason"], "the bot's reason is kept"
    assert data["join_evidence"]["stage"] == "requested"
    assert data["status_transition"][-1]["from"] is None and data["status_transition"][-1]["to"] == "failed"
    if "join_evidence" in event:
        assert data["join_evidence"]["node"] == "lke-pool-b7", "the attempt's node is kept"
        assert data["join_evidence"]["reason"] == "stopped_while_joining"
        assert data["join_evidence"]["attribution"] == "user_action"


@pytest.mark.parametrize("first", [
    {"status": "failed", "completion_reason": "join_failure", "exit_code": 1},
    {"status": "failed", "failure_stage": "joining", "completion_reason": "join_failure", "exit_code": 1},
    {"status": "active"},
], ids=["failed_without_stage", "failed_at_joining", "active"])
def test_every_other_first_event_is_still_refused(first):
    from fastapi.testclient import TestClient

    from meeting_api import create_app

    repo = InMemoryMeetingRepo()
    m = _waiting_row(repo)
    client = TestClient(create_app(open_callbacks=True, meeting_repo=repo))
    r = client.post(LIFECYCLE_CALLBACK, json={"connection_id": "sess-uid", **first})
    assert r.status_code == 409, r.text
    assert repo._meetings[m["id"]]["status"] == "requested"


# ── reconciler: a waiting `requested` row is measured from its join time ─────────────────────────

def _age(repo, meeting_id, seconds):
    repo._meetings[meeting_id]["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).isoformat()


async def _stale_ids(repo, preactive_grace=960.0):
    rows = await repo.list_stale_nonterminal(
        stop_grace=45.0, active_grace=300.0, preactive_grace=preactive_grace,
    )
    return [r[0] for r in rows]


async def test_a_waiting_requested_row_is_not_listed_before_its_join_time_plus_grace():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    meeting = await _spawn(repo, runtime, join_at=datetime.now(timezone.utc) + timedelta(seconds=600))
    # The row has been quiet for longer than the pre-active grace, but its bot is waiting by design.
    _age(repo, meeting["id"], 2000)
    assert meeting["id"] not in await _stale_ids(repo, preactive_grace=300.0)


async def test_the_same_row_is_listed_once_join_time_plus_grace_has_passed():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    meeting = await _spawn(repo, runtime, join_at=datetime.now(timezone.utc) + timedelta(seconds=30))
    repo._meetings[meeting["id"]]["data"]["join_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=400)
    ).isoformat().replace("+00:00", "Z")
    _age(repo, meeting["id"], 2000)
    assert meeting["id"] in await _stale_ids(repo, preactive_grace=300.0)


async def test_a_row_without_join_at_keeps_todays_window():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    meeting = await _spawn(repo, runtime)
    _age(repo, meeting["id"], 400)
    assert meeting["id"] in await _stale_ids(repo, preactive_grace=300.0)


def test_reference_time_rules():
    upd = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
    later = {"join_at": "2026-11-03T09:58:00Z"}
    assert reconcile_reference_time("requested", upd, later) == datetime(2026, 11, 3, 9, 58, tzinfo=timezone.utc)
    assert reconcile_reference_time("joining", upd, later) == datetime(2026, 11, 3, 9, 58, tzinfo=timezone.utc)
    # A live or stopping row is never measured from a join time.
    assert reconcile_reference_time("active", upd, later) == upd
    assert reconcile_reference_time("stopping", upd, later) == upd
    # An earlier join time, a garbled one, or none leaves updated_at.
    assert reconcile_reference_time("requested", upd, {"join_at": "2026-11-03T08:00:00Z"}) == upd
    assert reconcile_reference_time("requested", upd, {"join_at": "not a time"}) == upd
    assert reconcile_reference_time("requested", upd, {}) == upd
    assert reconcile_reference_time("requested", upd, None) == upd
