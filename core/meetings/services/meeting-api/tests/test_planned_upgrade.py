"""bot-spawn CLAIM path — sending a bot to a PLANNED meeting upgrades the SAME row.

A planned row (intent status `idle`/`scheduled`, created by POST /meetings or calendar sync) for
the same (user, platform, native) would otherwise collide with the unique partial index at spawn
time (it covers ALL non-terminal statuses). ``create_meeting_guarded`` therefore CLAIMS the planned
row inside the guarded transaction: status flips to `requested`, spawn keys merge OVER the planned
data, and the plan's `title` / `scheduled_at` / `workspace_id` / `auto_join` / `calendar_uid`
survive — plan, workspace bind, and transcript live on ONE row.

Drives the SHIPPED ``request_bot`` over the in-memory fakes, OFFLINE.
"""
from __future__ import annotations

import pytest

from meeting_api.bot_spawn import request_bot
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

SECRET = "test-admin-token"
USER = 7
PLAT, NID = "google_meet", "abc-defg-hij"
AT = "2026-07-10T15:00:00Z"


def _seed_planned(repo, *, status="scheduled", user_id=USER, mid=101):
    repo._meetings[mid] = {
        "id": mid, "user_id": user_id, "platform": PLAT,
        "native_meeting_id": NID, "platform_specific_id": NID,
        "status": status, "bot_container_id": None,
        "start_time": None, "end_time": None,
        "data": {"title": "Q3 kickoff", "scheduled_at": AT, "workspace_id": "ws-1",
                 "auto_join": True, "calendar_uid": "uid-1"},
        "created_at": "2026-07-08T09:00:00Z", "updated_at": "2026-07-08T09:00:00Z",
    }
    return mid


_KW = dict(user_id=USER, platform=PLAT, native_meeting_id=NID,
           redis_url="redis://r", token_secret=SECRET)


async def test_spawn_claims_planned_row_same_id():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed_planned(repo, status="scheduled")
    meeting = await request_bot(repo, runtime, **_KW)
    assert meeting["id"] == mid                 # SAME row — not a second insert
    assert meeting["status"] == "requested"
    assert len(repo._meetings) == 1


async def test_claim_preserves_planned_keys_and_merges_spawn_keys():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed_planned(repo)
    await request_bot(repo, runtime, **_KW)
    data = repo._meetings[mid]["data"]
    # planned keys survive
    assert data["title"] == "Q3 kickoff"
    assert data["scheduled_at"] == AT
    assert data["workspace_id"] == "ws-1"
    assert data["calendar_uid"] == "uid-1"
    # spawn keys merged over
    assert data["constructed_meeting_url"] == f"https://meet.google.com/{NID}"


async def test_claim_works_from_idle_too():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mid = _seed_planned(repo, status="idle")
    meeting = await request_bot(repo, runtime, **_KW)
    assert meeting["id"] == mid and meeting["status"] == "requested"


async def test_claimed_row_counts_toward_cap_next_spawn():
    """After a claim the row is ACTIVE — the next spawn for another meeting hits the cap."""
    from meeting_api.bot_spawn import MaxBotsExceeded

    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed_planned(repo)
    await request_bot(repo, runtime, max_concurrent=1, **_KW)
    with pytest.raises(MaxBotsExceeded):
        await request_bot(repo, runtime, user_id=USER, platform=PLAT,
                          native_meeting_id="zzz-zzzz-zzz", redis_url="redis://r",
                          token_secret=SECRET, max_concurrent=1)


async def test_planned_row_does_not_precount_toward_cap():
    """An unclaimed planned row is NOT an active bot — it must not consume cap headroom."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed_planned(repo)  # scheduled, never claimed
    meeting = await request_bot(repo, runtime, user_id=USER, platform=PLAT,
                                native_meeting_id="zzz-zzzz-zzz", redis_url="redis://r",
                                token_secret=SECRET, max_concurrent=1)
    assert meeting["status"] == "requested"


async def test_cap_still_enforced_on_claim():
    """Claiming a planned row is a spawn — the cap applies to it like any other."""
    from meeting_api.bot_spawn import MaxBotsExceeded

    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed_planned(repo)
    # another meeting already running consumes the whole cap
    await request_bot(repo, runtime, user_id=USER, platform=PLAT,
                      native_meeting_id="yyy-yyyy-yyy", redis_url="redis://r",
                      token_secret=SECRET, max_concurrent=1)
    with pytest.raises(MaxBotsExceeded):
        await request_bot(repo, runtime, max_concurrent=1, **_KW)


async def test_other_users_planned_row_not_claimed():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed_planned(repo, user_id=999)  # someone else's plan on the same link
    meeting = await request_bot(repo, runtime, **_KW)
    assert meeting["id"] != 101       # fresh row for THIS user
    assert repo._meetings[101]["status"] == "scheduled"  # the other plan untouched


async def test_double_spawn_after_claim_still_dedups():
    from meeting_api.bot_spawn import DuplicateMeeting

    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    _seed_planned(repo)
    await request_bot(repo, runtime, **_KW)
    with pytest.raises(DuplicateMeeting):
        await request_bot(repo, runtime, **_KW)
