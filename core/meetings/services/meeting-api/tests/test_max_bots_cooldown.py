"""The seat cooldown (founder, 4 Nov 2026: abuse protections for ad-hoc bots).

A user-stopped ad-hoc bot keeps its seat in the account's concurrent-bot cap for 60 seconds after
it ends, so a start-stop-start loop cannot cycle bots through one seat. No new column: the existing
``end_time`` and ``data.stop_requested``. A booked bot's end holds no seat, and a booked start (the
auto-join sweep) is not held back by the cooldown."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.bot_spawn import MaxBotsExceeded, build_router, request_bot
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

SECRET = "test-admin-token"
USER = 7
KW = dict(redis_url="r", token_secret=SECRET, meeting_api_url="http://meeting-api:8080")


def _ended(repo, meeting_id, *, ago_s, stopped=True, booked=False):
    row = repo._meetings[meeting_id]
    row["status"] = "completed"
    row["end_time"] = (datetime.now(timezone.utc) - timedelta(seconds=ago_s)).isoformat()
    if stopped:
        row["data"]["stop_requested"] = True
    if booked:
        row["data"]["scheduled_at"] = "2026-11-04T10:00:00Z"


def _client(repo, runtime):
    app = FastAPI()
    app.include_router(build_router(repo, runtime))
    return TestClient(app)


def test_a_bot_stopped_seconds_ago_still_holds_its_seat(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    headers = {"x-user-id": str(USER), "x-user-limits": "1"}
    r1 = client.post("/bots", headers=headers, json={"platform": "google_meet", "native_meeting_id": "m-1"})
    assert r1.status_code == 201, r1.text
    _ended(repo, r1.json()["id"], ago_s=10)

    r2 = client.post("/bots", headers=headers, json={"platform": "google_meet", "native_meeting_id": "m-2"})

    assert r2.status_code == 429, r2.text


def test_the_seat_frees_after_the_cooldown(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    headers = {"x-user-id": str(USER), "x-user-limits": "1"}
    r1 = client.post("/bots", headers=headers, json={"platform": "google_meet", "native_meeting_id": "m-1"})
    _ended(repo, r1.json()["id"], ago_s=61)
    r2 = client.post("/bots", headers=headers, json={"platform": "google_meet", "native_meeting_id": "m-2"})
    assert r2.status_code == 201, r2.text


@pytest.mark.parametrize("stopped,booked,why", [
    (False, False, "a bot that ended by itself (left alone, failed) holds no seat"),
    (True, True, "a booked bot's end holds no seat"),
])
def test_only_a_user_stopped_ad_hoc_bot_holds_a_seat(monkeypatch, stopped, booked, why):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    client = _client(repo, runtime)
    headers = {"x-user-id": str(USER), "x-user-limits": "1"}
    r1 = client.post("/bots", headers=headers, json={"platform": "google_meet", "native_meeting_id": "m-1"})
    _ended(repo, r1.json()["id"], ago_s=5, stopped=stopped, booked=booked)
    r2 = client.post("/bots", headers=headers, json={"platform": "google_meet", "native_meeting_id": "m-2"})
    assert r2.status_code == 201, why


async def test_a_booked_start_is_not_held_back_by_the_cooldown():
    """The auto-join sweep calls request_bot without a cooldown (auto_join.py)."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    m1 = await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                           native_meeting_id="m-1", max_concurrent=1, **KW)
    _ended(repo, m1["id"], ago_s=5)
    booked = await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                               native_meeting_id="m-2", max_concurrent=1, **KW)
    assert booked["status"] == "requested"
    with pytest.raises(MaxBotsExceeded):
        await request_bot(repo, runtime, user_id=USER, platform="google_meet",
                          native_meeting_id="m-3", max_concurrent=2, seat_cooldown_s=60, **KW)


async def test_billing_concurrency_counts_running_bots_only():
    """The service-authority sweep's `active_concurrency` reads count_active_bots without a cooldown."""
    repo = InMemoryMeetingRepo()
    m = await repo.create_meeting(user_id=USER, platform="google_meet", native_meeting_id="m-1", data={})
    _ended(repo, m["id"], ago_s=5)
    assert await repo.count_active_bots(user_id=USER) == 0
    assert await repo.count_active_bots(user_id=USER, cooldown_s=60) == 1
