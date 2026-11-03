"""Start every scheduled row of an in-memory repo through the planned-row start route, as Flows'
``booked_bot`` does at ``pod_at`` (ADR-0052 §6) — what the removed auto-join sweep's tick did, for
the tests whose subject is a SAFEGUARD of ``planned_start.start_row`` (block lists, the duplicate-
room check, the attempt stamp, recording and transcription, the meeting host, the user's stop, the
language), not the clock. The clock is Flows' (``core/flows/tests/test_booked_bot.py``); the route's
own window and answers are ``test_planned_start.py``.

The counters keep the sweep's names so each safeguard's assertion reads as it did: ``due`` counts the
rows the route acted on (it did not answer ``not_scheduled``, ``not_found``, ``too_early``,
``past_deadline`` or ``backoff``, the route's spellings of "not due"), the rest count outcomes by kind.
"""
from __future__ import annotations

from meeting_api.bot_spawn.planned_start import start_planned_meeting

KINDS = ("spawned", "already", "errors", "skipped_uncapped", "skipped_live", "stopped", "blocked")
NOT_DUE = frozenset({"not_scheduled", "not_found", "too_early", "past_deadline", "backoff"})


async def start_scheduled(repo, runtime, *, now, **kw) -> dict:
    """One pass over the repo's ``scheduled`` rows, each through ``start_planned_meeting``.
    Accepts the sweep's keyword arguments; ``lead_s`` / ``grace_s`` are gone with it (the window is
    BOOKED_* now) and ``list_scheduled_meetings`` on ``repo`` picks the rows when it exists."""
    kw.pop("lead_s", None)
    kw.pop("grace_s", None)
    if hasattr(repo, "list_scheduled_meetings"):
        rows = await repo.list_scheduled_meetings()
    else:
        rows = [r for r in repo._meetings.values() if r.get("status") == "scheduled"]
    counters = {"due": 0, **{k: 0 for k in KINDS}}
    kinds: list = []
    for row in [dict(r) for r in rows]:
        out = await start_planned_meeting(row["id"], user_id=row["user_id"], repo=repo,
                                          runtime=runtime, now=now, **kw)
        kinds.append(out.kind)
        if out.kind not in NOT_DUE:
            counters["due"] += 1
        if out.kind in counters:
            counters[out.kind] += 1
    start_scheduled.last_kinds = kinds
    return counters
