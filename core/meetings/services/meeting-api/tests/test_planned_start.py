"""ADR-0052 s2c — the start route: Flows starts the bot for ONE planned row, with the sweep's safeguards.

``POST /internal/meetings/{id}/start`` (``bot_spawn.start_router``) over ``planned_start``: refused
409 ``not_scheduled`` unless the row is still ``scheduled`` with auto-join on (a cancel that lands
first always wins, before or during the start), claims THAT row id (never the room's newest planned
row), carries the block lists, the calendar bot name, the last-attempt stamp, the duplicate-room
check and the error stamps, and hands the bot ``join_at`` = start − ``BOOKED_JOIN_LEAD_S``.
Driven over the in-memory fakes, offline.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.planned_start import start_planned_meeting
from meeting_api.bot_spawn.start_router import START_PATH, StartDeps

USER = 7
PLAT, NID = "google_meet", "abc-defg-hij"
# The spawn bounds join_at against the real clock (s1), so the meeting is ~10 minutes from now.
START = (datetime.now(timezone.utc) + timedelta(seconds=600)).replace(microsecond=0)
POD_AT = START - timedelta(seconds=600)


def _z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")
_NO_GATE = lambda: None  # noqa: E731 — tests bypass the STT capability gate


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key in ("BOOKED_POD_LEAD_S", "BOOKED_JOIN_LEAD_S", "BOOKED_GRACE_S"):
        monkeypatch.delenv(key, raising=False)


def _seed(repo, *, mid=1, status="scheduled", at=START, native=NID, user_id=USER, extra=None):
    data = {"title": "standup", "auto_join": True, "scheduled_at": at.isoformat()}
    data.update(extra or {})
    repo._meetings[mid] = {
        "id": mid, "user_id": user_id, "platform": PLAT,
        "native_meeting_id": native, "platform_specific_id": native,
        "status": status, "bot_container_id": None, "start_time": None, "end_time": None,
        "data": data, "created_at": "2026-11-01T09:00:00Z", "updated_at": "2026-11-01T09:00:00Z",
    }
    repo._next_id = max(repo._next_id, mid + 1)
    return mid


async def _ctx(_uid):
    return {"max_concurrent": 3, "bot_name": "Vexa Notetaker"}


async def _start(repo, runtime, mid=1, *, now=POD_AT, user_id=USER, **kw):
    kw.setdefault("transcribe_gate", _NO_GATE)
    kw.setdefault("token_secret", "s")
    kw.setdefault("redis_url", "redis://r")
    kw.setdefault("fetch_bot_context", _ctx)
    return await start_planned_meeting(mid, user_id=user_id, repo=repo, runtime=runtime,
                                       now=now, **kw)


def _invocation(runtime) -> dict:
    return json.loads(runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])


# ── the start: this row, with its join time ─────────────────────────────────────────────────────

async def test_start_claims_this_row_and_the_bot_waits_for_join_at():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo)
    out = await _start(repo, runtime)
    assert (out.kind, out.http) == ("spawned", 201)
    row = repo._meetings[mid]
    assert row["status"] == "requested" and row["data"]["title"] == "standup"
    assert _invocation(runtime)["joinAt"] == _z(START - timedelta(seconds=120))
    assert row["data"]["join_at"] == _z(START - timedelta(seconds=120))
    assert row["data"]["auto_join_last_attempt"] == POD_AT.isoformat()


async def test_join_lead_follows_booked_join_lead_s(monkeypatch):
    monkeypatch.setenv("BOOKED_JOIN_LEAD_S", "60")
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo)
    await _start(repo, runtime)
    assert _invocation(runtime)["joinAt"] == _z(START - timedelta(seconds=60))


async def test_booked_late_joins_at_once():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    start = START - timedelta(seconds=630)                     # it started 30 s ago
    _seed(repo, at=start)
    out = await _start(repo, runtime, now=POD_AT)
    assert out.kind == "spawned"
    assert _invocation(runtime)["joinAt"] == _z(start - timedelta(seconds=120))  # past: at once


async def test_recurring_link_starts_the_named_row_not_the_newest():
    """Two planned rows of one recurring link: the start claims the row it names (the sweep's
    claim picked the NEWEST row of the room, the wrong occurrence)."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    first = _seed(repo, mid=1)
    later = _seed(repo, mid=2, at=START + timedelta(days=7))
    out = await _start(repo, runtime, first)
    assert out.kind == "spawned"
    assert repo._meetings[first]["status"] == "requested"
    assert repo._meetings[later]["status"] == "scheduled", "the next occurrence is untouched"


# ── a cancel always wins ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("change", [
    {"status": "completed"},                      # DELETE /meetings terminalized it
    {"data": {"auto_join": False}},               # the toggle went off
    {"data": {"stop_requested": True}},           # a legacy stop flag
    {"status": "idle"},                           # PATCH cleared the time
])
async def test_cancel_before_fire_answers_not_scheduled_and_no_bot(change):
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo)
    if "status" in change:
        repo._meetings[mid]["status"] = change["status"]
    repo._meetings[mid]["data"].update(change.get("data", {}))
    out = await _start(repo, runtime)
    assert (out.code, out.http) == ("not_scheduled", 409)
    assert runtime.specs == []
    assert "auto_join_last_attempt" not in repo._meetings[mid]["data"]


class _CancelDuringStart(InMemoryMeetingRepo):
    """The owner cancels between the route's read and the spawn's row lock: the last-attempt stamp
    is the last write before the spawn, so the cancel lands right after it."""

    async def merge_meeting_data(self, meeting_id, patch):
        await super().merge_meeting_data(meeting_id, patch)
        if "auto_join_last_attempt" in patch and patch["auto_join_last_attempt"]:
            self._meetings[meeting_id]["status"] = "completed"


async def test_cancel_during_fire_answers_not_scheduled_and_no_bot():
    repo, runtime = _CancelDuringStart(), FakeRuntimeClient()
    mid = _seed(repo)
    out = await _start(repo, runtime)
    assert (out.code, out.http) == ("not_scheduled", 409)
    assert runtime.specs == [], "the spawn re-checks the row under its lock"
    assert repo._meetings[mid]["status"] == "completed"
    assert "auto_join_last_attempt" not in repo._meetings[mid]["data"], "no pod: no hold earned"


async def test_a_deleted_booking_answers_not_scheduled_and_no_bot():
    """ADR-0052 §5/§6: a cancel by DELETE is "nothing to do" (409), like every other cancel."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo)
    assert repo.delete_planned(mid)
    out = await _start(repo, runtime, mid)
    assert (out.code, out.http) == ("not_scheduled", 409) and runtime.specs == []
    other = await _start(repo, runtime, mid, user_id=USER + 1)
    assert (other.code, other.http) == ("not_found", 404), "never tells one person of another's row"
    never = await _start(repo, runtime, 999)
    assert (never.code, never.http) == ("not_found", 404)


async def test_another_persons_row_is_not_found():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, user_id=99)
    out = await _start(repo, runtime)
    assert (out.code, out.http) == ("not_found", 404) and runtime.specs == []


# ── the window ──────────────────────────────────────────────────────────────────────────────────

async def test_too_early_is_refused_with_retry_after_and_no_stamp():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo)
    out = await _start(repo, runtime, now=POD_AT - timedelta(seconds=300))
    assert (out.code, out.http) == ("too_early", 409)
    # join_at may be at most BOOKED_POD_LEAD_S + 60 s ahead (s1): the pod may start from
    # join_at − 660 s = start − 780 s = pod_at − 180 s, i.e. 120 s after this call.
    assert out.retry_after == 120
    assert runtime.specs == [] and "auto_join_last_attempt" not in repo._meetings[mid]["data"]


async def test_past_the_deadline_no_bot():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo)
    out = await _start(repo, runtime, now=START + timedelta(seconds=601))
    assert (out.code, out.http) == ("past_deadline", 409) and runtime.specs == []


# ── the sweep's safeguards, on the route ────────────────────────────────────────────────────────

async def test_operator_block_list_refuses_and_stamps(monkeypatch):
    from meeting_api.bot_spawn.auto_join_block import parse_block

    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo, extra={"constructed_meeting_url": f"https://meet.google.com/{NID}"})
    block = parse_block(["meet.google.com"], what="VEXA_AUTO_JOIN_BLOCK")
    out = await _start(repo, runtime, deployment_block=block)
    assert (out.code, out.http) == ("blocked", 403) and runtime.specs == []
    assert repo._meetings[mid]["data"]["auto_join_error"]


async def test_owner_block_list_refuses():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo, extra={"constructed_meeting_url": f"https://meet.google.com/{NID}"})

    async def ctx(_uid):
        return {"max_concurrent": 3, "auto_join_block": ["meet.google.com"]}

    out = await _start(repo, runtime, fetch_bot_context=ctx)
    assert out.code == "blocked" and runtime.specs == []
    assert repo._meetings[mid]["data"]["auto_join_error"]


async def test_calendar_source_bot_name_wins():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, extra={"calendar_sources": [{"id": "work", "auto_join": True,
                                             "bot_name": "Work Notes"}]})
    await _start(repo, runtime)
    assert _invocation(runtime)["botName"] == "Work Notes"


async def test_owner_bot_name_when_no_calendar_name():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo)
    await _start(repo, runtime)
    assert _invocation(runtime)["botName"] == "Vexa Notetaker"


async def test_last_attempt_stamp_holds_the_occurrence():
    """A sibling row calendar sync made for an occurrence whose bot was just started carries the
    stamp: the route answers 429 with Retry-After, never a second bot."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo, extra={"auto_join_last_attempt": (POD_AT - timedelta(seconds=100)).isoformat()})
    out = await _start(repo, runtime)
    assert (out.code, out.http) == ("backoff", 429)
    assert out.retry_after == 200 and runtime.specs == []


async def test_a_bot_already_in_the_room_on_another_row_is_refused():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo, mid=1)
    _seed(repo, mid=5, status="active", native=NID)
    out = await _start(repo, runtime, mid)
    assert (out.code, out.http) == ("already_live", 409) and out.extra == {"holder": 5}
    assert "already in this meeting (meeting 5)" in repo._meetings[mid]["data"]["auto_join_error"]
    assert runtime.specs == []


async def test_full_ceiling_is_429_and_retries_soon_without_a_hold(monkeypatch):
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo, mid=1)
    _seed(repo, mid=2, status="needs_help", native="zzz-zzzz-zzz")   # a slot the ceiling counts

    async def ctx(_uid):
        return {"max_concurrent": 1}

    out = await _start(repo, runtime, fetch_bot_context=ctx)
    assert (out.code, out.http, out.retry_after) == ("max_bots", 429, 15)
    data = repo._meetings[mid]["data"]
    assert data["auto_join_error"] and "auto_join_last_attempt" not in data
    # Flows calls again after Retry-After; a freed slot starts the bot.
    repo._meetings[2]["status"] = "completed"
    out = await _start(repo, runtime, fetch_bot_context=ctx, now=POD_AT + timedelta(seconds=16))
    assert out.kind == "spawned"
    assert "auto_join_error" not in repo._meetings[mid]["data"], "a resolved failure is cleared"


async def test_unreachable_identity_defers_with_its_reason_and_no_hold():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo)

    async def ctx(_uid):
        return None

    out = await _start(repo, runtime, fetch_bot_context=ctx)
    assert (out.code, out.http) == ("identity_unavailable", 503) and out.retry_after
    data = repo._meetings[mid]["data"]
    assert data.get("auto_join_last_attempt") is None and data.get("auto_join_next_retry") is None
    assert data["auto_join_error"] == "the account's spawn context could not be read"


async def test_no_identity_edge_refuses_uncapped_unless_opted_in():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed(repo)
    out = await _start(repo, runtime, fetch_bot_context=None)
    assert (out.code, out.http) == ("cap_unresolvable", 503) and runtime.specs == []
    out = await _start(repo, runtime, fetch_bot_context=None, allow_uncapped=True)
    assert out.kind == "spawned"


# ── over HTTP ───────────────────────────────────────────────────────────────────────────────────

def _client(repo, runtime, **deps):
    from meeting_api import create_app

    deps.setdefault("fetch_bot_context", _ctx)
    deps.setdefault("transcribe_gate", _NO_GATE)
    deps.setdefault("token_secret", "s")
    deps.setdefault("redis_url", "redis://r")
    app = create_app(meeting_repo=repo, runtime=runtime, planned_start=StartDeps(**deps))
    return TestClient(app)


def test_route_starts_the_bot_and_answers_its_join_time():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    soon = datetime.now(timezone.utc) + timedelta(seconds=500)
    mid = _seed(repo, at=soon)
    r = _client(repo, runtime).post(START_PATH.format(meeting_id=mid), headers={"X-User-Id": "7"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["code"] == "started" and body["meeting"] == {"id": mid, "status": "requested"}
    assert body["join_at"] == (soon - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")


def test_route_cancelled_row_is_409_not_scheduled():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo, status="completed")
    r = _client(repo, runtime).post(START_PATH.format(meeting_id=mid), headers={"X-User-Id": "7"})
    assert r.status_code == 409 and r.json()["code"] == "not_scheduled"


def test_route_full_ceiling_is_429_with_retry_after():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed(repo, at=datetime.now(timezone.utc) + timedelta(seconds=500))
    _seed(repo, mid=2, status="active", native="zzz-zzzz-zzz")

    async def ctx(_uid):
        return {"max_concurrent": 1}

    r = _client(repo, runtime, fetch_bot_context=ctx).post(
        START_PATH.format(meeting_id=mid), headers={"X-User-Id": "7"})
    assert r.status_code == 429 and r.headers["Retry-After"] == "15"
    assert r.json()["code"] == "max_bots"


def test_route_needs_a_subject():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    r = _client(repo, runtime).post(START_PATH.format(meeting_id=1))
    assert r.status_code == 401


def test_route_is_on_the_door_scope_table_not_a_public_row():
    from meeting_api import route_scopes

    key = ("POST", START_PATH)
    assert key in route_scopes.DOOR_ROUTES and key not in route_scopes.ROUTE_SCOPES
