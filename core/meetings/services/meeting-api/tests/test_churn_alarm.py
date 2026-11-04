"""The start-and-stop alarm (churn_alarm.py): an account whose bots are mostly stopped within 30 s
of starting is reported to ops once an hour, from existing rows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.churn_alarm import churn_alarm

NOW = datetime.now(timezone.utc)


async def _run(repo, user_id, n, *, quick, stopped=True, started_ago_s=600):
    for i in range(n):
        m = await repo.create_meeting(user_id=user_id, platform="google_meet",
                                      native_meeting_id=f"u{user_id}-{i}", data={})
        await repo.create_session(meeting_id=m["id"], session_uid=f"s-{user_id}-{i}")
        start = NOW - timedelta(seconds=started_ago_s)
        repo.sessions[-1]["started_at"] = start.isoformat()
        row = repo._meetings[m["id"]]
        row["status"] = "completed"
        row["end_time"] = (start + timedelta(seconds=10 if i < quick else 1800)).isoformat()
        if stopped:
            row["data"]["stop_requested"] = True


async def test_an_account_that_stops_most_bots_at_once_is_reported():
    repo, events = InMemoryMeetingRepo(), []
    await _run(repo, 7, 5, quick=4)          # 4 of 5 stopped within 30 s
    await _run(repo, 8, 10, quick=3)         # 3 of 10: normal use
    await _run(repo, 9, 3, quick=3)          # too few runs to judge
    await _run(repo, 10, 5, quick=5, stopped=False)   # ended by itself, not a stop

    flagged = await churn_alarm(repo, log_event=lambda *a, **k: events.append((a, k)))

    assert [(c["user_id"], c["runs"], c["quick_stops"]) for c in flagged] == [(7, 5, 4)]
    ((args, kw),) = events
    assert args == ("bot_churn_suspected",)
    assert kw["audience"] == "system" and kw["level"] == "error" and kw["user_id"] == 7


async def test_runs_older_than_the_window_are_not_counted():
    repo = InMemoryMeetingRepo()
    await _run(repo, 7, 5, quick=5, started_ago_s=2 * 86_400)
    assert await churn_alarm(repo, log_event=lambda *a, **k: None) == []
